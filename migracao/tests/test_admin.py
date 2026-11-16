import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError

import main
from admin import (
    AdminDatabaseError,
    AdminMatchNotFoundError,
    AdminKeyNotConfiguredError,
    InvalidAdminKeyError,
    MatchDeleteConflictError,
    MatchEditConflictError,
    MatchCreateRequest,
    create_match,
    delete_match,
    update_match,
    verify_admin_key,
)
from queries import list_matches
from storage import DB_PATH, connect, initialize_database
from v2_fixture import (
    assert_v2_database_unchanged,
    create_valid_v2_database,
    snapshot_database,
)


class AdminTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "admin.sqlite3"
        self.assertNotEqual(self.database_path.resolve(), DB_PATH)
        initialize_database(self.database_path)
        self.valid_match = MatchCreateRequest(
            title="Pelada de teste",
            match_date="2030-07-14",
            match_time="19:30",
            location="Quadra teste",
            duration_minutes=60,
            max_players=10,
            price_per_slot_cents=1875,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def insert_match(self, status: str = "agendada") -> int:
        connection = connect(self.database_path)
        try:
            cursor = connection.execute(
                """
                INSERT INTO matches (
                    title, match_date, match_time, location, duration_minutes,
                    max_players, price_per_slot_cents, status
                )
                VALUES ('Original', '2030-07-14', '19:30', 'Quadra original',
                        60, 10, 1875, ?)
                """,
                (status,),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def read_match(self, match_id: int) -> dict[str, object]:
        connection = connect(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT id, title, match_date, match_time, location, duration_minutes,
                       max_players, price_per_slot_cents, status
                FROM matches
                WHERE id = ?
                """,
                (match_id,),
            ).fetchone()
            return dict(row)
        finally:
            connection.close()

    def update_through_endpoint(self, match_id: int) -> dict[str, object]:
        with mock.patch(
            "main.update_match",
            side_effect=lambda identifier, match: update_match(
                identifier,
                match,
                self.database_path,
            ),
        ):
            return main.atualizar_partida_v2(
                match_id,
                self.valid_match,
                "chave-local",
            )

    def delete_through_endpoint(self, match_id: int) -> Response:
        with mock.patch(
            "main.delete_match",
            side_effect=lambda identifier: delete_match(
                identifier,
                self.database_path,
            ),
        ):
            return main.excluir_partida_v2(match_id, "chave-local")

    def add_reservation(self, match_id: int, status: str) -> int:
        connection = connect(self.database_path)
        try:
            if status == "bloqueada":
                cursor = connection.execute(
                    """
                    INSERT INTO bookings (
                        match_id, player_name, player_phone, status, locked_until,
                        payment_status
                    )
                    VALUES (?, 'Pessoa teste', '11999999999', ?,
                            '2030-07-14T20:00:00Z', 'cobranca_pendente')
                    """,
                    (match_id, status),
                )
            elif status == "confirmada":
                cursor = connection.execute(
                    """
                    INSERT INTO bookings (
                        match_id, player_name, player_phone, status,
                        payment_status, confirmed_at
                    )
                    VALUES (?, 'Pessoa teste', '11999999999', ?, 'aprovado',
                            '2030-07-14T18:00:00Z')
                    """,
                    (match_id, status),
                )
            else:
                cursor = connection.execute(
                    "INSERT INTO bookings (match_id, status) VALUES (?, ?)",
                    (match_id, status),
                )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def read_reservation(self, reservation_id: int) -> dict[str, object]:
        connection = connect(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT id, match_id, player_name, player_phone, status, locked_until,
                       payment_status, payment_ref, confirmed_at
                FROM bookings
                WHERE id = ?
                """,
                (reservation_id,),
            ).fetchone()
            return dict(row)
        finally:
            connection.close()

    def add_vote(self, match_id: int, reservation_id: int) -> int:
        connection = connect(self.database_path)
        try:
            cursor = connection.execute(
                """
                INSERT INTO votes (match_id, booking_id, category, voter_name)
                VALUES (?, ?, 'gols', 'Votante teste')
                """,
                (match_id, reservation_id),
            )
            connection.commit()
            return cursor.lastrowid
        finally:
            connection.close()

    def read_vote(self, vote_id: int) -> dict[str, object]:
        connection = connect(self.database_path)
        try:
            row = connection.execute(
                """
                SELECT id, match_id, booking_id, category, voter_name
                FROM votes
                WHERE id = ?
                """,
                (vote_id,),
            ).fetchone()
            return dict(row)
        finally:
            connection.close()

    def test_missing_server_configuration_is_distinct(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PELADA_ADMIN_KEY", None)
            with self.assertRaises(AdminKeyNotConfiguredError):
                verify_admin_key("qualquer-valor")

    def test_missing_and_incorrect_keys_are_rejected(self) -> None:
        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(InvalidAdminKeyError):
                verify_admin_key(None)
            with self.assertRaises(InvalidAdminKeyError):
                verify_admin_key("chave-incorreta")
            verify_admin_key("chave-local")

    def test_endpoint_returns_generic_errors_for_unconfigured_or_invalid_key(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PELADA_ADMIN_KEY", None)
            with self.assertRaises(HTTPException) as missing_configuration:
                main.cadastrar_partida_v2(self.valid_match, "chave-local")
            self.assertEqual(missing_configuration.exception.status_code, 503)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as missing_key:
                main.cadastrar_partida_v2(self.valid_match, None)
            self.assertEqual(missing_key.exception.status_code, 401)

            with self.assertRaises(HTTPException) as incorrect_key:
                main.cadastrar_partida_v2(self.valid_match, "outra-chave")
            self.assertEqual(incorrect_key.exception.status_code, 401)

        self.assertEqual(list_matches(self.database_path), [])

    def test_invalid_inputs_are_rejected_without_writing(self) -> None:
        invalid_inputs = (
            {"duration_minutes": True},
            {"duration_minutes": 60.5},
            {"price_per_slot_cents": True},
            {"price_per_slot_cents": 1875.0},
            {"max_players": True},
            {"max_players": 8},
            {"max_players": 11},
            {"max_players": 13},
            {"max_players": 14.5},
            {"match_date": "2030-02-30"},
            {"match_time": "25:00"},
            {"status": "finalizada"},
        )

        for update in invalid_inputs:
            data = self.valid_match.model_dump()
            data.update(update)
            with self.subTest(update=update):
                with self.assertRaises(ValidationError):
                    MatchCreateRequest(**data)

        self.assertEqual(list_matches(self.database_path), [])

    def test_valid_creation_preserves_cents_and_is_visible_in_public_list(self) -> None:
        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with mock.patch(
                "main.create_match",
                side_effect=lambda match: create_match(match, self.database_path),
            ):
                created = main.cadastrar_partida_v2(self.valid_match, "chave-local")

        self.assertEqual(created["price_per_slot_cents"], 1875)
        self.assertEqual(created["status"], "agendada")
        self.assertNotIn("player_phone", created)

        with mock.patch(
            "main.list_matches",
            side_effect=lambda: list_matches(self.database_path),
        ):
            public_matches = main.listar_partidas_v2()

        self.assertEqual(len(public_matches), 1)
        self.assertEqual(public_matches[0]["id"], created["id"])
        self.assertEqual(public_matches[0]["price_per_slot_cents"], 1875)

    def test_creation_accepts_each_supported_player_limit(self) -> None:
        for limit in (10, 12, 14):
            with self.subTest(max_players=limit):
                data = self.valid_match.model_dump()
                data["max_players"] = limit
                match = MatchCreateRequest(**data)

                with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
                    with mock.patch(
                        "main.create_match",
                        side_effect=lambda value: create_match(
                            value,
                            self.database_path,
                        ),
                    ):
                        created = main.cadastrar_partida_v2(
                            match,
                            "chave-local",
                        )

                self.assertEqual(created["max_players"], limit)
                self.assertEqual(
                    self.read_match(created["id"])["max_players"],
                    limit,
                )

    def test_update_endpoint_rejects_missing_incorrect_and_unconfigured_keys(self) -> None:
        match_id = self.insert_match()
        original = self.read_match(match_id)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PELADA_ADMIN_KEY", None)
            with self.assertRaises(HTTPException) as missing_configuration:
                main.atualizar_partida_v2(match_id, self.valid_match, "chave-local")
            self.assertEqual(missing_configuration.exception.status_code, 503)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as missing_key:
                main.atualizar_partida_v2(match_id, self.valid_match, None)
            self.assertEqual(missing_key.exception.status_code, 401)

            with self.assertRaises(HTTPException) as incorrect_key:
                main.atualizar_partida_v2(match_id, self.valid_match, "outra-chave")
            self.assertEqual(incorrect_key.exception.status_code, 401)

        self.assertEqual(self.read_match(match_id), original)

    def test_valid_update_preserves_id_and_price_in_cents(self) -> None:
        match_id = self.insert_match()
        updated = self.valid_match.model_copy(
            update={
                "title": "Título atualizado",
                "location": "Quadra atualizada",
                "price_per_slot_cents": 2450,
            }
        )

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with mock.patch(
                "main.update_match",
                side_effect=lambda identifier, match: update_match(
                    identifier,
                    match,
                    self.database_path,
                ),
            ):
                response = main.atualizar_partida_v2(
                    match_id,
                    updated,
                    "chave-local",
                )

        self.assertEqual(response["id"], match_id)
        self.assertEqual(response["title"], "Título atualizado")
        self.assertEqual(response["price_per_slot_cents"], 2450)
        self.assertEqual(response["status"], "agendada")
        self.assertEqual(len(list_matches(self.database_path)), 1)

    def test_update_accepts_each_supported_player_limit(self) -> None:
        for limit in (10, 12, 14):
            with self.subTest(max_players=limit):
                match_id = self.insert_match()
                data = self.valid_match.model_dump()
                data["max_players"] = limit
                match = MatchCreateRequest(**data)

                with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
                    with mock.patch(
                        "main.update_match",
                        side_effect=lambda identifier, value: update_match(
                            identifier,
                            value,
                            self.database_path,
                        ),
                    ):
                        updated = main.atualizar_partida_v2(
                            match_id,
                            match,
                            "chave-local",
                        )

                self.assertEqual(updated["id"], match_id)
                self.assertEqual(updated["max_players"], limit)

    def test_update_of_missing_match_returns_not_found(self) -> None:
        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as not_found:
                self.update_through_endpoint(987654)

        self.assertEqual(not_found.exception.status_code, 404)
        with self.assertRaises(AdminMatchNotFoundError):
            update_match(987654, self.valid_match, self.database_path)

    def test_invalid_update_inputs_leave_existing_match_unchanged(self) -> None:
        match_id = self.insert_match()
        original = self.read_match(match_id)
        invalid_updates = (
            {"duration_minutes": True},
            {"duration_minutes": 60.5},
            {"price_per_slot_cents": True},
            {"price_per_slot_cents": 1875.0},
            {"max_players": True},
            {"max_players": 8},
            {"max_players": 11},
            {"max_players": 13},
            {"max_players": 14.5},
            {"match_date": "2030-02-30"},
            {"match_time": "25:00"},
            {"status": "finalizada"},
        )

        for update in invalid_updates:
            data = self.valid_match.model_dump()
            data.update(update)
            with self.subTest(update=update):
                with self.assertRaises(ValidationError):
                    MatchCreateRequest(**data)

        self.assertEqual(self.read_match(match_id), original)

    def test_non_scheduled_match_is_rejected_without_changes(self) -> None:
        match_id = self.insert_match(status="finalizada")
        original = self.read_match(match_id)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as conflict:
                self.update_through_endpoint(match_id)

        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(self.read_match(match_id), original)

    def test_reservations_in_every_state_block_edits_and_remain_unchanged(self) -> None:
        for status in ("disponivel", "bloqueada", "confirmada", "expirada"):
            with self.subTest(status=status):
                match_id = self.insert_match()
                original_match = self.read_match(match_id)
                reservation_id = self.add_reservation(match_id, status)
                original_reservation = self.read_reservation(reservation_id)

                with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
                    with self.assertRaises(HTTPException) as conflict:
                        self.update_through_endpoint(match_id)

                self.assertEqual(conflict.exception.status_code, 409)
                self.assertEqual(self.read_match(match_id), original_match)
                self.assertEqual(
                    self.read_reservation(reservation_id),
                    original_reservation,
                )
                with self.assertRaises(MatchEditConflictError):
                    update_match(match_id, self.valid_match, self.database_path)

    def test_delete_endpoint_rejects_missing_incorrect_and_unconfigured_keys(self) -> None:
        match_id = self.insert_match()
        original = self.read_match(match_id)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PELADA_ADMIN_KEY", None)
            with self.assertRaises(HTTPException) as missing_configuration:
                main.excluir_partida_v2(match_id, "chave-local")
            self.assertEqual(missing_configuration.exception.status_code, 503)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as missing_key:
                main.excluir_partida_v2(match_id, None)
            self.assertEqual(missing_key.exception.status_code, 401)

            with self.assertRaises(HTTPException) as incorrect_key:
                main.excluir_partida_v2(match_id, "outra-chave")
            self.assertEqual(incorrect_key.exception.status_code, 401)

        self.assertEqual(self.read_match(match_id), original)

    def test_valid_delete_returns_204_without_body_and_preserves_other_match_ids(self) -> None:
        first_id = self.insert_match()
        deleted_id = self.insert_match()
        last_id = self.insert_match()

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            response = self.delete_through_endpoint(deleted_id)

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.body, b"")
        self.assertEqual(
            [match["id"] for match in list_matches(self.database_path)],
            [first_id, last_id],
        )
        with self.assertRaises(AdminMatchNotFoundError):
            delete_match(deleted_id, self.database_path)

    def test_delete_of_missing_match_returns_not_found(self) -> None:
        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as not_found:
                self.delete_through_endpoint(987654)

        self.assertEqual(not_found.exception.status_code, 404)
        with self.assertRaises(AdminMatchNotFoundError):
            delete_match(987654, self.database_path)

    def test_reservations_in_every_state_block_deletion_without_changes(self) -> None:
        for status in ("disponivel", "bloqueada", "confirmada", "expirada"):
            with self.subTest(status=status):
                match_id = self.insert_match()
                original_match = self.read_match(match_id)
                reservation_id = self.add_reservation(match_id, status)
                original_reservation = self.read_reservation(reservation_id)

                with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
                    with self.assertRaises(HTTPException) as conflict:
                        self.delete_through_endpoint(match_id)

                self.assertEqual(conflict.exception.status_code, 409)
                self.assertEqual(self.read_match(match_id), original_match)
                self.assertEqual(
                    self.read_reservation(reservation_id),
                    original_reservation,
                )
                with self.assertRaises(MatchDeleteConflictError):
                    delete_match(match_id, self.database_path)

    def test_vote_linked_to_match_blocks_deletion_without_changing_records(self) -> None:
        match_id = self.insert_match()
        original_match = self.read_match(match_id)
        reservation_id = self.add_reservation(match_id, "expirada")
        vote_id = self.add_vote(match_id, reservation_id)
        original_reservation = self.read_reservation(reservation_id)
        original_vote = self.read_vote(vote_id)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as conflict:
                self.delete_through_endpoint(match_id)

        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(self.read_match(match_id), original_match)
        self.assertEqual(self.read_reservation(reservation_id), original_reservation)
        self.assertEqual(self.read_vote(vote_id), original_vote)

    def test_non_scheduled_match_blocks_deletion_without_changes(self) -> None:
        match_id = self.insert_match(status="finalizada")
        original = self.read_match(match_id)

        with mock.patch.dict(os.environ, {"PELADA_ADMIN_KEY": "chave-local"}):
            with self.assertRaises(HTTPException) as conflict:
                self.delete_through_endpoint(match_id)

        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(self.read_match(match_id), original)

    def test_create_update_and_delete_reject_valid_v2_without_modification(self) -> None:
        database_path = create_valid_v2_database(
            Path(self.temporary_directory.name) / "valid-v2.sqlite3"
        )
        before = snapshot_database(database_path)

        with self.assertRaises(AdminDatabaseError):
            create_match(self.valid_match, database_path)
        assert_v2_database_unchanged(self, database_path, before)

        with self.assertRaises(AdminDatabaseError):
            update_match(41, self.valid_match, database_path)
        assert_v2_database_unchanged(self, database_path, before)

        with self.assertRaises(AdminDatabaseError):
            delete_match(41, database_path)
        assert_v2_database_unchanged(self, database_path, before)


if __name__ == "__main__":
    unittest.main()
