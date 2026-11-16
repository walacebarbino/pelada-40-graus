from pathlib import Path
import re
import sqlite3


MIGRATION_DIR = Path(__file__).resolve().parent
DATA_DIR = (MIGRATION_DIR / "data").resolve()
DB_PATH = (DATA_DIR / "pelada_migracao.sqlite3").resolve()
SCHEMA_VERSION_V1 = 1
SCHEMA_VERSION = 2

SCHEMA_OBJECTS_V1 = (
    (
        "table",
        "matches",
        """
        CREATE TABLE matches (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'Pelada'
                CHECK (length(trim(title)) > 0),
            match_date TEXT NOT NULL
                CHECK (
                    length(match_date) = 10
                    AND date(match_date) IS NOT NULL
                    AND date(match_date) = match_date
                ),
            match_time TEXT NOT NULL
                CHECK (
                    length(match_time) = 5
                    AND match_time GLOB '[0-2][0-9]:[0-5][0-9]'
                    AND substr(match_time, 1, 2) <= '23'
                ),
            location TEXT NOT NULL DEFAULT 'Quadra Principal'
                CHECK (length(trim(location)) > 0),
            duration_minutes INTEGER NOT NULL DEFAULT 40
                CHECK (duration_minutes > 0),
            max_players INTEGER NOT NULL DEFAULT 12
                CHECK (max_players IN (10, 12)),
            price_per_slot_cents INTEGER NOT NULL DEFAULT 1500
                CHECK (
                    typeof(price_per_slot_cents) = 'integer'
                    AND price_per_slot_cents >= 0
                ),
            status TEXT NOT NULL DEFAULT 'agendada'
                CHECK (status IN ('agendada', 'em_andamento', 'finalizada', 'cancelada')),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
        )
        """,
    ),
    (
        "table",
        "bookings",
        """
        CREATE TABLE bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            player_name TEXT,
            player_phone TEXT,
            status TEXT NOT NULL DEFAULT 'disponivel'
                CHECK (status IN ('disponivel', 'bloqueada', 'confirmada', 'expirada')),
            locked_until TEXT,
            payment_status TEXT NOT NULL DEFAULT 'nao_iniciado'
                CHECK (
                    payment_status IN (
                        'nao_iniciado',
                        'cobranca_pendente',
                        'aprovado',
                        'recusado',
                        'cancelado',
                        'expirado'
                    )
                ),
            payment_ref TEXT UNIQUE,
            confirmed_at TEXT,
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE (id, match_id),
            FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
            CHECK (
                status != 'disponivel'
                OR (
                    player_name IS NULL
                    AND player_phone IS NULL
                    AND locked_until IS NULL
                    AND payment_status = 'nao_iniciado'
                    AND payment_ref IS NULL
                    AND confirmed_at IS NULL
                )
            ),
            CHECK (
                status != 'bloqueada'
                OR (
                    length(trim(coalesce(player_name, ''))) > 0
                    AND length(trim(coalesce(player_phone, ''))) > 0
                    AND locked_until IS NOT NULL
                    AND length(trim(locked_until)) > 0
                    AND confirmed_at IS NULL
                )
            ),
            CHECK (
                status != 'confirmada'
                OR (
                    length(trim(coalesce(player_name, ''))) > 0
                    AND length(trim(coalesce(player_phone, ''))) > 0
                    AND locked_until IS NULL
                    AND confirmed_at IS NOT NULL
                    AND payment_status = 'aprovado'
                )
            ),
            CHECK (
                status != 'expirada'
                OR (
                    player_name IS NULL
                    AND player_phone IS NULL
                    AND locked_until IS NULL
                    AND confirmed_at IS NULL
                )
            )
        )
        """,
    ),
    (
        "table",
        "votes",
        """
        CREATE TABLE votes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL,
            booking_id INTEGER NOT NULL,
            category TEXT NOT NULL
                CHECK (category IN ('gols', 'dribles', 'defesas')),
            voter_name TEXT NOT NULL
                CHECK (length(trim(voter_name)) > 0),
            created_at TEXT NOT NULL
                DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            UNIQUE (match_id, booking_id, voter_name COLLATE NOCASE),
            FOREIGN KEY (match_id) REFERENCES matches(id) ON DELETE CASCADE,
            FOREIGN KEY (booking_id, match_id)
                REFERENCES bookings(id, match_id) ON DELETE CASCADE
        )
        """,
    ),
    ("index", "idx_matches_status_date", "CREATE INDEX idx_matches_status_date ON matches(status, match_date)"),
    ("index", "idx_bookings_match_status", "CREATE INDEX idx_bookings_match_status ON bookings(match_id, status)"),
    ("index", "idx_bookings_status_locked_until", "CREATE INDEX idx_bookings_status_locked_until ON bookings(status, locked_until)"),
    ("index", "idx_votes_match_category", "CREATE INDEX idx_votes_match_category ON votes(match_id, category)"),
    ("index", "idx_votes_booking_match", "CREATE INDEX idx_votes_booking_match ON votes(booking_id, match_id)"),
)

