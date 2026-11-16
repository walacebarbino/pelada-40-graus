from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Callable
from unittest import mock

import reservations
from admin import (
    MatchCreateRequest,
    MatchDeleteConflictError,
    MatchEditConflictError,
    delete_match,
    update_match,
)
from queries import list_confirmed_participant_names, list_matches
from reservations import (
    InvalidReservationDataError,
    MatchAlreadyStartedError,
    MatchNotFoundError,
    MatchNotReservableError,
    NoVacancyError,
    ReservationDatabaseError,
    ReservationIdempotencyConflictError,
    ReservationDatabaseBusyError,
    ReservationNotFoundError,
    create_temporary_reservation,
    generate_reservation_credentials,
    recover_temporary_reservation,
)
from storage import DB_PATH, connect, initialize_database
from v2_fixture import (
    assert_v2_database_unchanged,
    create_valid_v2_database,
    snapshot_database,
)


class ReservationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "reservations.sqlite3"
        self.assertNotEqual(self.database_path.resolve(), DB_PATH)
        initialize_database(self.database_path)
        self.now = datetime(2030, 7, 14, 21, 0, 0, 123456, tzinfo=timezone.utc)
        self.match_id = self.create_match()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_match(
        self,
        *,
        status: str = "agendada",
        max_players: int = 10,
        price_per_slot_cents: int = 4321,
        match_date: str = "2030-07-14",
        match_time: str = "19:30",
    ) -> int:
        connection = connect(self.database_path)
        try:
            cursor = connection.execute(
                """
                INSERT INTO matches (
                    match_date, match_time, max_players,
                    price_per_slot_cents, status
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (match_date, match_time, max_players, price_per_slot_cents, status),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def add_booking(
        self,
        match_id: int,
        *,
        status: str,
        locked_until: str | None = None,
        player_name: str = "Jogador Fictício",
    ) -> int:
        connection = connect(self.database_path)
        try:
            if status == "confirmada":
                cursor = connection.execute(
                    """
                    INSERT INTO bookings (
                        match_id, player_name, player_phone, status,
                        payment_status, confirmed_at
                    )
                    VALUES (?, ?, '11999999999', 'confirmada', 'aprovado',
                            '2030-07-14T20:00:00Z')
                    """,
                    (match_id, player_name),
                )
            elif status == "bloqueada":
                if locked_until is None:
                    raise ValueError("A blocked booking requires a lock timestamp.")
                cursor = connection.execute(
                    """
                    INSERT INTO bookings (
                        match_id, player_name, player_phone, status, locked_until,
                        payment_status
                    )
                    VALUES (?, ?, '11999999999', 'bloqueada', ?, 'nao_iniciado')
                    """,
                    (match_id, player_name, locked_until),
                )
            else:
                raise ValueError("Unsupported test booking status.")
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def read_booking(self, reservation_id: int) -> sqlite3.Row:
        connection = connect(self.database_path)
        try:
            row = connection.execute(
                "SELECT * FROM bookings WHERE id = ?",
                (reservation_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            return row
        finally:
            connection.close()

    def booking_count(self, match_id: int) -> int:
        connection = connect(self.database_path)
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM bookings WHERE match_id = ?",
                (match_id,),
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

    def reserve(
        self,
        match_id: int | None = None,
        *,
        player_name: str = "  Jogador Fictício  ",
        player_phone: str = "(11) 99999-9999",
        idempotency_key: str | None = None,
        recovery_secret: str | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        if idempotency_key is None and recovery_secret is None:
            credentials = generate_reservation_credentials()
            idempotency_key = credentials.idempotency_key
            recovery_secret = credentials.recovery_secret
        if idempotency_key is None or recovery_secret is None:
            raise ValueError("Both test credentials must be provided together.")
        return create_temporary_reservation(
            self.match_id if match_id is None else match_id,
            player_name,
            player_phone,
            idempotency_key=idempotency_key,
            recovery_secret=recovery_secret,
            db_path=self.database_path,
            clock=clock if clock is not None else lambda: self.now,
        )

    def test_creation_uses_server_clock_database_price_and_unpaid_state(self) -> None:
        result = self.reserve()
        booking = self.read_booking(result.reservation_id)

        self.assertEqual(result.match_id, self.match_id)
        self.assertEqual(result.price_per_slot_cents, 4321)
        self.assertEqual(
            result.locked_until,
            "2030-07-14T21:10:00.123456Z",
        )
        self.assertEqual(booking["player_name"], "Jogador Fictício")
        self.assertEqual(booking["player_phone"], "11999999999")
        self.assertEqual(booking["status"], "bloqueada")
        self.assertEqual(booking["locked_until"], result.locked_until)
        self.assertEqual(booking["payment_status"], "nao_iniciado")
        self.assertIsNone(booking["payment_ref"])
        self.assertFalse(hasattr(result, "player_name"))
        self.assertFalse(hasattr(result, "player_phone"))
        self.assertEqual(self.operation_count(), 1)

    def test_generated_credentials_are_independent_256_bit_urlsafe_values(self) -> None:
        credentials = generate_reservation_credentials()

        self.assertEqual(len(credentials.idempotency_key), 43)
        self.assertEqual(len(credentials.recovery_secret), 43)
        self.assertNotEqual(
            credentials.idempotency_key,
            credentials.recovery_secret,
        )
        self.assertNotIn(credentials.idempotency_key, repr(credentials))
        self.assertNotIn(credentials.recovery_secret, repr(credentials))
        self.assertRegex(credentials.idempotency_key, r"^[A-Za-z0-9_-]{43}$")
        self.assertRegex(credentials.recovery_secret, r"^[A-Za-z0-9_-]{43}$")

    def test_invalid_or_reused_credential_is_rejected_without_inserting(self) -> None:
        credentials = generate_reservation_credentials()
        invalid_pairs = (
            ("short", credentials.recovery_secret),
            (credentials.idempotency_key, "not-a-secret"),
            (credentials.idempotency_key, credentials.idempotency_key),
        )
        for idempotency_key, recovery_secret in invalid_pairs:
            with self.subTest(idempotency_key_length=len(idempotency_key)):
                with self.assertRaises(InvalidReservationDataError):
                    self.reserve(
                        idempotency_key=idempotency_key,
                        recovery_secret=recovery_secret,
                    )
        self.assertEqual(self.booking_count(self.match_id), 0)
        self.assertEqual(self.operation_count(), 0)

    def test_equivalent_retry_reuses_booking_and_original_deadline(self) -> None:
        credentials = generate_reservation_credentials()
        first = self.reserve(
            player_name=" Jogador Á ",
            player_phone="(11) 99999-9999",
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        retry = self.reserve(
            player_name="Jogador A\u0301",
            player_phone="+55 (11) 99999-9999",
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )

        self.assertEqual(retry, first)
        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.operation_count(), 1)

    def test_same_idempotency_key_with_different_request_or_secret_conflicts(self) -> None:
        credentials = generate_reservation_credentials()
        first = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        with self.assertRaises(ReservationIdempotencyConflictError) as changed_request:
            self.reserve(
                player_name="Outra pessoa",
                idempotency_key=credentials.idempotency_key,
                recovery_secret=credentials.recovery_secret,
            )
        other_credentials = generate_reservation_credentials()
        with self.assertRaises(ReservationIdempotencyConflictError) as changed_secret:
            self.reserve(
                idempotency_key=credentials.idempotency_key,
                recovery_secret=other_credentials.recovery_secret,
            )
        with self.assertRaises(ReservationIdempotencyConflictError):
            self.reserve(
                player_name="Jogador\ninválido",
                idempotency_key=credentials.idempotency_key,
                recovery_secret=credentials.recovery_secret,
            )
        with self.assertRaises(ReservationIdempotencyConflictError):
            self.reserve(
                idempotency_key=credentials.idempotency_key,
                recovery_secret="invalid",
            )

        self.assertEqual(str(changed_request.exception), str(changed_secret.exception))
        self.assertNotIn("Jogador", str(changed_request.exception))
        self.assertNotIn("11999999999", str(changed_request.exception))
        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.operation_count(), 1)
        self.assertEqual(self.read_booking(first.reservation_id)["locked_until"], first.locked_until)

    def test_retry_after_expiration_returns_original_without_occupying_capacity(self) -> None:
        credentials = generate_reservation_credentials()
        first = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        after_expiration = self.now + timedelta(minutes=11)

        retry = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
            clock=lambda: after_expiration,
        )
        listed_match = list_matches(
            self.database_path,
            now=after_expiration,
        )[0]

        self.assertEqual(retry.reservation_id, first.reservation_id)
        self.assertEqual(retry.locked_until, first.locked_until)
        self.assertEqual(retry.status, "expirada")
        self.assertEqual(listed_match["vagas_livres"], 10)
        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.read_booking(first.reservation_id)["status"], "bloqueada")
        recovered = recover_temporary_reservation(
            credentials.recovery_secret,
            db_path=self.database_path,
            clock=lambda: after_expiration,
        )
        self.assertEqual(recovered.status, "expirada")
        self.assertEqual(recovered.locked_until, first.locked_until)

    def test_original_deadline_ignores_mutable_booking_lock_timestamp(self) -> None:
        credentials = generate_reservation_credentials()
        first = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        connection = connect(self.database_path)
        try:
            connection.execute(
                """
                UPDATE bookings
                SET locked_until = '2099-01-01T00:00:00Z'
                WHERE id = ?
                """,
                (first.reservation_id,),
            )
            connection.commit()
        finally:
            connection.close()

        after_original_deadline = self.now + timedelta(minutes=11)
        retry = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
            clock=lambda: after_original_deadline,
        )
        listed = list_matches(
            self.database_path,
            now=after_original_deadline,
        )[0]

        self.assertEqual(retry.status, "expirada")
        self.assertEqual(retry.locked_until, first.locked_until)
        self.assertEqual(listed["vagas_livres"], 10)

    def test_retry_after_match_is_full_and_started_returns_original(self) -> None:
        credentials = generate_reservation_credentials()
        first = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        for _ in range(9):
            self.add_booking(self.match_id, status="confirmada")
        connection = connect(self.database_path)
        try:
            connection.execute(
                "UPDATE matches SET status = 'em_andamento' WHERE id = ?",
                (self.match_id,),
            )
            connection.commit()
        finally:
            connection.close()

        retry = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
            clock=lambda: datetime(2030, 7, 14, 22, 30, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(retry.reservation_id, first.reservation_id)
        self.assertEqual(retry.locked_until, first.locked_until)
        self.assertEqual(retry.status, "expirada")
        self.assertEqual(self.booking_count(self.match_id), 10)

    def test_recovery_requires_secret_and_returns_no_personal_data(self) -> None:
        credentials = generate_reservation_credentials()
        created = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )

        recovered = recover_temporary_reservation(
            credentials.recovery_secret,
            db_path=self.database_path,
            clock=lambda: self.now,
        )

        self.assertEqual(recovered, created)
        self.assertEqual(
            set(recovered.__dataclass_fields__),
            {
                "reservation_id",
                "match_id",
                "status",
                "locked_until",
                "price_per_slot_cents",
            },
        )
        self.assertFalse(hasattr(recovered, "player_name"))
        self.assertFalse(hasattr(recovered, "player_phone"))
        self.assertNotIn(credentials.recovery_secret, repr(recovered))
        self.assertNotIn(credentials.idempotency_key, repr(recovered))
        wrong_credentials = generate_reservation_credentials()
        with self.assertRaises(ReservationNotFoundError):
            recover_temporary_reservation(
                wrong_credentials.recovery_secret,
                db_path=self.database_path,
                clock=lambda: self.now,
            )

    def test_invalid_recovery_secret_does_not_authorize_by_booking_id(self) -> None:
        with self.assertRaises(InvalidReservationDataError):
            recover_temporary_reservation(
                "1",
                db_path=self.database_path,
                clock=lambda: self.now,
            )

    def test_legacy_booking_has_no_retroactive_recovery_access(self) -> None:
        self.add_booking(
            self.match_id,
            status="bloqueada",
            locked_until="2030-07-14T21:10:00Z",
        )
        credentials = generate_reservation_credentials()

        with self.assertRaises(ReservationNotFoundError):
            recover_temporary_reservation(
                credentials.recovery_secret,
                db_path=self.database_path,
                clock=lambda: self.now,
            )
        self.assertEqual(self.operation_count(), 0)

    def test_credentials_and_fingerprint_are_hashed_and_not_duplicated_as_pii(self) -> None:
        credentials = generate_reservation_credentials()
        result = self.reserve(
            idempotency_key=credentials.idempotency_key,
            recovery_secret=credentials.recovery_secret,
        )
        connection = connect(self.database_path)
        try:
            operation = connection.execute(
                "SELECT * FROM reservation_operations WHERE booking_id = ?",
                (result.reservation_id,),
            ).fetchone()
            column_names = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(reservation_operations)"
                )
            }
        finally:
            connection.close()

        stored_values = tuple(operation)
        self.assertEqual(len(operation["idempotency_hash"]), 32)
        self.assertEqual(len(operation["recovery_hash"]), 32)
        self.assertEqual(len(operation["request_fingerprint"]), 32)
        self.assertNotIn(credentials.idempotency_key.encode(), stored_values)
        self.assertNotIn(credentials.recovery_secret.encode(), stored_values)
        self.assertNotIn("player_name", column_names)
        self.assertNotIn("player_phone", column_names)

    def test_metadata_constraints_enforce_hash_types_lengths_uniqueness_and_immutability(self) -> None:
        first_booking = self.add_booking(self.match_id, status="confirmada")
        second_booking = self.add_booking(self.match_id, status="confirmada")
        connection = connect(self.database_path)
        try:
            hashes = (bytes(range(32)), bytes(range(1, 33)), bytes(range(2, 34)))
            connection.execute(
                """
                INSERT INTO reservation_operations (
                    booking_id, idempotency_hash, recovery_hash,
                    request_fingerprint, locked_until
                )
                VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                """,
                (first_booking, *hashes),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reservation_operations (
                        booking_id, idempotency_hash, recovery_hash,
                        request_fingerprint, locked_until
                    )
                    VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                    """,
                    (second_booking, hashes[0], b"x" * 32, b"y" * 32),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reservation_operations (
                        booking_id, idempotency_hash, recovery_hash,
                        request_fingerprint, locked_until
                    )
                    VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                    """,
                    (first_booking, b"f" * 32, b"g" * 32, b"h" * 32),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reservation_operations (
                        booking_id, idempotency_hash, recovery_hash,
                        request_fingerprint, locked_until
                    )
                    VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                    """,
                    (second_booking, b"a" * 32, hashes[1], b"y" * 32),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO reservation_operations (
                        booking_id, idempotency_hash, recovery_hash,
                        request_fingerprint, locked_until
                    )
                    VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                    """,
                    (second_booking, "not-a-blob", b"b" * 32, b"y" * 32),
                )
            for column in (
                "idempotency_hash",
                "recovery_hash",
                "request_fingerprint",
            ):
                for invalid_value in (b"z" * 31, "z" * 32):
                    with self.subTest(column=column, invalid_type=type(invalid_value).__name__):
                        values = {
                            "idempotency_hash": b"c" * 32,
                            "recovery_hash": b"d" * 32,
                            "request_fingerprint": b"e" * 32,
                        }
                        values[column] = invalid_value
                        with self.assertRaises(sqlite3.IntegrityError):
                            connection.execute(
                                """
                                INSERT INTO reservation_operations (
                                    booking_id, idempotency_hash, recovery_hash,
                                    request_fingerprint, locked_until
                                )
                                VALUES (?, ?, ?, ?, '2030-07-14T21:10:00Z')
                                """,
                                (
                                    second_booking,
                                    values["idempotency_hash"],
                                    values["recovery_hash"],
                                    values["request_fingerprint"],
                                ),
                            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    UPDATE reservation_operations
                    SET locked_until = '2030-07-14T22:00:00Z'
                    WHERE booking_id = ?
                    """,
                    (first_booking,),
                )
        finally:
            connection.close()

    def test_metadata_failure_between_booking_and_operation_rolls_back_both(self) -> None:
        with mock.patch(
            "reservations._insert_reservation_metadata",
            side_effect=sqlite3.IntegrityError("injected metadata failure"),
        ):
            with self.assertRaises(ReservationDatabaseError):
                self.reserve()

        self.assertEqual(self.booking_count(self.match_id), 0)
        self.assertEqual(self.operation_count(), 0)

    def test_two_connections_with_same_credentials_create_one_reservation(self) -> None:
        credentials = generate_reservation_credentials()
        barrier = threading.Barrier(2)

        def reserve_after_barrier():
            barrier.wait(timeout=10)
            return create_temporary_reservation(
                self.match_id,
                "Jogador Idempotente",
                "11999999999",
                idempotency_key=credentials.idempotency_key,
                recovery_secret=credentials.recovery_secret,
                db_path=self.database_path,
                clock=lambda: self.now,
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(reserve_after_barrier),
                executor.submit(reserve_after_barrier),
            )
            results = [future.result(timeout=15) for future in futures]

        self.assertEqual(results[0], results[1])
        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.operation_count(), 1)

    def test_temporary_block_reduces_listed_vacancies(self) -> None:
        before = list_matches(
            self.database_path,
            now=self.now,
        )[0]
        self.reserve()
        after = list_matches(
            self.database_path,
            now=self.now,
        )[0]

        self.assertEqual(before["vagas_livres"], 10)
        self.assertEqual(after["vagas_livres"], 9)
        self.assertEqual(after["vagas_ocupadas"], 1)

    def test_expired_block_stops_occupying_but_remains_linked(self) -> None:
        expired_at = (self.now - timedelta(seconds=1)).isoformat().replace(
            "+00:00",
            "Z",
        )
        expired_id = self.add_booking(
            self.match_id,
            status="bloqueada",
            locked_until=expired_at,
        )

        self.assertEqual(
            list_matches(self.database_path, now=self.now)[0]["vagas_livres"],
            10,
        )
        self.reserve()

        self.assertEqual(self.booking_count(self.match_id), 2)
        expired_booking = self.read_booking(expired_id)
        self.assertEqual(expired_booking["status"], "bloqueada")
        self.assertEqual(expired_booking["locked_until"], expired_at)

    def test_block_expiring_exactly_now_no_longer_occupies_capacity(self) -> None:
        exact_expiry = self.now.isoformat().replace("+00:00", "Z")
        self.add_booking(
            self.match_id,
            status="bloqueada",
            locked_until=exact_expiry,
        )

        listed_match = list_matches(
            self.database_path,
            now=self.now,
        )[0]
        result = self.reserve()

        self.assertEqual(listed_match["vagas_livres"], 10)
        self.assertEqual(result.reservation_id, 2)
        self.assertEqual(self.booking_count(self.match_id), 2)

    def test_missing_match_is_rejected_without_inserting(self) -> None:
        with self.assertRaises(MatchNotFoundError):
            self.reserve(987654)

        self.assertEqual(self.booking_count(self.match_id), 0)

    def test_non_scheduled_match_is_rejected_without_inserting(self) -> None:
        match_id = self.create_match(status="em_andamento")

        with self.assertRaises(MatchNotReservableError):
            self.reserve(match_id)

        self.assertEqual(self.booking_count(match_id), 0)

    def test_started_match_is_rejected(self) -> None:
        after_start = datetime(2030, 7, 14, 22, 30, 1, tzinfo=timezone.utc)

        with self.assertRaises(MatchAlreadyStartedError):
            self.reserve(clock=lambda: after_start)

        self.assertEqual(self.booking_count(self.match_id), 0)

    def test_exact_match_start_in_sao_paulo_is_rejected(self) -> None:
        exact_start = datetime(2030, 7, 14, 22, 30, tzinfo=timezone.utc)

        with self.assertRaises(MatchAlreadyStartedError):
            self.reserve(clock=lambda: exact_start)

        self.assertEqual(self.booking_count(self.match_id), 0)

    def test_invalid_name_or_phone_is_rejected_without_inserting(self) -> None:
        invalid_inputs = (
            ("", "11999999999"),
            ("   ", "11999999999"),
            ("N" * 121, "11999999999"),
            ("Jogador\nFictício", "11999999999"),
            ("Jogador Fictício", "119999999"),
            ("Jogador Fictício", "11abc99999999"),
            ("Jogador Fictício", "+1 202 555 0100"),
        )

        for player_name, player_phone in invalid_inputs:
            with self.subTest(player_name=player_name[:20], player_phone=player_phone):
                with self.assertRaises(InvalidReservationDataError):
                    self.reserve(
                        player_name=player_name,
                        player_phone=player_phone,
                    )
                self.assertEqual(self.booking_count(self.match_id), 0)

    def test_full_match_refuses_new_block(self) -> None:
        for _ in range(10):
            self.add_booking(self.match_id, status="confirmada")

        with self.assertRaises(NoVacancyError):
            self.reserve()

        self.assertEqual(self.booking_count(self.match_id), 10)
        self.assertEqual(self.operation_count(), 0)

    def test_capacities_ten_twelve_and_fourteen_are_enforced(self) -> None:
        for capacity in (10, 12, 14):
            with self.subTest(capacity=capacity):
                match_id = self.create_match(max_players=capacity)
                for _ in range(capacity - 1):
                    self.add_booking(match_id, status="confirmada")

                self.reserve(match_id)
                match = next(
                    item
                    for item in list_matches(self.database_path, now=self.now)
                    if item["id"] == match_id
                )
                self.assertEqual(match["vagas_ocupadas"], capacity)
                self.assertEqual(match["vagas_livres"], 0)
                with self.assertRaises(NoVacancyError):
                    self.reserve(match_id)

    def test_blocked_booking_is_not_listed_as_a_confirmed_participant(self) -> None:
        self.add_booking(self.match_id, status="confirmada", player_name="Confirmado")
        self.reserve()

        self.assertEqual(
            list_confirmed_participant_names(self.match_id, self.database_path),
            ["Confirmado"],
        )

    def test_expired_booking_still_blocks_admin_edit_and_delete(self) -> None:
        expired_at = (self.now - timedelta(seconds=1)).isoformat().replace(
            "+00:00",
            "Z",
        )
        self.add_booking(
            self.match_id,
            status="bloqueada",
            locked_until=expired_at,
        )
        updated_match = MatchCreateRequest(
            title="Partida de teste",
            match_date="2030-07-14",
            match_time="19:30",
            location="Quadra de teste",
            duration_minutes=60,
            max_players=10,
            price_per_slot_cents=5000,
        )

        with self.assertRaises(MatchEditConflictError):
            update_match(self.match_id, updated_match, self.database_path)
        with self.assertRaises(MatchDeleteConflictError):
            delete_match(self.match_id, self.database_path)

        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.read_booking(1)["locked_until"], expired_at)

    def test_reservation_preserves_other_matches_and_bookings(self) -> None:
        other_match_id = self.create_match()
        other_booking_id = self.add_booking(
            other_match_id,
            status="confirmada",
            player_name="Outro Jogador",
        )

        self.reserve()

        self.assertEqual(self.booking_count(self.match_id), 1)
        self.assertEqual(self.booking_count(other_match_id), 1)
        other_booking = self.read_booking(other_booking_id)
        self.assertEqual(other_booking["match_id"], other_match_id)
        self.assertEqual(other_booking["player_name"], "Outro Jogador")
        self.assertEqual(other_booking["status"], "confirmada")
        self.assertEqual(len(list_matches(self.database_path, now=self.now)), 2)

    def test_failure_after_insert_rolls_back_without_partial_booking(self) -> None:
        with mock.patch(
            "reservations.TemporaryReservation",
            side_effect=RuntimeError("Injected result construction failure"),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Injected result construction failure",
            ):
                self.reserve()

        self.assertEqual(self.booking_count(self.match_id), 0)
        self.assertEqual(self.operation_count(), 0)

    def test_two_connections_competing_for_last_slot_create_only_one_block(self) -> None:
        for _ in range(9):
            self.add_booking(self.match_id, status="confirmada")
        barrier = threading.Barrier(2)

        def reserve_after_barrier(name: str, idempotency_key: str, recovery_secret: str):
            barrier.wait(timeout=10)
            try:
                result = create_temporary_reservation(
                    self.match_id,
                    name,
                    "11999999999",
                    idempotency_key=idempotency_key,
                    recovery_secret=recovery_secret,
                    db_path=self.database_path,
                    clock=lambda: self.now,
                )
                return ("created", result)
            except NoVacancyError:
                return ("no_vacancy", None)
            except ReservationDatabaseBusyError:
                return ("database_busy", None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            credentials_a = generate_reservation_credentials()
            credentials_b = generate_reservation_credentials()
            futures = (
                executor.submit(
                    reserve_after_barrier,
                    "Jogador Concorrente A",
                    credentials_a.idempotency_key,
                    credentials_a.recovery_secret,
                ),
                executor.submit(
                    reserve_after_barrier,
                    "Jogador Concorrente B",
                    credentials_b.idempotency_key,
                    credentials_b.recovery_secret,
                ),
            )
            outcomes = [future.result(timeout=15)[0] for future in futures]

        self.assertCountEqual(outcomes, ["created", "no_vacancy"])
        self.assertEqual(self.booking_count(self.match_id), 10)
        self.assertEqual(self.operation_count(), 1)
        self.assertEqual(
            list_matches(self.database_path, now=self.now)[0]["vagas_livres"],
            0,
        )

    def test_database_lock_is_a_controlled_error_not_a_no_vacancy_result(self) -> None:
        lock_connection = sqlite3.connect(self.database_path, timeout=1.0)
        try:
            lock_connection.execute("BEGIN IMMEDIATE")
            real_connect = sqlite3.connect

            def short_timeout_connect(*args, **kwargs):
                kwargs["timeout"] = 0.05
                return real_connect(*args, **kwargs)

            with mock.patch(
                "reservations.sqlite3.connect",
                side_effect=short_timeout_connect,
            ):
                with self.assertRaises(ReservationDatabaseBusyError):
                    self.reserve()
        finally:
            if lock_connection.in_transaction:
                lock_connection.rollback()
            lock_connection.close()

    def test_creation_and_recovery_reject_valid_v2_without_modification(self) -> None:
        database_path = create_valid_v2_database(
            Path(self.temporary_directory.name) / "valid-v2.sqlite3"
        )
        before = snapshot_database(database_path)
        credentials = generate_reservation_credentials()

        with self.assertRaises(ReservationDatabaseError):
            create_temporary_reservation(
                41,
                "Jogador de teste",
                "11999999999",
                idempotency_key=credentials.idempotency_key,
                recovery_secret=credentials.recovery_secret,
                db_path=database_path,
                clock=lambda: self.now,
            )
        assert_v2_database_unchanged(self, database_path, before)

        with self.assertRaises(ReservationDatabaseError):
            recover_temporary_reservation(
                credentials.recovery_secret,
                db_path=database_path,
                clock=lambda: self.now,
            )
        assert_v2_database_unchanged(self, database_path, before)

        self.assertEqual(self.booking_count(self.match_id), 0)


if __name__ == "__main__":
    unittest.main()
