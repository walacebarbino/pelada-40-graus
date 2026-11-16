from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
import sqlite3
import unicodedata
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from availability import InvalidLockTimestampError, occupied_bookings_by_match
from storage import DB_PATH, schema_is_current


_PHONE_PATTERN = re.compile(r"\+?[\d\s().-]+", re.ASCII)
_LOCAL_TIMEZONE = "America/Sao_Paulo"
_HOLD_DURATION = timedelta(minutes=10)


class ReservationServiceError(Exception):
    pass


class InvalidReservationDataError(ReservationServiceError):
    pass


class MatchNotFoundError(ReservationServiceError):
    pass


class MatchNotReservableError(ReservationServiceError):
    pass


class MatchAlreadyStartedError(ReservationServiceError):
    pass


class NoVacancyError(ReservationServiceError):
    pass


class ReservationClockError(ReservationServiceError):
    pass


class ReservationDatabaseError(ReservationServiceError):
    pass


class ReservationDatabaseBusyError(ReservationDatabaseError):
    pass


@dataclass(frozen=True)
class TemporaryReservation:
    reservation_id: int
    match_id: int
    locked_until: str
    price_per_slot_cents: int


def _validate_match_id(match_id: int) -> None:
    if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
        raise InvalidReservationDataError("The match identifier is invalid.")


def _validate_player_name(player_name: str) -> str:
    if not isinstance(player_name, str):
        raise InvalidReservationDataError("The player name is invalid.")
    normalized = player_name.strip()
    if (
        not normalized
        or len(normalized) > 120
        or any(unicodedata.category(character) == "Cc" for character in normalized)
    ):
        raise InvalidReservationDataError("The player name is invalid.")
    return normalized


def _validate_player_phone(player_phone: str) -> str:
    if (
        not isinstance(player_phone, str)
        or len(player_phone) > 24
        or not _PHONE_PATTERN.fullmatch(player_phone)
    ):
        raise InvalidReservationDataError("The player phone is invalid.")

    digits = "".join(
        character for character in player_phone if "0" <= character <= "9"
    )
    if player_phone.startswith("+"):
        valid_phone = player_phone.startswith("+55") and len(digits) in (12, 13)
    else:
        valid_phone = len(digits) in (10, 11) or (
            digits.startswith("55") and len(digits) in (12, 13)
        )
    if not valid_phone:
        raise InvalidReservationDataError("The player phone is invalid.")
    return digits


def _current_utc(clock: Callable[[], datetime]) -> datetime:
    current_time = clock()
    if (
        not isinstance(current_time, datetime)
        or current_time.tzinfo is None
        or current_time.utcoffset() is None
    ):
        raise ReservationClockError("The reservation clock must return an aware datetime.")
    return current_time.astimezone(timezone.utc)


def _match_start_utc(match_date: str, match_time: str) -> datetime:
    try:
        local_start = datetime.combine(
            date.fromisoformat(match_date),
            time.fromisoformat(match_time),
            tzinfo=ZoneInfo(_LOCAL_TIMEZONE),
        )
    except (ValueError, ZoneInfoNotFoundError):
        raise ReservationDatabaseError(
            "The match date, time, or local timezone is invalid."
        ) from None
    return local_start.astimezone(timezone.utc)


def _is_database_busy(error: sqlite3.OperationalError) -> bool:
    error_code = getattr(error, "sqlite_errorcode", None)
    return error_code is not None and (
        error_code & 0xFF
    ) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)


def _rollback(connection: sqlite3.Connection | None) -> None:
    if connection is not None and connection.in_transaction:
        connection.rollback()


def create_temporary_reservation(
    match_id: int,
    player_name: str,
    player_phone: str,
    *,
    db_path: str | Path = DB_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TemporaryReservation:
    _validate_match_id(match_id)
    normalized_name = _validate_player_name(player_name)
    normalized_phone = _validate_player_phone(player_phone)

    try:
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise ReservationDatabaseError("The migration database is unavailable.")
    except OSError:
        raise ReservationDatabaseError("The migration database is unavailable.") from None

    connection = None
    try:
        connection = sqlite3.connect(
            f"{path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ReservationDatabaseError(
                "SQLite foreign key enforcement is unavailable."
            )

        connection.execute("BEGIN IMMEDIATE")
        current_time = _current_utc(clock)

        if not schema_is_current(connection):
            raise ReservationDatabaseError(
                "The migration database schema is incompatible."
            )

        match = connection.execute(
            """
            SELECT match_date, match_time, max_players, price_per_slot_cents, status
            FROM matches
            WHERE id = ?
            """,
            (match_id,),
        ).fetchone()
        if match is None:
            raise MatchNotFoundError("The match does not exist.")
        if match["status"] != "agendada":
            raise MatchNotReservableError("The match is not open for reservations.")
        if current_time >= _match_start_utc(match["match_date"], match["match_time"]):
            raise MatchAlreadyStartedError("The match has already started.")

        try:
            occupied = occupied_bookings_by_match(
                connection,
                current_time,
                match_id=match_id,
            ).get(match_id, 0)
        except InvalidLockTimestampError:
            raise ReservationDatabaseError(
                "A stored reservation has an invalid lock timestamp."
            ) from None

        if occupied >= match["max_players"]:
            raise NoVacancyError("The match has no available slots.")

        locked_until = current_time + _HOLD_DURATION
        locked_until_text = locked_until.isoformat(timespec="microseconds").replace(
            "+00:00",
            "Z",
        )
        cursor = connection.execute(
            """
            INSERT INTO bookings (
                match_id,
                player_name,
                player_phone,
                status,
                locked_until,
                payment_status
            )
            VALUES (?, ?, ?, 'bloqueada', ?, 'nao_iniciado')
            """,
            (match_id, normalized_name, normalized_phone, locked_until_text),
        )
        result = TemporaryReservation(
            reservation_id=cursor.lastrowid,
            match_id=match_id,
            locked_until=locked_until_text,
            price_per_slot_cents=match["price_per_slot_cents"],
        )
        connection.commit()
        return result
    except ReservationServiceError:
        _rollback(connection)
        raise
    except sqlite3.OperationalError as error:
        _rollback(connection)
        if _is_database_busy(error):
            raise ReservationDatabaseBusyError(
                "The migration database is busy."
            ) from None
        raise ReservationDatabaseError(
            "The migration database is unavailable or incompatible."
        ) from None
    except sqlite3.Error:
        _rollback(connection)
        raise ReservationDatabaseError(
            "The migration database is unavailable or incompatible."
        ) from None
    except Exception:
        _rollback(connection)
        raise
    finally:
        if connection is not None:
            connection.close()
