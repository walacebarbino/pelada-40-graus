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
    ReservationDatabaseBusyError,
    create_temporary_reservation,
)
from storage import DB_PATH, connect, initialize_database


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

    def reserve(
        self,
        match_id: int | None = None,
        *,
        player_name: str = "  Jogador Fictício  ",
        player_phone: str = "(11) 99999-9999",
        clock: Callable[[], datetime] | None = None,
    ):
        return create_temporary_reservation(
            self.match_id if match_id is None else match_id,
            player_name,
            player_phone,
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

    def test_two_connections_competing_for_last_slot_create_only_one_block(self) -> None:
        for _ in range(9):
            self.add_booking(self.match_id, status="confirmada")
        barrier = threading.Barrier(2)

        def reserve_after_barrier(name: str):
            barrier.wait(timeout=10)
            try:
                result = create_temporary_reservation(
                    self.match_id,
                    name,
                    "11999999999",
                    db_path=self.database_path,
                    clock=lambda: self.now,
                )
                return ("created", result)
            except NoVacancyError:
                return ("no_vacancy", None)
            except ReservationDatabaseBusyError:
                return ("database_busy", None)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(reserve_after_barrier, "Jogador Concorrente A"),
                executor.submit(reserve_after_barrier, "Jogador Concorrente B"),
            )
            outcomes = [future.result(timeout=15)[0] for future in futures]

        self.assertCountEqual(outcomes, ["created", "no_vacancy"])
        self.assertEqual(self.booking_count(self.match_id), 10)
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

        self.assertEqual(self.booking_count(self.match_id), 0)


if __name__ == "__main__":
    unittest.main()
