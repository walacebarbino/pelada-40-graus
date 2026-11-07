from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uuid
import mercadopago
from backend.database import init_db, get_connection

app = FastAPI(title="Pelada 40º API")

# Inicializa as tabelas do banco ao subir a aplicação
init_db()

# SDK Mercado Pago (coloque sua Access Token de teste aqui se desejar)
sdk = mercadopago.SDK("SUA_ACCESS_TOKEN_TESTE")

class ReservaRequest(BaseModel):
    partida_id: int
    nome_jogador: str

@app.get("/")
def home():
    return {"status": "API Pelada 40º rodando com SQLite!"}

@app.get("/partidas")
def listar_partidas():
    conn = get_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM partidas")
    partidas = cursor.fetchall()
    
    resultado = []
    for p in partidas:
        # Conta quantas reservas já foram feitas para essa partida
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
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verifica a partida
    cursor.execute("SELECT * FROM partidas WHERE id = ?", (reserva.partida_id,))
    partida = cursor.fetchone()
    if not partida:
        conn.close()
        raise HTTPException(status_code=404, detail="Partida não encontrada")
        
    # Verifica vagas disponíveis
    cursor.execute("SELECT COUNT(*) FROM reservas WHERE partida_id = ?", (reserva.partida_id,))
    reservas_feitas = cursor.fetchone()[0]
    if reservas_feitas >= partida["vagas_totais"]:
        conn.close()
        raise HTTPException(status_code=400, detail="Sem vagas disponíveis para este horário")
    
    # Gera cobrança PIX via Mercado Pago
    payment_data = {
        "transaction_amount": float(partida["preco"]),
        "description": f"Pelada 40º - Vaga {partida['horario']}",
        "payment_method_id": "pix",
        "payer": {
            "email": "jogador@pelada40.com",
            "first_name": reserva.nome_jogador
        }
    }
    
    pix_copia_cola = f"00020126580014br.gov.bcb.pix0136{uuid.uuid4()}520400005303986540518.005802BR5910Pelada406009Recife62070503***6304"
    qr_code_base64 = ""
    
    try:
        payment_response = sdk.payment().create(payment_data)
        payment = payment_response.get("response", {})
        if "point_of_interaction" in payment:
            pix_info = payment["point_of_interaction"]["transaction_data"]
            pix_copia_cola = pix_info.get("qr_code", pix_copia_cola)
            qr_code_base64 = pix_info.get("qr_code_base64", "")
    except Exception:
        pass  # Mantém o Pix simulado caso o token não seja válido
        
    # Grava a reserva no banco de dados SQLite
    cursor.execute("""
        INSERT INTO reservas (partida_id, nome_jogador, pix_codigo)
        VALUES (?, ?, ?)
    """, (reserva.partida_id, reserva.nome_jogador, pix_copia_cola))
    
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
    
    cursor.execute("SELECT nome_jogador FROM reservas WHERE partida_id = ?", (partida_id,))
    jogadores = [row["nome_jogador"] for row in cursor.fetchall()]
    
    conn.close()
    return {"partida_id": partida_id, "jogadores": jogadores}