from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from availability import InvalidLockTimestampError, occupied_bookings_by_match
from storage import DB_PATH, schema_is_current


class DatabaseReadError(Exception):
    pass


class MatchNotFoundError(Exception):
    pass


@contextmanager
def _readonly_connection(db_path: str | Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    path = Path(db_path).expanduser().resolve()
    if not path.is_file():
        raise DatabaseReadError("Migration database is unavailable.")

    connection = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise DatabaseReadError("SQLite foreign key enforcement is unavailable.")
        if not schema_is_current(connection):
            raise DatabaseReadError("Migration database schema is incompatible.")

        connection.execute("BEGIN")
        yield connection
    except DatabaseReadError:
        raise
    except sqlite3.Error:
        raise DatabaseReadError("Migration database is unavailable or incompatible.") from None
    finally:
        if connection is not None:
            if connection.in_transaction:
                connection.rollback()
            connection.close()


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("The comparison time must include a timezone.")
    return value.astimezone(timezone.utc)


def list_matches(
    db_path: str | Path = DB_PATH,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = _utc_now(now)

    with _readonly_connection(db_path) as connection:
        matches = connection.execute(
            """
            SELECT
                id,
                title,
                match_date,
                match_time,
                location,
                duration_minutes,
                max_players,
                price_per_slot_cents,
                status
            FROM matches
            ORDER BY match_date ASC, match_time ASC, id ASC
            """
        ).fetchall()
        try:
            occupied_by_match = occupied_bookings_by_match(
                connection,
                current_time,
            )
        except InvalidLockTimestampError:
            raise DatabaseReadError(
                "A reservation contains an invalid lock timestamp."
            ) from None

        result: list[dict[str, Any]] = []
        for row in matches:
            match = dict(row)
            occupied = occupied_by_match.get(match["id"], 0)
            match["vagas_ocupadas"] = occupied
            match["vagas_livres"] = max(0, match["max_players"] - occupied)
            result.append(match)

    return result


def list_confirmed_participant_names(
    partida_id: int,
    db_path: str | Path = DB_PATH,
) -> list[str]:
    if isinstance(partida_id, bool) or not isinstance(partida_id, int):
        raise ValueError("The match identifier must be an integer.")

    with _readonly_connection(db_path) as connection:
        match = connection.execute(
            "SELECT id FROM matches WHERE id = ?",
            (partida_id,),
        ).fetchone()
        if match is None:
            raise MatchNotFoundError

        rows = connection.execute(
            """
            SELECT player_name
            FROM bookings
            WHERE match_id = ? AND status = 'confirmada'
            ORDER BY id ASC
            """,
            (partida_id,),
        ).fetchall()

        names: list[str] = []
        for row in rows:
            name = row["player_name"]
            if not isinstance(name, str) or not name.strip():
                raise DatabaseReadError("A confirmed reservation has no valid player name.")
            names.append(name)

    return names
