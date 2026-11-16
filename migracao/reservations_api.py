from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from reservations import (
    InvalidReservationDataError,
    MatchAlreadyStartedError,
    MatchNotFoundError,
    MatchNotReservableError,
    NoVacancyError,
    ReservationClockError,
    ReservationDatabaseBusyError,
    ReservationDatabaseError,
    ReservationIdempotencyConflictError,
    ReservationNotFoundError,
    create_temporary_reservation,
    recover_temporary_reservation,
)
from storage import DB_PATH


CREATE_RESERVATION_PATH = "/api/v2/reservas"
RECOVER_RESERVATION_PATH = "/api/v2/reservas/recuperacao"
RESERVATION_PATHS = frozenset(
    (CREATE_RESERVATION_PATH, RECOVER_RESERVATION_PATH)
)
BODY_LIMIT_BYTES = 4096
HEADER_LIMIT_BYTES = 8192
IDEMPOTENCY_KEY_MAX_LENGTH = 43
AUTHORIZATION_MAX_LENGTH = 50
LOGGER = logging.getLogger(__name__)
Clock = Callable[[], datetime]
MonotonicClock = Callable[[], float]


@dataclass(frozen=True)
class RateLimitPolicy:
    limit: int
    window_seconds: float

    def __post_init__(self) -> None:
        if self.limit < 1 or not math.isfinite(self.window_seconds):
            raise ValueError("Rate-limit values must be finite and positive.")
        if self.window_seconds <= 0:
            raise ValueError("Rate-limit values must be finite and positive.")


@dataclass
class _TokenBucket:
    tokens: float
    updated_at: float
    last_seen: float


class InMemoryReservationRateLimiter:
    def __init__(
        self,
        *,
        create_policy: RateLimitPolicy = RateLimitPolicy(5, 60.0),
        recovery_policy: RateLimitPolicy = RateLimitPolicy(10, 60.0),
        max_entries: int = 10_000,
        monotonic_clock: MonotonicClock = time.monotonic,
    ) -> None:
        if max_entries < 1:
            raise ValueError("The rate limiter must retain at least one entry.")
        self._policies = {
            "create": create_policy,
            "recover": recovery_policy,
        }
        self._max_entries = max_entries
        self._monotonic_clock = monotonic_clock
        self._entries: OrderedDict[tuple[str, str], _TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def consume(self, action: str, key: str) -> int | None:
        policy = self._policies.get(action)
        if policy is None:
            raise ValueError("Unknown reservation rate-limit action.")

        now = self._monotonic_clock()
        with self._lock:
            self._remove_expired_entries(now)
            entry_key = (action, key)
            bucket = self._entries.get(entry_key)
            refill_rate = policy.limit / policy.window_seconds

            if bucket is None:
                if len(self._entries) >= self._max_entries:
                    self._entries.popitem(last=False)
                bucket = _TokenBucket(
                    tokens=float(policy.limit),
                    updated_at=now,
                    last_seen=now,
                )
                self._entries[entry_key] = bucket
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                bucket.tokens = min(
                    float(policy.limit),
                    bucket.tokens + elapsed * refill_rate,
                )
                bucket.updated_at = max(now, bucket.updated_at)
                bucket.last_seen = max(now, bucket.last_seen)
                self._entries.move_to_end(entry_key)

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None

            return max(
                1,
                math.ceil((1.0 - bucket.tokens) / refill_rate),
            )

    def _remove_expired_entries(self, now: float) -> None:
        expired = [
            key
            for key, bucket in self._entries.items()
            if now - bucket.last_seen
            >= self._policies[key[0]].window_seconds
        ]
        for key in expired:
            del self._entries[key]

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ReservationHTTPSettings:
    db_path: str | Path = DB_PATH
    clock: Clock = _utc_now
    limiter: InMemoryReservationRateLimiter = field(
        default_factory=InMemoryReservationRateLimiter
    )
    allowed_origins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        origins = tuple(origin.strip() for origin in self.allowed_origins)
        if len(set(origins)) != len(origins):
            raise ValueError("Allowed reservation origins must be unique.")
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                origin == "null"
                or parsed.scheme not in ("http", "https")
                or not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Reservation origins must be exact origins.")
        object.__setattr__(self, "allowed_origins", origins)

    @classmethod
    def from_environment(cls) -> "ReservationHTTPSettings":
        raw_origins = os.environ.get("PELADA_RESERVATION_ALLOWED_ORIGINS", "")
        origins = tuple(
            origin.strip()
            for origin in raw_origins.split(",")
            if origin.strip()
        )
        create_limit = _read_positive_integer(
            "PELADA_RESERVATION_CREATE_LIMIT",
            5,
        )
        recovery_limit = _read_positive_integer(
            "PELADA_RESERVATION_RECOVERY_LIMIT",
            10,
        )
        window_seconds = _read_positive_float(
            "PELADA_RESERVATION_RATE_WINDOW_SECONDS",
            60.0,
        )
        max_entries = _read_positive_integer(
            "PELADA_RESERVATION_LIMITER_MAX_ENTRIES",
            10_000,
        )
        return cls(
            limiter=InMemoryReservationRateLimiter(
                create_policy=RateLimitPolicy(create_limit, window_seconds),
                recovery_policy=RateLimitPolicy(recovery_limit, window_seconds),
                max_entries=max_entries,
            ),
            allowed_origins=origins,
        )


def _read_positive_integer(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer.") from None
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _read_positive_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a positive finite number.") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError(f"{name} must be a positive finite number.")
    return parsed


def reservation_http_enabled(value: str | None) -> bool:
    return value == "1"


def _error_response(
    status_code: int,
    code: str,
    *,
    retry_after: int | None = None,
) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code}},
        headers=headers,
    )


