from datetime import datetime, timezone
import re
import sqlite3
from typing import Any


_UTC_TIMESTAMP_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)


class InvalidLockTimestampError(Exception):
    pass


def _parse_locked_until(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise InvalidLockTimestampError
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        raise InvalidLockTimestampError from None


def occupied_bookings_by_match(
    connection: sqlite3.Connection,
    current_time: datetime,
    *,
    match_id: int | None = None,
) -> dict[int, int]:
    if current_time.tzinfo is None or current_time.utcoffset() is None:
        raise ValueError("The comparison time must include a timezone.")
    current_time = current_time.astimezone(timezone.utc)

    query = """
        SELECT match_id, status, locked_until
        FROM bookings
        WHERE status IN ('confirmada', 'bloqueada')
    """
    parameters: tuple[int, ...] = ()
    if match_id is not None:
        query += " AND match_id = ?"
        parameters = (match_id,)

    occupied_by_match: dict[int, int] = {}
    for booking in connection.execute(query, parameters):
        occupies_slot = booking["status"] == "confirmada"
        if booking["status"] == "bloqueada":
            occupies_slot = _parse_locked_until(booking["locked_until"]) > current_time
        if occupies_slot:
            booking_match_id = booking["match_id"]
            occupied_by_match[booking_match_id] = (
                occupied_by_match.get(booking_match_id, 0) + 1
            )

    return occupied_by_match
