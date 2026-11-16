from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Any, Iterator

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from storage import DB_PATH, schema_is_current


MAX_SAFE_INTEGER = 9_007_199_254_740_991
_TIME_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class MatchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: StrictStr
    match_date: StrictStr
    match_time: StrictStr
    location: StrictStr
    duration_minutes: StrictInt = Field(gt=0, le=MAX_SAFE_INTEGER)
    max_players: StrictInt
    price_per_slot_cents: StrictInt = Field(ge=0, le=MAX_SAFE_INTEGER)

    @field_validator("title", "location")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("This field must not be empty.")
        return normalized

    @field_validator("match_date")
    @classmethod
    def require_iso_date(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("Use a valid YYYY-MM-DD date.") from None
        if parsed.isoformat() != value:
            raise ValueError("Use a valid YYYY-MM-DD date.")
        return value

    @field_validator("match_time")
    @classmethod
    def require_hhmm_time(cls, value: str) -> str:
        if not _TIME_PATTERN.fullmatch(value):
            raise ValueError("Use a valid HH:MM time.")
        return value

    @field_validator("max_players")
    @classmethod
    def require_supported_player_limit(cls, value: int) -> int:
        if value not in (10, 12, 14):
            raise ValueError("The player limit must be 10, 12, or 14.")
        return value


class AdminDatabaseError(Exception):
    pass


class AdminKeyNotConfiguredError(Exception):
    pass


class InvalidAdminKeyError(Exception):
    pass


class AdminMatchNotFoundError(Exception):
    pass


class MatchEditConflictError(Exception):
    pass


class MatchDeleteConflictError(Exception):
    pass


def verify_admin_key(provided_key: str | None) -> None:
    configured_key = os.environ.get("PELADA_ADMIN_KEY")
    if not configured_key or not configured_key.strip():
        raise AdminKeyNotConfiguredError

    if provided_key is None or not secrets.compare_digest(
        provided_key.encode("utf-8"),
        configured_key.encode("utf-8"),
    ):
        raise InvalidAdminKeyError


@contextmanager
def _write_transaction(db_path: str | Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    connection = None
    try:
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise AdminDatabaseError("Migration database is unavailable.")
    except OSError:
        raise AdminDatabaseError("Migration database is unavailable.") from None

    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise AdminDatabaseError("SQLite foreign key enforcement is unavailable.")

        connection.execute("BEGIN IMMEDIATE")
        if not schema_is_current(connection):
            raise AdminDatabaseError("Migration database schema is incompatible.")

        yield connection
        connection.commit()
    except AdminDatabaseError:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    except sqlite3.Error:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise AdminDatabaseError("Migration database is unavailable or incompatible.") from None
    except Exception:
        if connection is not None and connection.in_transaction:
            connection.rollback()
        raise
    finally:
        if connection is not None:
            connection.close()


def _match_response(connection: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    row = connection.execute(
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
        WHERE id = ?
        """,
        (match_id,),
    ).fetchone()
    if row is None:
        raise AdminDatabaseError("The match could not be read after writing.")

    result = dict(row)
    result["vagas_ocupadas"] = 0
    result["vagas_livres"] = result["max_players"]
    return result


def create_match(
    match: MatchCreateRequest,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    with _write_transaction(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO matches (
                title,
                match_date,
                match_time,
                location,
                duration_minutes,
                max_players,
                price_per_slot_cents,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'agendada')
            """,
            (
                match.title,
                match.match_date,
                match.match_time,
                match.location,
                match.duration_minutes,
                match.max_players,
                match.price_per_slot_cents,
            ),
        )
        return _match_response(connection, cursor.lastrowid)


def update_match(
    match_id: int,
    match: MatchCreateRequest,
    db_path: str | Path = DB_PATH,
) -> dict[str, Any]:
    with _write_transaction(db_path) as connection:
        current = connection.execute(
            "SELECT status FROM matches WHERE id = ?",
            (match_id,),
        ).fetchone()
        if current is None:
            raise AdminMatchNotFoundError

        has_reservations = connection.execute(
            "SELECT 1 FROM bookings WHERE match_id = ? LIMIT 1",
            (match_id,),
        ).fetchone()
        if current["status"] != "agendada" or has_reservations is not None:
            raise MatchEditConflictError

        cursor = connection.execute(
            """
            UPDATE matches
            SET title = ?,
                match_date = ?,
                match_time = ?,
                location = ?,
                duration_minutes = ?,
                max_players = ?,
                price_per_slot_cents = ?
            WHERE id = ?
            """,
            (
                match.title,
                match.match_date,
                match.match_time,
                match.location,
                match.duration_minutes,
                match.max_players,
                match.price_per_slot_cents,
                match_id,
            ),
        )
        if cursor.rowcount != 1:
            raise AdminMatchNotFoundError

        return _match_response(connection, match_id)


def delete_match(
    match_id: int,
    db_path: str | Path = DB_PATH,
) -> None:
    with _write_transaction(db_path) as connection:
        current = connection.execute(
            "SELECT status FROM matches WHERE id = ?",
            (match_id,),
        ).fetchone()
        if current is None:
            raise AdminMatchNotFoundError

        has_reservations = connection.execute(
            "SELECT 1 FROM bookings WHERE match_id = ? LIMIT 1",
            (match_id,),
        ).fetchone()
        has_votes = connection.execute(
            "SELECT 1 FROM votes WHERE match_id = ? LIMIT 1",
            (match_id,),
        ).fetchone()
        if (
            current["status"] != "agendada"
            or has_reservations is not None
            or has_votes is not None
        ):
            raise MatchDeleteConflictError

        cursor = connection.execute(
            "DELETE FROM matches WHERE id = ?",
            (match_id,),
        )
        if cursor.rowcount != 1:
            raise AdminMatchNotFoundError