def _service_error_response(
    error: Exception,
    *,
    validation_status: int = 422,
) -> JSONResponse:
    if isinstance(error, ReservationIdempotencyConflictError):
        return _error_response(409, "idempotency_conflict")
    if isinstance(error, NoVacancyError):
        return _error_response(409, "no_vacancy")
    if isinstance(error, (MatchAlreadyStartedError, MatchNotReservableError)):
        return _error_response(409, "match_unavailable")
    if isinstance(error, MatchNotFoundError):
        return _error_response(404, "match_not_found")
    if isinstance(error, InvalidReservationDataError):
        return _error_response(validation_status, "invalid_request")
    if isinstance(error, ReservationDatabaseBusyError):
        return _error_response(503, "service_unavailable", retry_after=1)
    if isinstance(error, (ReservationDatabaseError, ReservationClockError)):
        return _error_response(503, "service_unavailable")
    return _unexpected_response(error)


def _unexpected_response(error: Exception) -> JSONResponse:
    LOGGER.error(
        "Reservation HTTP operation failed; error_type=%s",
        type(error).__name__,
    )
    return _error_response(500, "internal_error")


def _header_values(request: Request, name: bytes) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in request.scope.get("headers", [])
        if key.lower() == name
    ]


def _bearer_credential(request: Request) -> str:
    values = _header_values(request, b"authorization")
    if len(values) != 1 or len(values[0]) > AUTHORIZATION_MAX_LENGTH:
        return ""
    parts = values[0].split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        return ""
    return parts[1]