SCHEMA_OBJECTS = tuple(
    (
        object_type,
        name,
        statement.replace(
            "CHECK (max_players IN (10, 12))",
            "CHECK (max_players IN (10, 12, 14))",
            1,
        )
        if object_type == "table" and name == "matches"
        else statement,
    )
    for object_type, name, statement in SCHEMA_OBJECTS_V1
)


def connect(db_path: str | Path = DB_PATH) -> sqlite3.Connection:
    resolved_path = Path(db_path).expanduser().resolve()
    connection = sqlite3.connect(str(resolved_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        connection.close()
        raise RuntimeError("SQLite foreign key enforcement could not be enabled.")

    return connection


def _normalize_sql(statement: str) -> str:
    normalized = " ".join(statement.split()).rstrip(";").casefold()
    return re.sub(r'"([a-z_][a-z0-9_]*)"', r"\1", normalized)


def schema_matches(
    connection: sqlite3.Connection,
    expected_objects: tuple[tuple[str, str, str], ...] = SCHEMA_OBJECTS,
) -> bool:
    actual_objects = {
        (row[0], row[1]): _normalize_sql(row[2] or "")
        for row in connection.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'view', 'trigger')
              AND substr(name, 1, 7) != 'sqlite_'
            """
        )
    }
    expected_objects = {
        (object_type, name): _normalize_sql(statement)
        for object_type, name, statement in expected_objects
    }
    return actual_objects == expected_objects


def schema_is_current(connection: sqlite3.Connection) -> bool:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    return version == SCHEMA_VERSION and schema_matches(connection)


def schema_is_version_1(connection: sqlite3.Connection) -> bool:
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    return version == SCHEMA_VERSION_V1 and schema_matches(
        connection,
        SCHEMA_OBJECTS_V1,
    )


def initialize_database(db_path: str | Path = DB_PATH) -> Path:
    resolved_path = Path(db_path).expanduser().resolve()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(resolved_path)

    try:
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]

        if version == SCHEMA_VERSION_V1:
            raise RuntimeError(
                "Schema version 1 requires the explicit migration command "
                "migrate_db.py --confirm-v1-to-v2."
            )

        if version == SCHEMA_VERSION:
            if not schema_is_current(connection):
                raise RuntimeError("The database schema does not match the recorded version.")
            connection.commit()
            return resolved_path

        if version != 0:
            raise RuntimeError(
                f"Unsupported database schema version: {version}."
            )

        existing_objects = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'index', 'view', 'trigger')
              AND substr(name, 1, 7) != 'sqlite_'
            LIMIT 1
            """
        ).fetchone()
        if existing_objects is not None:
            raise RuntimeError(
                "The database contains an unversioned schema and was left unchanged."
            )

        for _, _, statement in SCHEMA_OBJECTS:
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

        if not schema_matches(connection):
            raise RuntimeError("The initialized database schema failed verification.")

        connection.commit()
        return resolved_path
    except Exception:
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
