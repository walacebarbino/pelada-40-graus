import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import uuid

from migrate_db import (
    MigrationError,
    _reserve_backup_path,
    _snapshot,
    migrate_database,
)
from storage import (
    SCHEMA_OBJECTS_V1,
    SCHEMA_VERSION,
    connect,
    initialize_database,
    schema_is_current,
    schema_is_version_1,
)


class MigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "v1.sqlite3"
        self.create_version_1_fixture()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_version_1_fixture(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            for _, _, statement in SCHEMA_OBJECTS_V1:
                connection.execute(statement)

            connection.execute(
                """
                INSERT INTO matches (
                    id, title, match_date, match_time, location, duration_minutes,
                    max_players, price_per_slot_cents, status, created_at
                )
                VALUES
                    (41, 'Partida A', '2030-07-14', '19:30', 'Quadra A', 60,
                     12, 1875, 'agendada', '2030-01-01T10:00:00Z'),
                    (58, 'Partida B', '2030-07-15', '20:00', 'Quadra B', 90,
                     10, 2500, 'agendada', '2030-01-02T10:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO bookings (
                    id, match_id, player_name, player_phone, status, payment_status,
                    confirmed_at
                )
                VALUES (71, 41, 'Jogador A', '11911111111', 'confirmada',
                        'aprovado', '2030-07-14T18:00:00Z')
                """
            )
            connection.execute(
                """
                INSERT INTO bookings (id, match_id, status)
                VALUES (88, 58, 'expirada')
                """
            )
            connection.execute("DELETE FROM bookings WHERE id = 88")
            connection.execute(
                """
                INSERT INTO votes (
                    id, match_id, booking_id, category, voter_name
                )
                VALUES (45, 41, 71, 'gols', 'Votante A')
                """
            )
            connection.execute(
                """
                INSERT INTO votes (
                    id, match_id, booking_id, category, voter_name
                )
                VALUES (67, 41, 71, 'dribles', 'Votante B')
                """
            )
            connection.execute("DELETE FROM votes WHERE id = 67")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()

    def snapshot(self, path: Path) -> dict[str, tuple[tuple[object, ...], ...]]:
        connection = sqlite3.connect(path)
        try:
            return _snapshot(connection)
        finally:
            connection.close()

    def schema_version_1(self, path: Path) -> bool:
        connection = sqlite3.connect(path)
        try:
            return schema_is_version_1(connection)
        finally:
            connection.close()

    def schema_version_2(self, path: Path) -> bool:
        connection = sqlite3.connect(path)
        try:
            return schema_is_current(connection)
        finally:
            connection.close()

    def test_migration_preserves_rows_backup_relations_indexes_and_next_ids(self) -> None:
        before = self.snapshot(self.database_path)
        result = migrate_database(self.database_path)

        self.assertFalse(result.already_current)
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.is_file())
        self.assertTrue(self.schema_version_2(self.database_path))
        self.assertTrue(self.schema_version_1(result.backup_path))
        self.assertEqual(self.snapshot(self.database_path), before)
        self.assertEqual(self.snapshot(result.backup_path), before)

        connection = connect(self.database_path)
        try:
            expected_indexes = {
                "idx_matches_status_date",
                "idx_bookings_match_status",
                "idx_bookings_status_locked_until",
                "idx_votes_match_category",
                "idx_votes_booking_match",
            }
            actual_indexes = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
                if not row["name"].startswith("sqlite_autoindex_")
            }
            self.assertEqual(actual_indexes, expected_indexes)
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0],
                "ok",
            )

            match_cursor = connection.execute(
                """
                INSERT INTO matches (
                    title, match_date, match_time, location, duration_minutes,
                    max_players, price_per_slot_cents
                )
                VALUES ('Próxima', '2030-07-16', '21:00', 'Quadra C', 60,
                        14, 1999)
                """
            )
            self.assertEqual(match_cursor.lastrowid, 59)

            booking_cursor = connection.execute(
                "INSERT INTO bookings (match_id, status) VALUES (58, 'disponivel')"
            )
            self.assertEqual(booking_cursor.lastrowid, 89)
            vote_cursor = connection.execute(
                """
                INSERT INTO votes (match_id, booking_id, category, voter_name)
                VALUES (58, ?, 'defesas', 'Novo votante')
                """,
                (booking_cursor.lastrowid,),
            )
            self.assertEqual(vote_cursor.lastrowid, 68)
            connection.commit()
        finally:
            connection.close()

    def test_failed_backup_prevents_all_source_changes(self) -> None:
        before = self.snapshot(self.database_path)
        with mock.patch(
            "migrate_db._create_backup",
            side_effect=MigrationError("induced backup failure"),
        ):
            with self.assertRaises(MigrationError):
                migrate_database(self.database_path)

        self.assertTrue(self.schema_version_1(self.database_path))
        self.assertEqual(self.snapshot(self.database_path), before)

    def test_failure_after_rebuild_rolls_back_and_keeps_verified_backup(self) -> None:
        before = self.snapshot(self.database_path)
        from migrate_db import _rebuild_matches

        def rebuild_then_fail(connection: sqlite3.Connection) -> None:
            _rebuild_matches(connection)
            raise RuntimeError("induced transformation failure")

        with mock.patch(
            "migrate_db._rebuild_matches",
            side_effect=rebuild_then_fail,
        ):
            with self.assertRaises(MigrationError) as failure:
                migrate_database(self.database_path)

        backups = list(
            Path(self.temporary_directory.name).glob(
                "v1.sqlite3.v1-backup-*.sqlite3"
            )
        )
        self.assertEqual(len(backups), 1)
        self.assertIn(backups[0].name, str(failure.exception))
        self.assertTrue(self.schema_version_1(self.database_path))
        self.assertEqual(self.snapshot(self.database_path), before)
        self.assertTrue(self.schema_version_1(backups[0]))
        self.assertEqual(self.snapshot(backups[0]), before)

    def test_missing_database_is_not_created(self) -> None:
        missing_path = Path(self.temporary_directory.name) / "missing.sqlite3"

        with self.assertRaises(MigrationError):
            migrate_database(missing_path)

        self.assertFalse(missing_path.exists())
        self.assertEqual(
            list(Path(self.temporary_directory.name).glob("*.v1-backup-*.sqlite3")),
            [],
        )

    def test_incompatible_v1_schema_is_rejected_without_backup_or_changes(self) -> None:
        incompatible_path = Path(self.temporary_directory.name) / "incompatible.sqlite3"
        connection = sqlite3.connect(incompatible_path)
        try:
            connection.execute("CREATE TABLE matches (id INTEGER PRIMARY KEY)")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
        finally:
            connection.close()

        with self.assertRaises(MigrationError):
            migrate_database(incompatible_path)

        connection = sqlite3.connect(incompatible_path)
        try:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name = 'matches'"
                ).fetchone()[0],
                "CREATE TABLE matches (id INTEGER PRIMARY KEY)",
            )
        finally:
            connection.close()
        self.assertEqual(
            list(Path(self.temporary_directory.name).glob(
                "incompatible.sqlite3.v1-backup-*.sqlite3"
            )),
            [],
        )

    def test_initializer_rejects_v1_and_requires_explicit_migration(self) -> None:
        before = self.snapshot(self.database_path)
        with self.assertRaisesRegex(RuntimeError, "explicit migration"):
            initialize_database(self.database_path)

        self.assertTrue(self.schema_version_1(self.database_path))
        self.assertEqual(self.snapshot(self.database_path), before)

    def test_v2_rerun_validates_and_preserves_without_new_backup(self) -> None:
        v2_path = Path(self.temporary_directory.name) / "already-v2.sqlite3"
        initialize_database(v2_path)
        connection = connect(v2_path)
        try:
            connection.execute(
                """
                INSERT INTO matches (
                    title, match_date, match_time, location, duration_minutes,
                    max_players, price_per_slot_cents
                )
                VALUES ('14 jogadores', '2030-07-14', '19:30', 'Quadra A', 60,
                        14, 1875)
                """
            )
            connection.commit()
        finally:
            connection.close()
        before = self.snapshot(v2_path)

        result = migrate_database(v2_path)

        self.assertTrue(result.already_current)
        self.assertIsNone(result.backup_path)
        self.assertEqual(self.snapshot(v2_path), before)
        self.assertTrue(self.schema_version_2(v2_path))

    def test_backup_reservation_never_overwrites_an_existing_file(self) -> None:
        fixed_time = mock.Mock()
        fixed_time.now.return_value.strftime.return_value = "20300101T000000Z"
        existing_path = Path(self.temporary_directory.name) / (
            f"{self.database_path.name}.v1-backup-20300101T000000Z-{uuid.UUID(int=1).hex}.sqlite3"
        )
        existing_path.write_bytes(b"existing backup")

        with mock.patch("migrate_db.datetime", fixed_time), mock.patch(
            "migrate_db.uuid.uuid4",
            side_effect=(uuid.UUID(int=1), uuid.UUID(int=2)),
        ):
            reserved_path = _reserve_backup_path(self.database_path)

        self.assertNotEqual(reserved_path, existing_path)
        self.assertEqual(existing_path.read_bytes(), b"existing backup")
        self.assertTrue(reserved_path.is_file())


if __name__ == "__main__":
    unittest.main()
