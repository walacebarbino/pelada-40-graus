import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import uuid

from storage import (
    DB_PATH,
    SCHEMA_OBJECTS_V2,
    SCHEMA_OBJECTS_V3,
    SCHEMA_VERSION_V2,
    SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    schema_is_current,
    schema_is_version_1,
    schema_is_version_2,
    schema_matches,
)


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class MigrationResult:
    database_path: Path
    backup_path: Path | None
    already_current: bool


def _snapshot(connection: sqlite3.Connection) -> dict[str, tuple[tuple[object, ...], ...]]:
    snapshot = {
        table: tuple(
            tuple(row)
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY id")
        )
        for table in ("matches", "bookings", "votes")
    }
    snapshot["sqlite_sequence"] = tuple(
        tuple(row)
        for row in connection.execute(
            "SELECT name, seq FROM sqlite_sequence ORDER BY name"
        )
    )
    return snapshot


def _validate_integrity(connection: sqlite3.Connection) -> None:
    integrity = [row[0] for row in connection.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise MigrationError("SQLite integrity check failed.")

    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigrationError("The database contains broken foreign-key relationships.")


def _reserve_backup_path(
    database_path: Path,
    source_version: int = SCHEMA_VERSION_V1,
) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for _ in range(10):
        backup_path = database_path.with_name(
            f"{database_path.name}.v{source_version}-backup-"
            f"{timestamp}-{uuid.uuid4().hex}.sqlite3"
        )
        try:
            with backup_path.open("xb"):
                pass
        except FileExistsError:
            continue
        except OSError:
            raise MigrationError("Could not reserve a new backup file.") from None
        return backup_path
    raise MigrationError("Could not reserve a unique backup filename.")


def _create_backup(
    database_path: Path,
    expected_snapshot: dict[str, tuple[tuple[object, ...], ...]],
    source_version: int = SCHEMA_VERSION_V1,
) -> Path:
    backup_path = _reserve_backup_path(database_path, source_version)
    source = None
    destination = None
    try:
        source = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        destination = sqlite3.connect(
            f"{backup_path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        source.backup(destination)
        destination.commit()
        destination.close()
        destination = None
        source.close()
        source = None

        validation = sqlite3.connect(
            f"{backup_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        try:
            version_matches = (
                schema_is_version_1(validation)
                if source_version == SCHEMA_VERSION_V1
                else schema_is_version_2(validation)
            )
            if not version_matches:
                raise MigrationError(
                    f"The SQLite backup does not match schema version {source_version}."
                )
            _validate_integrity(validation)
            if _snapshot(validation) != expected_snapshot:
                raise MigrationError("The SQLite backup does not match the source data.")
        finally:
            validation.close()

        return backup_path
    except Exception as error:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
        try:
            backup_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise MigrationError(
                "Backup creation failed and the incomplete backup could not be removed."
            ) from None
        if isinstance(error, MigrationError):
            raise
        raise MigrationError("Could not create and verify the SQLite backup.") from None


def _rebuild_matches(connection: sqlite3.Connection) -> None:
    old_sql = SCHEMA_OBJECTS_V2[0][2]
    create_new_sql = old_sql.replace(
        "CREATE TABLE matches (",
        "CREATE TABLE matches_migration_new (",
        1,
    )
    if create_new_sql == old_sql:
        raise MigrationError("The version 2 matches table definition is invalid.")

    connection.execute(create_new_sql)
    connection.execute(
        """
        INSERT INTO matches_migration_new (
            id,
            title,
            match_date,
            match_time,
            location,
            duration_minutes,
            max_players,
            price_per_slot_cents,
            status,
            created_at
        )
        SELECT
            id,
            title,
            match_date,
            match_time,
            location,
            duration_minutes,
            max_players,
            price_per_slot_cents,
            status,
            created_at
        FROM matches
        """
    )
    connection.execute("DROP TABLE matches")
    connection.execute(
        "ALTER TABLE matches_migration_new RENAME TO matches"
    )
    for object_type, name, statement in SCHEMA_OBJECTS_V2:
        if object_type == "index" and name == "idx_matches_status_date":
            connection.execute(statement)
            break
    else:
        raise MigrationError("The version 2 match index definition is missing.")


def _set_schema_version(
    connection: sqlite3.Connection,
    version: int,
) -> None:
    connection.execute(f"PRAGMA user_version = {version}")


def _create_v3_objects(connection: sqlite3.Connection) -> None:
    for _, _, statement in SCHEMA_OBJECTS_V3[len(SCHEMA_OBJECTS_V2):]:
        connection.execute(statement)


def _apply_migration(
    connection: sqlite3.Connection,
    before_snapshot: dict[str, tuple[tuple[object, ...], ...]],
    backup_path: Path,
) -> None:
    try:
        _rebuild_matches(connection)

        if not schema_matches(connection, SCHEMA_OBJECTS_V2):
            raise MigrationError("The rebuilt schema does not match version 2.")
        if _snapshot(connection) != before_snapshot:
            raise MigrationError("The migration changed data that must be preserved.")
        _validate_integrity(connection)

        _set_schema_version(connection, SCHEMA_VERSION_V2)
        if not schema_is_version_2(connection):
            raise MigrationError("The migrated schema version failed verification.")

        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise MigrationError(
            "Migration failed and was rolled back. "
            f"The verified backup is preserved at: {backup_path}"
        ) from None


def migrate_database(db_path: str | Path = DB_PATH) -> MigrationResult:
    try:
        database_path = Path(db_path).expanduser().resolve()
        if not database_path.is_file():
            raise MigrationError("The migration source database does not exist.")
    except OSError:
        raise MigrationError("The migration source database is unavailable.") from None

    connection = None
    original_foreign_keys = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        original_foreign_keys = connection.execute(
            "PRAGMA foreign_keys"
        ).fetchone()[0]

        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise MigrationError("Could not enable foreign-key checks.")

        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION_V2:
            if not schema_is_version_2(connection):
                raise MigrationError("The version 2 database schema is incompatible.")
            _validate_integrity(connection)
            return MigrationResult(database_path, None, True)
        if version != SCHEMA_VERSION_V1:
            raise MigrationError(
                f"Expected database schema version {SCHEMA_VERSION_V1}; found {version}."
            )

        connection.execute("PRAGMA foreign_keys = OFF")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 0:
            raise MigrationError("Could not disable foreign keys before the transaction.")

        connection.execute("BEGIN IMMEDIATE")
        if not schema_is_version_1(connection):
            raise MigrationError("The source schema does not match version 1.")
        _validate_integrity(connection)
        before_snapshot = _snapshot(connection)

        backup_path = _create_backup(database_path, before_snapshot)
        _apply_migration(connection, before_snapshot, backup_path)
        return MigrationResult(database_path, backup_path, False)
    except MigrationError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise MigrationError("Migration failed; the source database was not changed.") from None
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            if original_foreign_keys is not None:
                connection.execute(
                    f"PRAGMA foreign_keys = {int(original_foreign_keys)}"
                )
                if (
                    connection.execute("PRAGMA foreign_keys").fetchone()[0]
                    != original_foreign_keys
                ):
                    connection.close()
                    raise MigrationError("Could not restore the foreign-key setting.")
            connection.close()


def _apply_v2_to_v3(
    connection: sqlite3.Connection,
    before_snapshot: dict[str, tuple[tuple[object, ...], ...]],
    backup_path: Path,
) -> None:
    try:
        _create_v3_objects(connection)

        if not schema_matches(connection, SCHEMA_OBJECTS_V3):
            raise MigrationError("The schema does not match version 3.")
        if _snapshot(connection) != before_snapshot:
            raise MigrationError("The migration changed data that must be preserved.")
        _validate_integrity(connection)

        _set_schema_version(connection, SCHEMA_VERSION)
        if not schema_is_current(connection):
            raise MigrationError("The migrated schema version failed verification.")

        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise MigrationError(
            "Migration failed and was rolled back. "
            f"The verified backup is preserved at: {backup_path}"
        ) from None


def migrate_v2_to_v3(db_path: str | Path = DB_PATH) -> MigrationResult:
    try:
        database_path = Path(db_path).expanduser().resolve()
        if not database_path.is_file():
            raise MigrationError("The migration source database does not exist.")
    except OSError:
        raise MigrationError("The migration source database is unavailable.") from None

    connection = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise MigrationError("Could not enable foreign-key checks.")

        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            if not schema_is_current(connection):
                raise MigrationError("The version 3 database schema is incompatible.")
            _validate_integrity(connection)
            return MigrationResult(database_path, None, True)
        if version != SCHEMA_VERSION_V2:
            raise MigrationError(
                f"Expected database schema version {SCHEMA_VERSION_V2}; found {version}."
            )

        connection.execute("BEGIN IMMEDIATE")
        if not schema_is_version_2(connection):
            raise MigrationError("The source schema does not match version 2.")
        _validate_integrity(connection)
        before_snapshot = _snapshot(connection)

        backup_path = _create_backup(
            database_path,
            before_snapshot,
            source_version=SCHEMA_VERSION_V2,
        )
        _apply_v2_to_v3(connection, before_snapshot, backup_path)
        return MigrationResult(database_path, backup_path, False)
    except MigrationError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise MigrationError(
            "Migration failed; the source database was not changed."
        ) from None
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Explicitly migrate the Pelada SQLite database between schema versions."
    )
    confirmation = parser.add_mutually_exclusive_group(required=True)
    confirmation.add_argument(
        "--confirm-v1-to-v2",
        action="store_true",
        help="confirm the version 1 to version 2 migration",
    )
    confirmation.add_argument(
        "--confirm-v2-to-v3",
        action="store_true",
        help="confirm the version 2 to version 3 migration",
    )
    arguments = parser.parse_args()

    try:
        if arguments.confirm_v1_to_v2:
            result = migrate_database()
            target_version = SCHEMA_VERSION_V2
        else:
            result = migrate_v2_to_v3()
            target_version = SCHEMA_VERSION
    except MigrationError as error:
        parser.exit(1, f"Migration failed: {error}\n")

    if result.already_current:
        print(
            f"Schema v{target_version} validated; no changes made: "
            f"{result.database_path}"
        )
    else:
        print(
            f"Schema v{target_version} migration completed: "
            f"{result.database_path}"
        )
        print(f"Verified pre-migration backup: {result.backup_path}")


if __name__ == "__main__":
    main()