def _client_key(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _duplicate_free_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


async def _request_payload(
    request: Request,
) -> tuple[Any, int]:
    content_types = _header_values(request, b"content-type")
    if len(content_types) != 1 or (
        content_types[0].split(";", 1)[0].strip().casefold()
        != "application/json"
    ):
        return None, 415

    try:
        payload = json.loads(
            await request.body(),
            object_pairs_hook=_duplicate_free_object,
        )
    except (RecursionError, UnicodeDecodeError, ValueError):
        return None, 422
    return payload, 0


router = APIRouter(tags=["reservations"])


@router.post(CREATE_RESERVATION_PATH)
async def create_reservation(request: Request) -> Response:
    settings: ReservationHTTPSettings = request.app.state.reservation_http_settings
    retry_after = settings.limiter.consume("create", _client_key(request))
    if retry_after is not None:
        return _error_response(
            429,
            "rate_limited",
            retry_after=retry_after,
        )

    idempotency_keys = _header_values(request, b"idempotency-key")
    if (
        len(idempotency_keys) != 1
        or len(idempotency_keys[0]) > IDEMPOTENCY_KEY_MAX_LENGTH
    ):
        return _error_response(422, "invalid_request")
    idempotency_key = idempotency_keys[0]
    recovery_secret = _bearer_credential(request)

    payload, payload_error_status = await _request_payload(request)
    valid_shape = (
        isinstance(payload, dict)
        and set(payload) == {"match_id", "player_name", "player_phone"}
    )
    if valid_shape:
        match_id = payload["match_id"]
        player_name = payload["player_name"]
        player_phone = payload["player_phone"]
    else:
        match_id = 0
        player_name = None
        player_phone = None

    try:
        reservation = await run_in_threadpool(
            create_temporary_reservation,
            match_id,
            player_name,
            player_phone,
            idempotency_key=idempotency_key,
            recovery_secret=recovery_secret,
            db_path=settings.db_path,
            clock=settings.clock,
        )
    except Exception as error:
        validation_status = payload_error_status or 422
        if isinstance(error, ReservationIdempotencyConflictError):
            validation_status = 422
        return _service_error_response(
            error,
            validation_status=validation_status,
        )

    return JSONResponse(
        status_code=200,
        content={
            "status": reservation.status,
            "locked_until": reservation.locked_until,
            "price_per_slot_cents": reservation.price_per_slot_cents,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get(RECOVER_RESERVATION_PATH)
async def recover_reservation(request: Request) -> Response:
    settings: ReservationHTTPSettings = request.app.state.reservation_http_settings
    retry_after = settings.limiter.consume("recover", _client_key(request))
    if retry_after is not None:
        return _error_response(
            429,
            "rate_limited",
            retry_after=retry_after,
        )

    recovery_secret = _bearer_credential(request)
    try:
        reservation = await run_in_threadpool(
            recover_temporary_reservation,
            recovery_secret,
            db_path=settings.db_path,
            clock=settings.clock,
        )
    except (InvalidReservationDataError, ReservationNotFoundError):
        return _error_response(404, "reservation_not_found")
    except Exception as error:
        return _service_error_response(error)

    return JSONResponse(
        status_code=200,
        content={
            "status": reservation.status,
            "locked_until": reservation.locked_until,
            "price_per_slot_cents": reservation.price_per_slot_cents,
        },
        headers={"Cache-Control": "no-store"},
    )


class ReservationBoundaryMiddleware:
    def __init__(
        self,
        app: Any,
        *,
        settings: ReservationHTTPSettings,
    ) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "").rstrip("/")
        if path not in RESERVATION_PATHS:
            await self.app(scope, receive, send)
            return

        response_started = False
        origin = self._single_header(scope, b"origin")
        allowed_origin = origin if origin in self.settings.allowed_origins else None

        async def secure_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers = self._set_header(headers, b"cache-control", b"no-store")
                headers = self._set_header(
                    headers,
                    b"x-content-type-options",
                    b"nosniff",
                )
                if allowed_origin is not None:
                    headers = self._set_header(
                        headers,
                        b"access-control-allow-origin",
                        allowed_origin.encode("latin-1"),
                    )
                    headers = self._add_vary_origin(headers)
                message = {**message, "headers": headers}
                response_started = True
            await send(message)

        async def reject(status_code: int, code: str) -> None:
            response = _error_response(status_code, code)
            await response(scope, receive, secure_send)

        try:
            header_bytes = sum(
                len(name) + len(value)
                for name, value in scope.get("headers", [])
            )
            if header_bytes > HEADER_LIMIT_BYTES:
                await reject(431, "headers_too_large")
                return

            origin_values = self._header_values(scope, b"origin")
            if len(origin_values) > 1:
                await reject(403, "origin_not_allowed")
                return
            if origin is not None and (
                origin == "null" or allowed_origin is None
            ):
                await reject(403, "origin_not_allowed")
                return

            if scope["method"] == "OPTIONS":
                await self._preflight(scope, origin, allowed_origin, secure_send)
                return

            if scope.get("query_string"):
                await reject(
                    404 if path == RECOVER_RESERVATION_PATH else 400,
                    "invalid_request",
                )
                return

            content_length = self._header_values(scope, b"content-length")
            if len(content_length) > 1:
                await reject(400, "invalid_request")
                return
            if content_length:
                value = content_length[0]
                if not value.isascii() or not value.isdecimal():
                    await reject(400, "invalid_request")
                    return
                significant_digits = value.lstrip("0") or "0"
                limit_text = str(BODY_LIMIT_BYTES)
                if (
                    len(significant_digits) > len(limit_text)
                    or (
                        len(significant_digits) == len(limit_text)
                        and significant_digits > limit_text
                    )
                ):
                    await reject(413, "request_too_large")
                    return

            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                if message["type"] != "http.request":
                    continue
                chunk = message.get("body", b"")
                if len(body) + len(chunk) > BODY_LIMIT_BYTES:
                    await reject(413, "request_too_large")
                    return
                body.extend(chunk)
                if not message.get("more_body", False):
                    break

            if scope["method"] == "GET" and body:
                await reject(400, "invalid_request")
                return

            body_bytes = bytes(body)
            replayed = False

            async def replay_receive() -> dict[str, Any]:
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {
                        "type": "http.request",
                        "body": body_bytes,
                        "more_body": False,
                    }
                return {"type": "http.disconnect"}

            await self.app(scope, replay_receive, secure_send)
        except Exception as error:
            if response_started:
                raise
            LOGGER.error(
                "Reservation HTTP boundary failed; error_type=%s",
                type(error).__name__,
            )
            response = _error_response(500, "internal_error")
            await response(scope, receive, secure_send)

    async def _preflight(
        self,
        scope: dict[str, Any],
        origin: str | None,
        allowed_origin: str | None,
        send: Any,
    ) -> None:
        if origin is None or allowed_origin is None:
            await self._send_error(scope, send, 403, "origin_not_allowed")
            return

        requested_method = self._single_header(
            scope,
            b"access-control-request-method",
        )
        expected_methods = (
            {"POST"}
            if scope.get("path", "").rstrip("/") == CREATE_RESERVATION_PATH
            else {"GET"}
        )
        requested_headers = self._single_header(
            scope,
            b"access-control-request-headers",
        )
        if len(self._header_values(scope, b"access-control-request-headers")) > 1:
            await self._send_error(scope, send, 403, "origin_not_allowed")
            return
        header_names = {
            item.strip().casefold()
            for item in (requested_headers or "").split(",")
            if item.strip()
        }
        if (
            requested_method not in expected_methods
            or not header_names.issubset(
                {"authorization", "content-type", "idempotency-key"}
            )
        ):
            await self._send_error(scope, send, 403, "origin_not_allowed")
            return

        headers = [
            (b"access-control-allow-origin", allowed_origin.encode("latin-1")),
            (b"access-control-allow-methods", requested_method.encode("ascii")),
            (
                b"access-control-allow-headers",
                ", ".join(sorted(header_names)).encode("ascii"),
            ),
            (b"access-control-max-age", b"300"),
            (b"vary", b"Origin"),
            (b"cache-control", b"no-store"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": b""})

    async def _send_error(
        self,
        scope: dict[str, Any],
        send: Any,
        status_code: int,
        code: str,
    ) -> None:
        response = _error_response(status_code, code)
        await response(scope, _unused_receive, send)

    @staticmethod
    def _single_header(
        scope: dict[str, Any],
        name: bytes,
    ) -> str | None:
        values = ReservationBoundaryMiddleware._header_values(scope, name)
        return values[0] if len(values) == 1 else None

    @staticmethod
    def _header_values(scope: dict[str, Any], name: bytes) -> list[str]:
        return [
            value.decode("latin-1")
            for key, value in scope.get("headers", [])
            if key.lower() == name
        ]

    @staticmethod
    def _set_header(
        headers: list[tuple[bytes, bytes]],
        name: bytes,
        value: bytes,
    ) -> list[tuple[bytes, bytes]]:
        return [
            (key, header_value)
            for key, header_value in headers
            if key.lower() != name
        ] + [(name, value)]

    @classmethod
    def _add_vary_origin(
        cls,
        headers: list[tuple[bytes, bytes]],
    ) -> list[tuple[bytes, bytes]]:
        vary_values = [
            value.decode("latin-1")
            for key, value in headers
            if key.lower() == b"vary"
        ]
        tokens = {
            token.strip()
            for value in vary_values
            for token in value.split(",")
            if token.strip()
        }
        tokens.add("Origin")
        headers = [
            (key, value)
            for key, value in headers
            if key.lower() != b"vary"
        ]
        return headers + [
            (b"vary", ", ".join(sorted(tokens)).encode("latin-1"))
        ]


async def _unused_receive() -> dict[str, str]:
    return {"type": "http.disconnect"}


def configure_reservation_http(
    app: FastAPI,
    *,
    enabled: bool = False,
    settings: ReservationHTTPSettings | None = None,
) -> None:
    if not enabled:
        return
    if any(getattr(route, "path", None) in RESERVATION_PATHS for route in app.routes):
        raise RuntimeError("Reservation HTTP routes are already configured.")

    settings = settings or ReservationHTTPSettings.from_environment()
    app.state.reservation_http_settings = settings
    app.add_middleware(
        ReservationBoundaryMiddleware,
        settings=settings,
    )
    app.include_router(router)
