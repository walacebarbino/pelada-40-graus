import sqlite3
from pathlib import Path

from storage import SCHEMA_OBJECTS_V2, schema_is_version_2


def create_valid_v2_database(database_path: Path) -> Path:
    if database_path.exists():
        raise FileExistsError(database_path)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for _, _, statement in SCHEMA_OBJECTS_V2:
            connection.execute(statement)

        connection.execute(
            """
            INSERT INTO matches (
                id, title, match_date, match_time, location, duration_minutes,
                max_players, price_per_slot_cents, status
            )
            VALUES (
                41, 'Partida de teste', '2030-07-14', '19:30',
                'Quadra de teste', 60, 12, 1875, 'agendada'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO bookings (
                id, match_id, player_name, player_phone, status,
                payment_status, confirmed_at
            )
            VALUES (
                71, 41, 'Pessoa de teste', '11911111111', 'confirmada',
                'aprovado', '2030-07-14T18:00:00Z'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO votes (id, match_id, booking_id, category, voter_name)
            VALUES (45, 41, 71, 'gols', 'Votante de teste')
            """
        )
        connection.execute("PRAGMA user_version = 2")
        if not schema_is_version_2(connection):
            raise RuntimeError("The temporary v2 fixture is invalid.")
        connection.commit()
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()

    return database_path


def snapshot_database(database_path: Path) -> dict[str, object]:
    connection = sqlite3.connect(database_path)
    try:
        objects = tuple(
            tuple(row)
            for row in connection.execute(
                """
                SELECT type, name, sql
                FROM sqlite_master
                WHERE type IN ('table', 'index', 'view', 'trigger')
                  AND substr(name, 1, 7) != 'sqlite_'
                ORDER BY type, name
                """
            )
        )
        return {
            "user_version": connection.execute(
                "PRAGMA user_version"
            ).fetchone()[0],
            "objects": objects,
            "matches": tuple(
                tuple(row)
                for row in connection.execute("SELECT * FROM matches ORDER BY id")
            ),
            "bookings": tuple(
                tuple(row)
                for row in connection.execute("SELECT * FROM bookings ORDER BY id")
            ),
            "votes": tuple(
                tuple(row)
                for row in connection.execute("SELECT * FROM votes ORDER BY id")
            ),
            "sqlite_sequence": tuple(
                tuple(row)
                for row in connection.execute(
                    "SELECT name, seq FROM sqlite_sequence ORDER BY name"
                )
            ),
        }
    finally:
        connection.close()


def assert_v2_database_unchanged(
    test_case,
    database_path: Path,
    before: dict[str, object],
) -> None:
    after = snapshot_database(database_path)
    test_case.assertEqual(after, before)
    test_case.assertEqual(after["user_version"], 2)
    object_names = {item[1] for item in after["objects"]}
    test_case.assertNotIn("reservation_operations", object_names)
