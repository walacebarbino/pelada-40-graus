from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mercadopago

app = FastAPI(title="API Pelada 40º", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Access Token de teste configurado do Mercado Pago
ACCESS_TOKEN = "TEST-6362835952828012-110609-076204e5b90516e36235a256e2346e74-102250045"
sdk = mercadopago.SDK(ACCESS_TOKEN)

# Base de dados simulada em memória com a lista de participantes
partidas_db = [
    {"id": 1, "horario": "19:00 - 19:40", "vagas_totais": 10, "vagas_ocupadas": 0, "preco": 18.00, "participantes": []},
    {"id": 2, "horario": "19:45 - 20:25", "vagas_totais": 10, "vagas_ocupadas": 0, "preco": 18.00, "participantes": []},
    {"id": 3, "horario": "20:30 - 21:10", "vagas_totais": 10, "vagas_ocupadas": 0, "preco": 18.00, "participantes": []},
]

class ReservaRequest(BaseModel):
    partida_id: int
    nome: str
    telefone: str

@app.get("/partidas")
def listar_partidas():
    return partidas_db

@app.post("/reservar")
def fazer_reserva(dados: ReservaRequest):
    for p in partidas_db:
        if p["id"] == dados.partida_id:
            if p["vagas_ocupadas"] < p["vagas_totais"]:
                
                # Dados da cobrança para a API do Mercado Pago
                payment_data = {
                    "transaction_amount": float(p["preco"]),
                    "description": f"Pelada 40º - Horario {p['horario']} - {dados.nome}",
                    "payment_method_id": "pix",
                    "payer": {
                        "email": f"atleta_{dados.telefone}@pelada40.com",
                        "first_name": dados.nome,
                    }
                }
                
                try:
                    # Chamada oficial ao Mercado Pago para gerar o Pix com QR Code real
                    payment_response = sdk.payment().create(payment_data)
                    payment = payment_response.get("response")
                    
                    if not payment or "point_of_interaction" not in payment:
                        raise HTTPException(status_code=400, detail="Erro ao comunicar com o Mercado Pago. Verifica o teu Token.")
                    
                    # Extrai o código Copia e Cola gerado oficialmente
                    pix_copia_cola = payment["point_of_interaction"]["transaction_data"]["qr_code"]
                    
                    p["vagas_ocupadas"] += 1
                    p["participantes"].append(dados.nome)
                    
                    return {
                        "status": "sucesso",
                        "mensagem": f"Cobrança Pix gerada para {dados.nome}!",
                        "pix_copia_cola": pix_copia_cola
                    }
                    
                except Exception as e:
                    raise HTTPException(status_code=500, detail=f"Erro interno: {str(e)}")
            else:
                raise HTTPException(status_code=400, detail="Partida esgotada!")
                
    raise HTTPException(status_code=404, detail="Partida não encontrada.")

@app.post("/simular-pagamento")
def simular_pagamento(dados: ReservaModel):
    for p in partidas_db:
        if p["id"] == dados.partida_id:
            # Se o nome já estiver na lista, retorna sucesso para evitar erro
            if dados.nome in p["participantes"]:
                return {"mensagem": "Pagamento já confirmado anteriormente."}
            
            if p["vagas_ocupadas"] < p["vagas_totais"]:
                p["vagas_ocupadas"] += 1
                p["participantes"].append(dados.nome)
                return {"mensagem": f"Pagamento simulado com sucesso! {dados.nome} foi adicionado à partida."}
            else:
                raise HTTPException(status_code=400, detail="Partida lotada.")
    raise HTTPException(status_code=404, detail="Partida não encontrada.")