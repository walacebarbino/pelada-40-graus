from datetime import datetime, timezone
import re
import sqlite3
from typing import Any

from storage import schema_is_current


_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class InvalidLockTimestampError(Exception):
    pass


class IncompatibleAvailabilitySchemaError(sqlite3.DatabaseError):
    pass


def _parse_locked_until(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise InvalidLockTimestampError
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise InvalidLockTimestampError from None


def locked_booking_is_active(
    locked_until: Any,
    current_time: datetime,
) -> bool:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("The comparison time must include a timezone.")
    return _parse_locked_until(locked_until) > current_time.astimezone(timezone.utc)


def occupied_bookings_by_match(
    connection: sqlite3.Connection,
    current_time: datetime,
    *,
    match_id: int | None = None,
) -> dict[int, int]:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("The comparison time must include a timezone.")
    if not schema_is_current(connection):
        raise IncompatibleAvailabilitySchemaError(
            "The database schema is incompatible with availability queries."
        )
    current_time = current_time.astimezone(timezone.utc)

    query = """
        SELECT
            bookings.match_id,
            bookings.status,
            coalesce(
                reservation_operations.locked_until,
                bookings.locked_until
            ) AS locked_until
        FROM bookings
        LEFT JOIN reservation_operations
            ON reservation_operations.booking_id = bookings.id
        WHERE bookings.status IN ('confirmada', 'bloqueada')
    """
    parameters: tuple[int, ...] = ()
    if match_id is not None:
        query += " AND bookings.match_id = ?"
        parameters = (match_id,)

    occupied_by_match: dict[int, int] = {}
    for booking in connection.execute(query, parameters):
        occupies_slot = booking["status"] == "confirmada"
        if booking["status"] == "bloqueada":
            occupies_slot = locked_booking_is_active(
                booking["locked_until"],
                current_time,
            )
        if occupies_slot:
            booking_match_id = booking["match_id"]
            occupied_by_match[booking_match_id] = (
                occupied_by_match.get(booking_match_id, 0) + 1
            )

    return occupied_by_match
