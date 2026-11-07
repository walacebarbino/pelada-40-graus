from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
import uuid
import mercadopago
from backend.database import init_db, get_connection

app = FastAPI(title="Pelada 40º API")

init_db()
sdk = mercadopago.SDK("SUA_ACCESS_TOKEN_TESTE")

ADMIN_SECRET_KEY = "admin123"

class ReservaRequest(BaseModel):
    partida_id: int
    nome_jogador: str
    telefone: str

class PartidaUpdate(BaseModel):
    horario: str
    preco: float
    vagas_totais: int

class AdminRemoverRequest(BaseModel):
    partida_id: int
    nome_jogador: str

def verificar_admin(x_admin_key: str = Header(None)):
    if x_admin_key != ADMIN_SECRET_KEY:
        raise HTTPException(status_code=403, detail="Acesso negado: Senha de Administrador incorreta")

@app.get("/")
def home():
    return {"status": "API Pelada 40º rodando com SQLite e Painel Admin!"}

@app.get("/partidas")
def listar_partidas():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM partidas")
    partidas = cursor.fetchall()
    
    resultado = []
    for p in partidas:
        cursor.execute("SELECT COUNT(*) FROM reservas WHERE partida_id = ?", (p["id"],))
        reservas_feitas = cursor.fetchone()[0]
        vagas_livres = p["vagas_totais"] - reservas_feitas
        
        resultado.append({
            "id": p["id"],
            "horario": p["horario"],
            "vagas_totais": p["vagas_totais"],
            "vagas_livres": max(0, vagas_livres),
            "preco": p["preco"]
        })
    conn.close()
    return resultado

@app.post("/reservar")
def fazer_reserva(reserva: ReservaRequest):
    telefone = "".join(
        caractere for caractere in reserva.telefone
        if caractere in "0123456789"
    )

    if len(telefone) not in (10, 11):
        raise HTTPException(
            status_code=400,
            detail="Informe o telefone com DDD, usando 10 ou 11 dígitos, sem o +55."
        )

    reserva.telefone = telefone

    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM partidas WHERE id = ?", (reserva.partida_id,))
    partida = cursor.fetchone()
    if not partida:
        conn.close()
        raise HTTPException(status_code=404, detail="Partida não encontrada")
        
    cursor.execute("SELECT COUNT(*) FROM reservas WHERE partida_id = ?", (reserva.partida_id,))
    reservas_feitas = cursor.fetchone()[0]
    if reservas_feitas >= partida["vagas_totais"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Sem vagas disponíveis para este horário")
    
    try:
        payment_response = sdk.payment().create({
            "transaction_amount": float(partida["preco"]),
            "description": f"Pelada 40º - Vaga {partida['horario']}",
            "payment_method_id": "pix",
            "payer": {
                "email": "jogador@pelada40.com",
                "first_name": reserva.nome_jogador
            }
        })
    except Exception:
        conn.close()
        raise HTTPException(
            status_code=502,
            detail="Não foi possível conectar ao Mercado Pago. A reserva não foi registrada."
        )

    if payment_response.get("status") not in (200, 201):
        conn.close()
        raise HTTPException(
            status_code=502,
            detail="O Mercado Pago não criou a cobrança. A reserva não foi registrada."
        )

    payment = payment_response.get("response") or {}
    interacao = payment.get("point_of_interaction") or {}
    pix_info = interacao.get("transaction_data") or {}

    pix_copia_cola = pix_info.get("qr_code")
    qr_code_base64 = pix_info.get("qr_code_base64") or ""

    if not pix_copia_cola:
        conn.close()
        raise HTTPException(
            status_code=502,
            detail="O Mercado Pago não retornou o código Pix. A reserva não foi registrada; não tente pagar nem repetir a cobrança antes de verificar."
        )
        
    cursor.execute("""
        INSERT INTO reservas (
            partida_id, nome_jogador, telefone, pix_codigo, status
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        reserva.partida_id,
        reserva.nome_jogador,
        reserva.telefone,
        pix_copia_cola,
        "pendente"
    ))
    
    conn.commit()
    conn.close()
    
    return {
        "mensagem": "Reserva registrada com sucesso!",
        "pix_copia_cola": pix_copia_cola,
        "qr_code_base64": qr_code_base64
    }

@app.get("/participantes/{partida_id}")
def listar_participantes(partida_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT nome_jogador
        FROM reservas
        WHERE partida_id = ?
          AND status = ?
        ORDER BY id
    """, (partida_id, "confirmado"))

    jogadores = [row["nome_jogador"] for row in cursor.fetchall()]

    conn.close()

    return {
        "partida_id": partida_id,
        "jogadores": jogadores
    }

@app.put("/admin/partida/{partida_id}")
def atualizar_partida(partida_id: int, dados: PartidaUpdate, x_admin_key: str = Header(None)):
    verificar_admin(x_admin_key)
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE partidas SET horario = ?, preco = ?, vagas_totais = ?
        WHERE id = ?
    """, (dados.horario, dados.preco, dados.vagas_totais, partida_id))
    
    conn.commit()
    conn.close()
    return {"mensagem": "Partida atualizada com sucesso!"}

@app.post("/admin/remover-jogador")
def remover_jogador(dados: AdminRemoverRequest, x_admin_key: str = Header(None)):
    verificar_admin(x_admin_key)
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM reservas 
        WHERE partida_id = ? AND nome_jogador = ?
    """, (dados.partida_id, dados.nome_jogador))
    
    conn.commit()
    conn.close()
    return {"mensagem": f"Jogador {dados.nome_jogador} removido da partida!"}