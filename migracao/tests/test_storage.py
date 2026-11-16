import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from storage import DB_PATH, SCHEMA_VERSION, connect, initialize_database


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "test.sqlite3"
        self.assertNotEqual(self.database_path.resolve(), DB_PATH)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @contextmanager
    def open_database(self):
        connection = connect(self.database_path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def insert_match(self, connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            """
            INSERT INTO matches (match_date, match_time)
            VALUES ('2030-01-01', '19:00')
            """
        )
        return cursor.lastrowid

    def test_initialization_creates_versioned_empty_schema(self) -> None:
        self.assertFalse(self.database_path.exists())

        initialize_database(self.database_path)

        self.assertTrue(self.database_path.is_file())
        with self.open_database() as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )
            table_names = {
                row["name"]
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND substr(name, 1, 7) != 'sqlite_'
                    """
                )
            }
            self.assertEqual(
                table_names,
                {"matches", "bookings", "votes", "reservation_operations"},
            )
            for table_name in table_names:
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()[0]
                self.assertEqual(count, 0)

    def test_repeated_initialization_preserves_existing_data(self) -> None:
        initialize_database(self.database_path)
        with self.open_database() as connection:
            match_id = self.insert_match(connection)

        initialize_database(self.database_path)

        with self.open_database() as connection:
            row = connection.execute(
                "SELECT id, title FROM matches WHERE id = ?",
                (match_id,),
            ).fetchone()
            self.assertEqual(tuple(row), (match_id, "Pelada"))
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                SCHEMA_VERSION,
            )

    def test_foreign_keys_are_enabled_and_reject_orphan_bookings(self) -> None:
        initialize_database(self.database_path)

        with self.open_database() as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_keys").fetchone()[0],
                1,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO bookings (match_id) VALUES (999)"
                )

    def test_basic_match_constraints_reject_invalid_values(self) -> None:
        initialize_database(self.database_path)

        with self.open_database() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO matches (
                        match_date, match_time, max_players, price_per_slot_cents
                    )
                    VALUES ('2030-01-01', '19:00', 8, 1800)
                    """
                )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO matches (
                        match_date, match_time, price_per_slot_cents
                    )
                    VALUES ('2030-01-01', '19:00', 1800.5)
                    """
                )

    def test_v3_accepts_fourteen_players_and_rejects_other_limits(self) -> None:
        initialize_database(self.database_path)

        with self.open_database() as connection:
            connection.execute(
                """
                INSERT INTO matches (match_date, match_time, max_players)
                VALUES ('2030-01-01', '19:00', 14)
                """
            )
            for limit in (8, 11, 13, 15):
                with self.subTest(max_players=limit):
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute(
                            """
                            INSERT INTO matches (match_date, match_time, max_players)
                            VALUES ('2030-01-02', '19:00', ?)
                            """,
                            (limit,),
                        )

    def test_pending_charge_is_not_an_approved_payment(self) -> None:
        initialize_database(self.database_path)

        with self.open_database() as connection:
            match_id = self.insert_match(connection)
            connection.execute(
                """
                INSERT INTO bookings (
                    match_id, player_name, player_phone, status, locked_until,
                    payment_status, payment_ref
                )
                VALUES (?, 'Jogador Teste', '11999999999', 'bloqueada',
                        '2030-01-01T19:10:00Z', 'cobranca_pendente', 'test-ref')
                """,
                (match_id,),
            )
            row = connection.execute(
                "SELECT status, payment_status FROM bookings"
            ).fetchone()

        self.assertEqual(tuple(row), ("bloqueada", "cobranca_pendente"))
