import base64
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import unicodedata
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from availability import (
    InvalidLockTimestampError,
    locked_booking_is_active,
    occupied_bookings_by_match,
)
from storage import DB_PATH, schema_is_current


_PHONE_PATTERN = re.compile(r"\+?[\d\s().-]+", re.ASCII)
_CREDENTIAL_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}", re.ASCII)
_LOCAL_TIMEZONE = "America/Sao_Paulo"
_HOLD_DURATION = timedelta(minutes=10)
_CREDENTIAL_BYTES = 32


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


class ReservationIdempotencyConflictError(ReservationServiceError):
    pass


class ReservationNotFoundError(ReservationServiceError):
    pass


class ReservationClockError(ReservationServiceError):
    pass


class ReservationDatabaseError(ReservationServiceError):
    pass


class ReservationDatabaseBusyError(ReservationDatabaseError):
    pass


@dataclass(frozen=True, repr=False)
class ReservationCredentials:
    idempotency_key: str
    recovery_secret: str


@dataclass(frozen=True)
class TemporaryReservation:
    reservation_id: int
    match_id: int
    status: str
    locked_until: str
    price_per_slot_cents: int


def generate_reservation_credentials() -> ReservationCredentials:
    idempotency_key = secrets.token_urlsafe(_CREDENTIAL_BYTES)
    recovery_secret = secrets.token_urlsafe(_CREDENTIAL_BYTES)
    while hmac.compare_digest(idempotency_key, recovery_secret):
        recovery_secret = secrets.token_urlsafe(_CREDENTIAL_BYTES)
    return ReservationCredentials(idempotency_key, recovery_secret)


def _validate_match_id(match_id: int) -> None:
    if isinstance(match_id, bool) or not isinstance(match_id, int) or match_id <= 0:
        raise InvalidReservationDataError("The match identifier is invalid.")


def _validate_player_name(player_name: str) -> str:
    if not isinstance(player_name, str):
        raise InvalidReservationDataError("The player name is invalid.")
    normalized = unicodedata.normalize("NFC", player_name.strip())
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
    if digits.startswith("55") and len(digits) in (12, 13):
        digits = digits[2:]
    return digits


def _decode_credential(value: str) -> bytes:
    if not isinstance(value, str) or not _CREDENTIAL_PATTERN.fullmatch(value):
        raise InvalidReservationDataError("Reservation credentials are invalid.")
    try:
        decoded = base64.urlsafe_b64decode(value + "=")
    except ValueError:
        raise InvalidReservationDataError(
            "Reservation credentials are invalid."
        ) from None
    if (
        len(decoded) != _CREDENTIAL_BYTES
        or base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=") != value
    ):
        raise InvalidReservationDataError("Reservation credentials are invalid.")
    return decoded


def _credential_hash(purpose: bytes, credential: bytes) -> bytes:
    return hashlib.sha256(b"pelada-reservation-v1:" + purpose + b":" + credential).digest()


