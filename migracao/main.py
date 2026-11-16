import math
import os
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from admin import (
    AdminDatabaseError,
    AdminKeyNotConfiguredError,
    AdminMatchNotFoundError,
    InvalidAdminKeyError,
    MatchDeleteConflictError,
    MatchEditConflictError,
    MatchCreateRequest,
    create_match,
    delete_match,
    update_match,
    verify_admin_key,
)
from queries import (
    DatabaseReadError,
    MatchNotFoundError,
    list_confirmed_participant_names,
    list_matches,
)
from reservations_api import (
    ReservationHTTPSettings,
    configure_reservation_http,
    reservation_http_enabled,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PARTIDAS_API_URL = "http://127.0.0.1:8000/partidas"
PARTICIPANTS_API_URL = "http://127.0.0.1:8000/participantes/{partida_id}"
PARTIDA_FIELDS = ("id", "horario", "vagas_totais", "vagas_livres", "preco")
UPSTREAM_ERROR = "Não foi possível carregar as partidas. Tente novamente."
PARTICIPANTS_ERROR = "Não foi possível carregar os participantes. Tente novamente."

app = FastAPI(title="Pelada - Interface local")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def invalid_upstream_response() -> HTTPException:
    return HTTPException(status_code=502, detail=UPSTREAM_ERROR)


def invalid_participants_response() -> HTTPException:
    return HTTPException(status_code=502, detail=PARTICIPANTS_ERROR)


def validate_partidas(data: Any) -> list[dict[str, int | float | str]]:
    if not isinstance(data, list):
        raise invalid_upstream_response()

    partidas: list[dict[str, int | float | str]] = []
    for item in data:
        if not isinstance(item, dict) or any(field not in item for field in PARTIDA_FIELDS):
            raise invalid_upstream_response()

        partida_id = item["id"]
        horario = item["horario"]
        vagas_totais = item["vagas_totais"]
        vagas_livres = item["vagas_livres"]
        preco = item["preco"]

        if isinstance(partida_id, bool) or not isinstance(partida_id, int):
            raise invalid_upstream_response()
        if not isinstance(horario, str):
            raise invalid_upstream_response()
        if (
            isinstance(vagas_totais, bool)
            or not isinstance(vagas_totais, int)
            or isinstance(vagas_livres, bool)
            or not isinstance(vagas_livres, int)
            or vagas_totais < 0
            or vagas_livres < 0
            or vagas_livres > vagas_totais
        ):
            raise invalid_upstream_response()
        if isinstance(preco, bool) or not isinstance(preco, (int, float)):
            raise invalid_upstream_response()
        try:
            price_is_finite = math.isfinite(preco)
        except OverflowError:
            price_is_finite = False
        if not price_is_finite:
            raise invalid_upstream_response()

        partidas.append({field: item[field] for field in PARTIDA_FIELDS})

    return partidas


@app.get("/", include_in_schema=False)
def serve_interface() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/partidas")
def listar_partidas() -> list[dict[str, int | float | str]]:
    try:
        response = requests.get(
            PARTIDAS_API_URL,
            timeout=(2, 5),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise requests.HTTPError("Unexpected upstream response status")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise invalid_upstream_response() from None

    return validate_partidas(data)


@app.get("/api/partidas/{partida_id}/participantes")
def listar_participantes(partida_id: int) -> dict[str, int | list[str]]:
    try:
        response = requests.get(
            PARTICIPANTS_API_URL.format(partida_id=partida_id),
            timeout=(2, 5),
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise requests.HTTPError("Unexpected upstream response status")
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        raise invalid_participants_response() from None

    if not isinstance(data, dict):
        raise invalid_participants_response()

    response_match_id = data.get("partida_id")
    participants = data.get("jogadores")
    if (
        isinstance(response_match_id, bool)
        or not isinstance(response_match_id, int)
        or response_match_id != partida_id
        or not isinstance(participants, list)
        or any(not isinstance(name, str) or not name.strip() for name in participants)
    ):
        raise invalid_participants_response()

    return {"partida_id": partida_id, "jogadores": participants}


@app.get("/api/v2/partidas")
def listar_partidas_v2() -> list[dict[str, Any]]:
    try:
        return list_matches()
    except DatabaseReadError:
        raise HTTPException(
            status_code=503,
            detail="O banco local está indisponível ou incompatível.",
        ) from None


@app.get("/api/v2/partidas/{partida_id}/participantes")
def listar_participantes_v2(partida_id: int) -> dict[str, int | list[str]]:
    try:
        names = list_confirmed_participant_names(partida_id)
    except MatchNotFoundError:
        raise HTTPException(status_code=404, detail="Partida não encontrada.") from None
    except DatabaseReadError:
        raise HTTPException(
            status_code=503,
            detail="O banco local está indisponível ou incompatível.",
        ) from None

    return {"partida_id": partida_id, "participantes": names}


@app.post("/api/v2/admin/partidas", status_code=201)
def cadastrar_partida_v2(
    match: MatchCreateRequest,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> dict[str, Any]:
    try:
        verify_admin_key(x_admin_key)
    except AdminKeyNotConfiguredError:
        raise HTTPException(
            status_code=503,
            detail="O cadastro administrativo está indisponível.",
        ) from None
    except InvalidAdminKeyError:
        raise HTTPException(status_code=401, detail="Não autorizado.") from None

    try:
        return create_match(match)
    except AdminDatabaseError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível cadastrar a partida.",
        ) from None


@app.put("/api/v2/admin/partidas/{partida_id}", status_code=200)
def atualizar_partida_v2(
    partida_id: int,
    match: MatchCreateRequest,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> dict[str, Any]:
    try:
        verify_admin_key(x_admin_key)
    except AdminKeyNotConfiguredError:
        raise HTTPException(
            status_code=503,
            detail="A edição administrativa está indisponível.",
        ) from None
    except InvalidAdminKeyError:
        raise HTTPException(status_code=401, detail="Não autorizado.") from None

    try:
        return update_match(partida_id, match)
    except AdminMatchNotFoundError:
        raise HTTPException(status_code=404, detail="Partida não encontrada.") from None
    except MatchEditConflictError:
        raise HTTPException(
            status_code=409,
            detail="A partida só pode ser editada quando estiver agendada e sem reservas.",
        ) from None
    except AdminDatabaseError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível editar a partida.",
        ) from None


@app.delete(
    "/api/v2/admin/partidas/{partida_id}",
    status_code=204,
    response_class=Response,
)
def excluir_partida_v2(
    partida_id: int,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
) -> Response:
    try:
        verify_admin_key(x_admin_key)
    except AdminKeyNotConfiguredError:
        raise HTTPException(
            status_code=503,
            detail="A exclusão administrativa está indisponível.",
        ) from None
    except InvalidAdminKeyError:
        raise HTTPException(status_code=401, detail="Não autorizado.") from None

    try:
        delete_match(partida_id)
    except AdminMatchNotFoundError:
        raise HTTPException(status_code=404, detail="Partida não encontrada.") from None
    except MatchDeleteConflictError:
        raise HTTPException(
            status_code=409,
            detail="A partida só pode ser excluída quando estiver agendada e sem reservas ou votos.",
        ) from None
    except AdminDatabaseError:
        raise HTTPException(
            status_code=503,
            detail="Não foi possível excluir a partida.",
        ) from None

    return Response(status_code=204)


if reservation_http_enabled(
    os.environ.get("PELADA_RESERVATION_HTTP_ENABLED")
):
    configure_reservation_http(
        app,
        enabled=True,
        settings=ReservationHTTPSettings.from_environment(),
    )
