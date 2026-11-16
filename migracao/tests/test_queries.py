from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
import tempfile
import unittest
from pathlib import Path

from availability import (
    IncompatibleAvailabilitySchemaError,
    occupied_bookings_by_match,
)
from queries import (
    DatabaseReadError,
    MatchNotFoundError,
    list_confirmed_participant_names,
    list_matches,
)
from storage import DB_PATH, connect, initialize_database
from v2_fixture import (
    assert_v2_database_unchanged,
    create_valid_v2_database,
    snapshot_database,
)


class QueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "queries.sqlite3"
        self.assertNotEqual(self.database_path.resolve(), DB_PATH)
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_v2_database(self) -> Path:
        return create_valid_v2_database(
            Path(self.temporary_directory.name) / "valid-v2.sqlite3"
        )

    @contextmanager
    def open_database(self):
        connection = connect(self.database_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def insert_match(
        self,
        connection,
        *,
        date: str,
        time: str,
        price_cents: int = 1500,
        max_players: int = 10,
    ) -> int:
        cursor = connection.execute(
            """
            INSERT INTO matches (
                match_date, match_time, price_per_slot_cents, max_players
            )
            VALUES (?, ?, ?, ?)
            """,
            (date, time, price_cents, max_players),
        )
        return cursor.lastrowid

    def test_empty_database_returns_empty_match_list(self) -> None:
        self.assertEqual(
            list_matches(self.database_path, now=datetime(2030, 1, 1, tzinfo=timezone.utc)),
            [],
        )

    def test_matches_are_sorted_and_prices_remain_in_cents(self) -> None:
        with self.open_database() as connection:
            later_id = self.insert_match(
                connection,
                date="2030-05-02",
                time="19:00",
                price_cents=1875,
            )
            earlier_id = self.insert_match(
                connection,
                date="2030-05-01",
                time="20:00",
                price_cents=2150,
            )

        matches = list_matches(
            self.database_path,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual([match["id"] for match in matches], [earlier_id, later_id])
        self.assertEqual(
            [match["price_per_slot_cents"] for match in matches],
            [2150, 1875],
        )

    def test_availability_counts_confirmed_and_only_unexpired_holds(self) -> None:
        current_time = datetime(2030, 4, 5, 19, 0, tzinfo=timezone.utc)
        with self.open_database() as connection:
            match_id = self.insert_match(
                connection,
                date="2030-04-05",
                time="20:00",
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, payment_status,
                    confirmed_at
                )
                VALUES (?, 'Jogador Confirmado', '11911111111', 'confirmada',
                        'aprovado', '2030-04-05T18:00:00Z')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, locked_until,
                    payment_status
                )
                VALUES (?, 'Bloqueio Válido', '11922222222', 'bloqueada',
                        '2030-04-05T19:10:00Z', 'cobranca_pendente')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, locked_until,
                    payment_status
                )
                VALUES (?, 'Pagamento Cancelado', '11944444444', 'bloqueada',
                        '2030-04-05T19:20:00Z', 'cancelado')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, locked_until,
                    payment_status
                )
                VALUES (?, 'Bloqueio Expirado', '11933333333', 'bloqueada',
                        '2030-04-05T18:59:59Z', 'cobranca_pendente')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (match_id, status)
                VALUES (?, 'expirada')
                """,
                (match_id,),
            )

        match = list_matches(self.database_path, now=current_time)[0]

        self.assertEqual(match["vagas_ocupadas"], 3)
        self.assertEqual(match["vagas_livres"], 7)

    def test_participant_query_returns_only_confirmed_names_in_order(self) -> None:
        with self.open_database() as connection:
            match_id = self.insert_match(
                connection,
                date="2030-06-01",
                time="19:00",
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, payment_status,
                    confirmed_at
                )
                VALUES (?, 'Pessoa A', '11911111111', 'confirmada', 'aprovado',
                        '2030-06-01T18:00:00Z')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, payment_status,
                    confirmed_at
                )
                VALUES (?, 'Pessoa B', '11922222222', 'confirmada', 'aprovado',
                        '2030-06-01T18:01:00Z')
                """,
                (match_id,),
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, locked_until,
                    payment_status
                )
                VALUES (?, 'Ainda Bloqueado', '11933333333', 'bloqueada',
                        '2030-06-01T19:10:00Z', 'cobranca_pendente')
                """,
                (match_id,),
            )

        self.assertEqual(
            list_confirmed_participant_names(match_id, self.database_path),
            ["Pessoa A", "Pessoa B"],
        )

    def test_existing_match_without_confirmed_participants_returns_empty_list(self) -> None:
        with self.open_database() as connection:
            match_id = self.insert_match(
                connection,
                date="2030-06-02",
                time="19:00",
            )

        self.assertEqual(
            list_confirmed_participant_names(match_id, self.database_path),
            [],
        )

    def test_nonexistent_match_raises_not_found(self) -> None:
        with self.assertRaises(MatchNotFoundError):
            list_confirmed_participant_names(987654, self.database_path)

    def test_missing_database_is_an_error_and_is_not_created(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.sqlite3"

        with self.assertRaises(DatabaseReadError):
            list_matches(missing_path)

        self.assertFalse(missing_path.exists())

    def test_incompatible_database_is_an_error(self) -> None:
        incompatible_path = Path(self.temporary_directory.name) / "incompatible.sqlite3"
        connection = connect(incompatible_path)
        try:
            connection.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY)")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(DatabaseReadError):
            list_matches(incompatible_path)

    def test_direct_availability_helper_rejects_v2_before_joining_v3_table(self) -> None:
        database_path = self.create_v2_database()
        before = snapshot_database(database_path)
        connection = sqlite3.connect(database_path)
        try:
            with self.assertRaises(IncompatibleAvailabilitySchemaError):
                occupied_bookings_by_match(
                    connection,
                    datetime(2030, 7, 14, tzinfo=timezone.utc),
                )
        finally:
            connection.close()

        assert_v2_database_unchanged(self, database_path, before)

    def test_match_listing_rejects_valid_v2_without_modification(self) -> None:
        database_path = self.create_v2_database()
        before = snapshot_database(database_path)

        with self.assertRaises(DatabaseReadError):
            list_matches(
                database_path,
                now=datetime(2030, 7, 14, tzinfo=timezone.utc),
            )

        assert_v2_database_unchanged(self, database_path, before)

    def test_participant_listing_rejects_valid_v2_without_modification(self) -> None:
        database_path = self.create_v2_database()
        before = snapshot_database(database_path)

        with self.assertRaises(DatabaseReadError):
            list_confirmed_participant_names(41, database_path)

        assert_v2_database_unchanged(self, database_path, before)