def _request_fingerprint(
    idempotency_credential: bytes,
    match_id: int,
    player_name: str,
    player_phone: str,
) -> bytes:
    request_bytes = json.dumps(
        {
            "match_id": match_id,
            "player_name": player_name,
            "player_phone": player_phone,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(
        idempotency_credential,
        b"pelada-reservation-request-v1:" + request_bytes,
        hashlib.sha256,
    ).digest()


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


def _open_database(
    path: Path,
    *,
    readonly: bool = False,
) -> sqlite3.Connection:
    mode = "ro" if readonly else "rw"
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode={mode}",
        uri=True,
        timeout=5.0,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise ReservationDatabaseError(
                "SQLite foreign key enforcement is unavailable."
            )
    except Exception:
        connection.close()
        raise
    return connection


def _reservation_result(
    row: sqlite3.Row,
    current_time: datetime,
) -> TemporaryReservation:
    status = row["status"]
    locked_until = row["original_locked_until"]
    if status == "bloqueada":
        try:
            if not locked_booking_is_active(locked_until, current_time):
                status = "expirada"
        except InvalidLockTimestampError:
            raise ReservationDatabaseError(
                "A stored reservation has an invalid lock timestamp."
            ) from None
    if not isinstance(locked_until, str):
        raise ReservationDatabaseError("The reservation has no lock timestamp.")
    return TemporaryReservation(
        reservation_id=row["id"],
        match_id=row["match_id"],
        status=status,
        locked_until=locked_until,
        price_per_slot_cents=row["price_per_slot_cents"],
    )


def _insert_reservation_metadata(
    connection: sqlite3.Connection,
    *,
    booking_id: int,
    idempotency_hash: bytes,
    recovery_hash: bytes,
    request_fingerprint: bytes,
    locked_until: str,
) -> None:
    connection.execute(
        """
        INSERT INTO reservation_operations (
            booking_id,
            idempotency_hash,
            recovery_hash,
            request_fingerprint,
            locked_until
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            booking_id,
            idempotency_hash,
            recovery_hash,
            request_fingerprint,
            locked_until,
        ),
    )


def create_temporary_reservation(
    match_id: int,
    player_name: str,
    player_phone: str,
    *,
    idempotency_key: str,
    recovery_secret: str,
    db_path: str | Path = DB_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TemporaryReservation:
    idempotency_credential = _decode_credential(idempotency_key)
    idempotency_hash = _credential_hash(b"idempotency", idempotency_credential)
    recovery_hash = None
    credential_validation_error = None
    try:
        recovery_credential = _decode_credential(recovery_secret)
        if hmac.compare_digest(idempotency_credential, recovery_credential):
            raise InvalidReservationDataError(
                "Reservation credentials must be independent."
            )
        recovery_hash = _credential_hash(b"recovery", recovery_credential)
    except InvalidReservationDataError as error:
        credential_validation_error = error

    normalized_name = None
    normalized_phone = None
    request_fingerprint = None
    request_validation_error = None
    try:
        _validate_match_id(match_id)
        normalized_name = _validate_player_name(player_name)
        normalized_phone = _validate_player_phone(player_phone)
        request_fingerprint = _request_fingerprint(
            idempotency_credential,
            match_id,
            normalized_name,
            normalized_phone,
        )
    except InvalidReservationDataError as error:
        request_validation_error = error

    try:
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise ReservationDatabaseError("The migration database is unavailable.")
    except OSError:
        raise ReservationDatabaseError("The migration database is unavailable.") from None

    connection = None
    try:
        connection = _open_database(path)
        connection.execute("BEGIN IMMEDIATE")
        current_time = _current_utc(clock)

        if not schema_is_current(connection):
            raise ReservationDatabaseError(
                "The migration database schema is incompatible."
            )

        existing = connection.execute(
            """
            SELECT
                operations.idempotency_hash,
                operations.recovery_hash,
                operations.request_fingerprint,
                bookings.id,
                bookings.match_id,
                bookings.status,
                operations.locked_until AS original_locked_until,
                matches.price_per_slot_cents
            FROM reservation_operations AS operations
            JOIN bookings ON bookings.id = operations.booking_id
            JOIN matches ON matches.id = bookings.match_id
            WHERE operations.idempotency_hash = ?
            """,
            (idempotency_hash,),
        ).fetchone()
        if existing is not None:
            if (
                credential_validation_error is not None
                or request_validation_error is not None
                or recovery_hash is None
                or request_fingerprint is None
            ):
                raise ReservationIdempotencyConflictError(
                    "The reservation request conflicts with an existing operation."
                )
            matches_request = (
                hmac.compare_digest(
                    bytes(existing["idempotency_hash"]),
                    idempotency_hash,
                )
                and hmac.compare_digest(
                    bytes(existing["recovery_hash"]),
                    recovery_hash,
                )
                and hmac.compare_digest(
                    bytes(existing["request_fingerprint"]),
                    request_fingerprint,
                )
            )
            if not matches_request:
                raise ReservationIdempotencyConflictError(
                    "The reservation request conflicts with an existing operation."
                )
            result = _reservation_result(existing, current_time)
            connection.commit()
            return result

        if credential_validation_error is not None:
            raise credential_validation_error
        if request_validation_error is not None:
            raise request_validation_error
        if normalized_name is None or normalized_phone is None:
            raise ReservationDatabaseError(
                "The reservation request could not be normalized."
            )
        if recovery_hash is None or request_fingerprint is None:
            raise ReservationDatabaseError(
                "The reservation credentials could not be validated."
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
        _insert_reservation_metadata(
            connection,
            booking_id=cursor.lastrowid,
            idempotency_hash=idempotency_hash,
            recovery_hash=recovery_hash,
            request_fingerprint=request_fingerprint,
            locked_until=locked_until_text,
        )
        result = TemporaryReservation(
            reservation_id=cursor.lastrowid,
            match_id=match_id,
            status="bloqueada",
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


def recover_temporary_reservation(
    recovery_secret: str,
    *,
    db_path: str | Path = DB_PATH,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> TemporaryReservation:
    recovery_credential = _decode_credential(recovery_secret)
    recovery_hash = _credential_hash(b"recovery", recovery_credential)

    try:
        path = Path(db_path).expanduser().resolve()
        if not path.is_file():
            raise ReservationDatabaseError("The migration database is unavailable.")
    except OSError:
        raise ReservationDatabaseError("The migration database is unavailable.") from None

    connection = None
    try:
        connection = _open_database(path, readonly=True)
        connection.execute("BEGIN")
        current_time = _current_utc(clock)
        if not schema_is_current(connection):
            raise ReservationDatabaseError(
                "The migration database schema is incompatible."
            )

        row = connection.execute(
            """
            SELECT
                operations.recovery_hash,
                bookings.id,
                bookings.match_id,
                bookings.status,
                operations.locked_until AS original_locked_until,
                matches.price_per_slot_cents
            FROM reservation_operations AS operations
            JOIN bookings ON bookings.id = operations.booking_id
            JOIN matches ON matches.id = bookings.match_id
            WHERE operations.recovery_hash = ?
            """,
            (recovery_hash,),
        ).fetchone()
        if row is None or not hmac.compare_digest(
            bytes(row["recovery_hash"]),
            recovery_hash,
        ):
            raise ReservationNotFoundError(
                "The reservation could not be recovered."
            )

        result = _reservation_result(row, current_time)
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
