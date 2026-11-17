import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from fastapi import FastAPI

from reservations import generate_reservation_credentials
from reservations_api import (
    CREATE_RESERVATION_PATH,
    RECOVER_RESERVATION_PATH,
    InMemoryReservationRateLimiter,
    RateLimitPolicy,
    ReservationHTTPSettings,
    RESERVATION_PATHS,
    configure_reservation_http,
    reservation_http_enabled,
)
from storage import DB_PATH, connect, initialize_database
from v2_fixture import (
    assert_v2_database_unchanged,
    create_valid_v2_database,
    snapshot_database,
)


_NOW = datetime(2030, 7, 14, 21, 0, 0, 123456, tzinfo=timezone.utc)
_ALLOWED_ORIGIN = "https://client.example.test"
_PII_MARKERS = ("Pessoa Privada", "11987654321")


class _DropFirstResponseTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.drop_response = True

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        response = await self.inner.handle_async_request(request)
        if self.drop_response:
            self.drop_response = False
            await response.aread()
            raise httpx.ReadError(
                "The test transport discarded the response.",
                request=request,
            )
        return response

    async def aclose(self) -> None:
        await self.inner.aclose()


class _AsyncChunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b" " * 2048
        yield b" " * 2048
        yield b" " * 16


class _FailingRateLimiter(InMemoryReservationRateLimiter):
    def consume(self, action: str, key: str) -> int | None:
        raise RuntimeError("private limiter exception detail")


class ReservationHTTPContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "http.sqlite3"
        self.assertNotEqual(self.database_path.resolve(), DB_PATH)
        initialize_database(self.database_path)
        self.match_id = self.create_match()
        self.now = _NOW
        self.limiter = InMemoryReservationRateLimiter(
            create_policy=RateLimitPolicy(100, 60),
            recovery_policy=RateLimitPolicy(100, 60),
        )
        self.app = self.make_app(self.database_path)

    async def asyncSetUp(self) -> None:
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def make_app(self, database_path: Path) -> FastAPI:
        app = FastAPI()
        configure_reservation_http(
            app,
            enabled=True,
            settings=ReservationHTTPSettings(
                db_path=database_path,
                clock=lambda: self.now,
                limiter=self.limiter,
                allowed_origins=(_ALLOWED_ORIGIN,),
            ),
        )
        return app

    def create_match(self, *, max_players: int = 10) -> int:
        connection = connect(self.database_path)
        try:
            cursor = connection.execute(
                """
                INSERT INTO matches (
                    match_date, match_time, max_players, price_per_slot_cents
                )
                VALUES ('2030-07-14', '19:30', ?, 4321)
                """,
                (max_players,),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def add_confirmed_bookings(self, count: int) -> None:
        connection = connect(self.database_path)
        try:
            connection.executemany(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status,
                    payment_status, confirmed_at
                )
                VALUES (?, ?, ?, 'confirmada', 'aprovado', '2030-07-14T20:00:00Z')
                """,
                [
                    (self.match_id, "Fixture " + str(index), "1190000" + f"{index:04d}")
                    for index in range(count)
                ],
            )
            connection.commit()
        finally:
            connection.close()

    def booking_count(self) -> int:
        connection = connect(self.database_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookings WHERE match_id = ?",
                (self.match_id,),
            ).fetchone()[0]
        finally:
            connection.close()

    def operation_count(self) -> int:
        connection = connect(self.database_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM reservation_operations"
            ).fetchone()[0]
        finally:
            connection.close()

    @staticmethod
    def credentials() -> tuple[str, str]:
        generated = generate_reservation_credentials()
        return generated.idempotency_key, generated.recovery_secret

    def headers(
        self,
        idempotency_key: str,
        recovery_secret: str,
    ) -> dict[str, str]:
        return {
            "Idempotency-Key": idempotency_key,
            "Authorization": "Bearer " + recovery_secret,
        }

    def payload(self, **overrides: object) -> dict[str, object]:
        result: dict[str, object] = {
            "match_id": self.match_id,
            "player_name": _PII_MARKERS[0],
            "player_phone": _PII_MARKERS[1],
        }
        result.update(overrides)
        return result

    async def create(
        self,
        *,
        idempotency_key: str | None = None,
        recovery_secret: str | None = None,
        payload: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if idempotency_key is None or recovery_secret is None:
            idempotency_key, recovery_secret = self.credentials()
        request_headers = self.headers(idempotency_key, recovery_secret)
        if headers is not None:
            request_headers.update(headers)
        body = self.payload() if payload is None else payload
        return await self.client.post(
            CREATE_RESERVATION_PATH,
            headers=request_headers,
            json=body,
        )

    async def test_creation_replay_and_response_privacy(self) -> None:
        key, secret = self.credentials()
        first = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
            payload=self.payload(
                player_name="  Pessoa Privada  ",
                player_phone="(11) 98765-4321",
            ),
        )
        replay = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(
            first.json(),
            {
                "status": "bloqueada",
                "locked_until": "2030-07-14T21:10:00.123456Z",
                "price_per_slot_cents": 4321,
            },
        )
        self.assertEqual(self.booking_count(), 1)
        self.assertEqual(self.operation_count(), 1)
        for response in (first, replay):
            self.assertEqual(response.headers["cache-control"], "no-store")
            for value in (*_PII_MARKERS, key, secret):
                self.assertNotIn(value, response.text)

    async def test_lost_response_can_be_replayed_with_original_credentials(self) -> None:
        key, secret = self.credentials()
        transport = _DropFirstResponseTransport(
            httpx.ASGITransport(app=self.app)
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as disconnected_client:
            with self.assertRaises(httpx.ReadError):
                await disconnected_client.post(
                    CREATE_RESERVATION_PATH,
                    headers=self.headers(key, secret),
                    json=self.payload(),
                )

        self.now += timedelta(minutes=5)
        replay = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(
            replay.json()["locked_until"],
            "2030-07-14T21:10:00.123456Z",
        )
        self.assertEqual(self.booking_count(), 1)
        self.assertEqual(self.operation_count(), 1)

    async def test_used_key_with_invalid_or_different_payload_is_conflict(self) -> None:
        key, secret = self.credentials()
        self.assertEqual(
            (await self.create(idempotency_key=key, recovery_secret=secret)).status_code,
            200,
        )

        invalid_payloads = (
            {"match_id": self.match_id, "player_name": 17, "player_phone": "invalid"},
            {"extra": "value"},
            ["not", "an", "object"],
        )
        for payload in invalid_payloads:
            response = await self.create(
                idempotency_key=key,
                recovery_secret=secret,
                payload=payload,
            )
            self.assertEqual(response.status_code, 409)
            self.assertEqual(
                response.json(),
                {"error": {"code": "idempotency_conflict"}},
            )
            self.assertEqual(response.headers["cache-control"], "no-store")
        malformed = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Content-Type": "application/json",
            },
            content=b'{"private-marker":',
        )
        self.assertEqual(malformed.status_code, 409)
        self.assertEqual(
            malformed.json(),
            {"error": {"code": "idempotency_conflict"}},
        )
        self.assertEqual(self.booking_count(), 1)
        self.assertEqual(self.operation_count(), 1)

    async def test_conflicting_recovery_secret_is_not_accepted(self) -> None:
        key, secret = self.credentials()
        await self.create(idempotency_key=key, recovery_secret=secret)
        _, different_secret = self.credentials()

        response = await self.create(
            idempotency_key=key,
            recovery_secret=different_secret,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"error": {"code": "idempotency_conflict"}},
        )
        self.assertEqual(self.booking_count(), 1)

    async def test_recovery_failures_are_generic_and_success_has_no_sensitive_data(
        self,
    ) -> None:
        key, secret = self.credentials()
        await self.create(idempotency_key=key, recovery_secret=secret)
        _, wrong_secret = self.credentials()

        responses = [
            await self.client.get(RECOVER_RESERVATION_PATH),
            await self.client.get(
                RECOVER_RESERVATION_PATH,
                headers={"Authorization": "Bearer invalid"},
            ),
            await self.client.get(
                RECOVER_RESERVATION_PATH,
                headers={"Authorization": "Bearer " + wrong_secret},
            ),
        ]
        self.assertEqual([response.status_code for response in responses], [404] * 3)
        self.assertEqual(len({response.text for response in responses}), 1)
        for response in responses:
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn(secret, response.text)
            self.assertNotIn(wrong_secret, response.text)

        recovered = await self.client.get(
            RECOVER_RESERVATION_PATH,
            headers={"Authorization": "Bearer " + secret},
        )
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(
            set(recovered.json()),
            {"status", "locked_until", "price_per_slot_cents"},
        )
        self.assertEqual(recovered.headers["cache-control"], "no-store")
        for value in (*_PII_MARKERS, key, secret):
            self.assertNotIn(value, recovered.text)

    async def test_retry_and_recovery_report_expiration_without_relocking(self) -> None:
        key, secret = self.credentials()
        first = await self.create(idempotency_key=key, recovery_secret=secret)
        self.assertEqual(first.status_code, 200)
        self.now += timedelta(minutes=11)

        retry = await self.create(idempotency_key=key, recovery_secret=secret)
        recovered = await self.client.get(
            RECOVER_RESERVATION_PATH,
            headers={"Authorization": "Bearer " + secret},
        )
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(retry.json()["status"], "expirada")
        self.assertEqual(recovered.json()["status"], "expirada")
        self.assertEqual(
            retry.json()["locked_until"],
            first.json()["locked_until"],
        )
        self.assertEqual(self.booking_count(), 1)
        self.assertEqual(self.operation_count(), 1)

    async def test_concurrent_same_key_creates_exactly_one_reservation(self) -> None:
        key, secret = self.credentials()
        responses = await asyncio.gather(
            self.create(idempotency_key=key, recovery_secret=secret),
            self.create(idempotency_key=key, recovery_secret=secret),
        )

        self.assertEqual([response.status_code for response in responses], [200, 200])
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(self.booking_count(), 1)
        self.assertEqual(self.operation_count(), 1)

    async def test_concurrent_different_keys_compete_for_last_vacancy(self) -> None:
        self.add_confirmed_bookings(9)
        credentials = [self.credentials(), self.credentials()]
        responses = await asyncio.gather(
            *(
                self.create(
                    idempotency_key=key,
                    recovery_secret=secret,
                )
                for key, secret in credentials
            )
        )

        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        failure = next(response for response in responses if response.status_code == 409)
        self.assertEqual(failure.json(), {"error": {"code": "no_vacancy"}})
        self.assertEqual(self.booking_count(), 10)
        self.assertEqual(self.operation_count(), 1)

    async def test_match_and_capacity_errors_have_stable_public_codes(self) -> None:
        key, secret = self.credentials()
        missing_match = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
            payload=self.payload(match_id=999999),
        )
        self.assertEqual(missing_match.status_code, 404)
        self.assertEqual(
            missing_match.json(),
            {"error": {"code": "match_not_found"}},
        )

        connection = connect(self.database_path)
        try:
            connection.execute(
                "UPDATE matches SET status = 'cancelada' WHERE id = ?",
                (self.match_id,),
            )
            connection.commit()
        finally:
            connection.close()
        key, secret = self.credentials()
        unavailable = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )
        self.assertEqual(unavailable.status_code, 409)
        self.assertEqual(
            unavailable.json(),
            {"error": {"code": "match_unavailable"}},
        )

    async def test_busy_database_returns_retry_after_without_sqlite_details(self) -> None:
        blocker = sqlite3.connect(self.database_path, timeout=0)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            key, secret = self.credentials()
            response = await self.create(
                idempotency_key=key,
                recovery_secret=secret,
            )
        finally:
            if blocker.in_transaction:
                blocker.rollback()
            blocker.close()

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "1")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"error": {"code": "service_unavailable"}})
        self.assertNotIn("sqlite", response.text.casefold())

    async def test_event_loop_stays_responsive_while_sqlite_waits_for_lock(self) -> None:
        blocker = sqlite3.connect(self.database_path, timeout=0)
        blocker.execute("BEGIN IMMEDIATE")
        key, secret = self.credentials()
        request_task = asyncio.create_task(
            self.create(idempotency_key=key, recovery_secret=secret)
        )
        loop = asyncio.get_running_loop()
        heartbeat = asyncio.Event()
        timer = threading.Timer(
            0.1,
            lambda: loop.call_soon_threadsafe(heartbeat.set),
        )
        timer.start()
        started = time.monotonic()
        timed_out = False
        try:
            await asyncio.wait_for(heartbeat.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            timed_out = True
        finally:
            timer.cancel()
            if blocker.in_transaction:
                blocker.rollback()
            blocker.close()
            timer.join(timeout=1)

        response = await request_task
        self.assertFalse(timed_out)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(response.status_code, 200)

    async def test_v2_database_is_rejected_without_modification_or_migration(self) -> None:
        v2_path = Path(self.temporary_directory.name) / "valid-v2.sqlite3"
        create_valid_v2_database(v2_path)
        before = snapshot_database(v2_path)
        self.app.state.reservation_http_settings = ReservationHTTPSettings(
            db_path=v2_path,
            clock=lambda: self.now,
            limiter=self.limiter,
            allowed_origins=(_ALLOWED_ORIGIN,),
        )

        key, secret = self.credentials()
        create_response = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )
        recovery_response = await self.client.get(
            RECOVER_RESERVATION_PATH,
            headers={"Authorization": "Bearer " + secret},
        )
        self.assertEqual(create_response.status_code, 503)
        self.assertEqual(recovery_response.status_code, 503)
        self.assertEqual(
            create_response.json(),
            {"error": {"code": "service_unavailable"}},
        )
        self.assertEqual(create_response.headers["cache-control"], "no-store")
        self.assertEqual(recovery_response.headers["cache-control"], "no-store")
        assert_v2_database_unchanged(self, v2_path, before)

    async def test_validation_body_and_header_limits_are_enforced(self) -> None:
        key, secret = self.credentials()
        for invalid_payload in (
            self.payload(player_name="x" * 121),
            self.payload(player_phone="1" * 25),
            self.payload(match_id=True),
            {**self.payload(), "extra": "not accepted"},
        ):
            response = await self.create(
                idempotency_key=key,
                recovery_secret=secret,
                payload=invalid_payload,
            )
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.json(), {"error": {"code": "invalid_request"}})
            self.assertEqual(response.headers["cache-control"], "no-store")
            key, secret = self.credentials()

        key, secret = self.credentials()
        response = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers=self.headers(key, secret),
            content=b"x" * 4097,
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["cache-control"], "no-store")

        key, secret = self.credentials()
        response = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Content-Type": "application/json",
            },
            content=_AsyncChunks(),
        )
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.headers["cache-control"], "no-store")

        key, secret = self.credentials()
        enormous_content_length = httpx.Request(
            "POST",
            "http://testserver" + CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Content-Type": "application/json",
                "Content-Length": "9" * 5000,
            },
            content=b"",
        )
        huge_length_response = await self.client.send(enormous_content_length)
        self.assertEqual(huge_length_response.status_code, 413)

        key, secret = self.credentials()
        oversized_request = httpx.Request(
            "POST",
            "http://testserver" + CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Content-Type": "application/json",
                "Content-Length": "1",
            },
            content=b"x" * 4097,
        )
        mismatch_response = await self.client.send(oversized_request)
        self.assertEqual(mismatch_response.status_code, 413)

        key, secret = self.credentials()
        too_long_key = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Idempotency-Key": "x" * 44,
            },
            json=self.payload(),
        )
        self.assertEqual(too_long_key.status_code, 422)

        key, secret = self.credentials()
        too_long_authorization = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
            headers={"Authorization": "Bearer " + "x" * 44},
        )
        self.assertEqual(too_long_authorization.status_code, 422)
        self.assertEqual(
            too_long_authorization.headers["cache-control"],
            "no-store",
        )
        self.assertEqual(self.booking_count(), 0)

        key, secret = self.credentials()
        large_headers = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "X-Fill": "x" * 9000,
            },
            json=self.payload(),
        )
        self.assertEqual(large_headers.status_code, 431)
        self.assertEqual(large_headers.headers["cache-control"], "no-store")

    async def test_existing_key_with_oversized_authorization_still_conflicts(self) -> None:
        key, secret = self.credentials()
        await self.create(idempotency_key=key, recovery_secret=secret)
        response = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
            headers={"Authorization": "Bearer " + "x" * 44},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"error": {"code": "idempotency_conflict"}},
        )

    async def test_content_type_and_malformed_payload_errors_do_not_echo_input(self) -> None:
        key, secret = self.credentials()
        wrong_type = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers=self.headers(key, secret),
            content=b"private-marker",
        )
        self.assertEqual(wrong_type.status_code, 415)
        self.assertEqual(wrong_type.json(), {"error": {"code": "invalid_request"}})
        self.assertEqual(wrong_type.headers["cache-control"], "no-store")
        self.assertNotIn("private-marker", wrong_type.text)

        key, secret = self.credentials()
        malformed = await self.client.post(
            CREATE_RESERVATION_PATH,
            headers={
                **self.headers(key, secret),
                "Content-Type": "application/json",
            },
            content=b'{"private-marker":',
        )
        self.assertEqual(malformed.status_code, 422)
        self.assertEqual(malformed.json(), {"error": {"code": "invalid_request"}})
        self.assertEqual(malformed.headers["cache-control"], "no-store")
        self.assertNotIn("private-marker", malformed.text)

    async def test_origin_policy_and_preflight_are_explicit(self) -> None:
        key, secret = self.credentials()
        allowed = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
            headers={"Origin": _ALLOWED_ORIGIN},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(
            allowed.headers["access-control-allow-origin"],
            _ALLOWED_ORIGIN,
        )
        self.assertIn("origin", allowed.headers["vary"].casefold())

        for origin in ("https://attacker.example", "null"):
            key, secret = self.credentials()
            denied = await self.create(
                idempotency_key=key,
                recovery_secret=secret,
                headers={"Origin": origin},
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.headers["cache-control"], "no-store")
            self.assertEqual(self.booking_count(), 1)

        preflight = await self.client.options(
            CREATE_RESERVATION_PATH,
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": (
                    "authorization,content-type,idempotency-key"
                ),
            },
        )
        self.assertEqual(preflight.status_code, 204)
        self.assertEqual(
            preflight.headers["access-control-allow-origin"],
            _ALLOWED_ORIGIN,
        )
        self.assertEqual(preflight.headers["cache-control"], "no-store")

        denied_preflight = await self.client.options(
            CREATE_RESERVATION_PATH,
            headers={
                "Origin": _ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "x-unapproved",
            },
        )
        self.assertEqual(denied_preflight.status_code, 403)

    async def test_rate_limit_has_retry_after_and_is_separate_by_action(self) -> None:
        monotonic = [100.0]
        limited = InMemoryReservationRateLimiter(
            create_policy=RateLimitPolicy(1, 60),
            recovery_policy=RateLimitPolicy(1, 60),
            monotonic_clock=lambda: monotonic[0],
        )
        self.app.state.reservation_http_settings = ReservationHTTPSettings(
            db_path=self.database_path,
            clock=lambda: self.now,
            limiter=limited,
            allowed_origins=(_ALLOWED_ORIGIN,),
        )

        key, secret = self.credentials()
        recovery_secret = secret
        created = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )
        self.assertEqual(created.status_code, 200)
        key, secret = self.credentials()
        limited_create = await self.create(
            idempotency_key=key,
            recovery_secret=secret,
        )
        self.assertEqual(limited_create.status_code, 429)
        self.assertEqual(limited_create.headers["retry-after"], "60")
        self.assertEqual(limited_create.headers["cache-control"], "no-store")

        recovered = await self.client.get(
            RECOVER_RESERVATION_PATH,
            headers={"Authorization": "Bearer " + recovery_secret},
        )
        self.assertEqual(recovered.status_code, 200)

    async def test_unexpected_error_is_generic_and_never_cached(self) -> None:
        def broken_clock() -> datetime:
            raise RuntimeError("private implementation detail")

        self.app.state.reservation_http_settings = ReservationHTTPSettings(
            db_path=self.database_path,
            clock=broken_clock,
            limiter=self.limiter,
            allowed_origins=(_ALLOWED_ORIGIN,),
        )
        key, secret = self.credentials()
        payload = self.payload(
            player_name="Private Log Name",
            player_phone="11912345678",
        )
        body = json.dumps(payload, separators=(",", ":"))
        headers = self.headers(key, secret)
        headers["Content-Type"] = "application/json"
        with self.assertLogs("reservations_api", level="ERROR") as service_logs:
            response = await self.client.post(
                CREATE_RESERVATION_PATH,
                headers=headers,
                content=body,
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"error": {"code": "internal_error"}})
        self.assertNotIn("private implementation detail", response.text)
        self.assertNotIn(secret, response.text)
        service_log_text = "\n".join(service_logs.output)
        for value in (
            body,
            headers["Authorization"],
            key,
            secret,
            "Private Log Name",
            "11912345678",
            "private implementation detail",
        ):
            self.assertNotIn(value, service_log_text)

        self.app.state.reservation_http_settings = ReservationHTTPSettings(
            db_path=self.database_path,
            clock=lambda: self.now,
            limiter=_FailingRateLimiter(),
            allowed_origins=(_ALLOWED_ORIGIN,),
        )
        boundary_key, boundary_secret = self.credentials()
        boundary_headers = self.headers(boundary_key, boundary_secret)
        boundary_headers["Content-Type"] = "application/json"
        with self.assertLogs("reservations_api", level="ERROR") as boundary_logs:
            boundary_response = await self.client.post(
                CREATE_RESERVATION_PATH,
                headers=boundary_headers,
                content=body,
            )
        self.assertEqual(boundary_response.status_code, 500)
        self.assertEqual(
            boundary_response.json(),
            {"error": {"code": "internal_error"}},
        )
        self.assertEqual(boundary_response.headers["cache-control"], "no-store")
        boundary_log_text = "\n".join(boundary_logs.output)
        for value in (
            body,
            boundary_headers["Authorization"],
            boundary_key,
            boundary_secret,
            "Private Log Name",
            "11912345678",
            "private limiter exception detail",
        ):
            self.assertNotIn(value, boundary_log_text)

    async def test_routes_are_disabled_by_default(self) -> None:
        app = FastAPI()
        configure_reservation_http(app)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(CREATE_RESERVATION_PATH, json={})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(reservation_http_enabled(None))
        self.assertFalse(reservation_http_enabled("true"))
        self.assertTrue(reservation_http_enabled("1"))

    async def test_main_app_flag_registers_routes_and_import_startup_do_not_open_sqlite(
        self,
    ) -> None:
        original_main = sys.modules.pop("main", None)
        feature_flag = "PELADA_RESERVATION_HTTP_ENABLED"
        original_flag = os.environ.get(feature_flag)
        injected_settings = ReservationHTTPSettings(
            db_path=self.database_path,
            clock=lambda: self.now,
            limiter=self.limiter,
            allowed_origins=(_ALLOWED_ORIGIN,),
        )

        def registered_reservation_routes(app: FastAPI) -> set[tuple[str, str]]:
            registered: set[tuple[str, str]] = set()
            pending_routes = list(app.routes)
            while pending_routes:
                route = pending_routes.pop()
                path = getattr(route, "path", None)
                if path in RESERVATION_PATHS:
                    registered.update(
                        (path, method)
                        for method in getattr(route, "methods", set())
                    )
                nested_router = getattr(route, "original_router", None)
                if nested_router is not None:
                    pending_routes.extend(nested_router.routes)
            return registered

        try:
            with mock.patch(
                "sqlite3.connect",
                side_effect=AssertionError("SQLite opened during import or startup."),
            ) as sqlite_connect:
                with mock.patch.dict(os.environ, clear=False):
                    os.environ.pop(feature_flag, None)
                    app_module = importlib.import_module("main")
                    self.assertEqual(registered_reservation_routes(app_module.app), set())
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app_module.app),
                        base_url="http://testserver",
                    ) as capability_client:
                        capability_response = await capability_client.get(
                            "/api/ui-capabilities"
                        )
                    self.assertEqual(capability_response.status_code, 200)
                    self.assertEqual(
                        capability_response.json(),
                        {"reservation_http_enabled": False},
                    )
                    self.assertEqual(
                        capability_response.headers["cache-control"],
                        "no-store",
                    )
                    async with app_module.app.router.lifespan_context(
                        app_module.app
                    ):
                        self.assertEqual(
                            registered_reservation_routes(app_module.app),
                            set(),
                        )

                    os.environ[feature_flag] = "1"
                    with mock.patch.object(
                        ReservationHTTPSettings,
                        "from_environment",
                        return_value=injected_settings,
                    ):
                        app_module = importlib.reload(app_module)
                    self.assertEqual(
                        registered_reservation_routes(app_module.app),
                        {
                            ("/api/v2/reservas", "POST"),
                            ("/api/v2/reservas/recuperacao", "GET"),
                        },
                    )
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app_module.app),
                        base_url="http://testserver",
                    ) as capability_client:
                        capability_response = await capability_client.get(
                            "/api/ui-capabilities"
                        )
                    self.assertEqual(capability_response.status_code, 200)
                    self.assertEqual(
                        capability_response.json(),
                        {"reservation_http_enabled": True},
                    )
                    self.assertEqual(
                        capability_response.headers["cache-control"],
                        "no-store",
                    )
                    async with app_module.app.router.lifespan_context(
                        app_module.app
                    ):
                        self.assertEqual(
                            registered_reservation_routes(app_module.app),
                            {
                                ("/api/v2/reservas", "POST"),
                                ("/api/v2/reservas/recuperacao", "GET"),
                            },
                        )
                sqlite_connect.assert_not_called()
        finally:
            sys.modules.pop("main", None)
            if original_main is not None:
                sys.modules["main"] = original_main
            if original_flag is None:
                os.environ.pop(feature_flag, None)
            else:
                os.environ[feature_flag] = original_flag


class ReservationRateLimiterTests(unittest.TestCase):
    def test_expiration_policy_separation_and_memory_bound(self) -> None:
        now = [0.0]
        limiter = InMemoryReservationRateLimiter(
            create_policy=RateLimitPolicy(1, 10),
            recovery_policy=RateLimitPolicy(1, 20),
            max_entries=2,
            monotonic_clock=lambda: now[0],
        )
        self.assertIsNone(limiter.consume("create", "client"))
        self.assertEqual(limiter.consume("create", "client"), 10)
        self.assertIsNone(limiter.consume("recover", "client"))

        now[0] = 10.1
        self.assertIsNone(limiter.consume("create", "other"))
        self.assertLessEqual(limiter.entry_count, 2)
        self.assertEqual(limiter.consume("recover", "client"), 10)

        limiter.consume("create", "third")
        limiter.consume("create", "fourth")
        self.assertLessEqual(limiter.entry_count, 2)

    def test_concurrent_consumption_is_thread_safe(self) -> None:
        limiter = InMemoryReservationRateLimiter(
            create_policy=RateLimitPolicy(4, 60),
        )
        with ThreadPoolExecutor(max_workers=12) as executor:
            results = list(
                executor.map(
                    lambda _: limiter.consume("create", "same-client"),
                    range(24),
                )
            )

        self.assertEqual(sum(result is None for result in results), 4)
        self.assertEqual(limiter.entry_count, 1)


if __name__ == "__main__":
    unittest.main()
