import streamlit as st
import requests

st.set_page_config(page_title="Pelada 40º", page_icon="⚽", layout="centered")

st.title("⚽ Pelada 40º")
st.subheader("Garanta sua vaga na partida de 40 minutos!")

st.markdown("---")

API_URL = "http://127.0.0.1:8000"

try:
    response = requests.get(f"{API_URL}/partidas")
    partidas = response.json() if response.status_code == 200 else []
except:
    st.warning("⚠️ Servidor backend inacessível.")
    partidas = []

# --- CRIANDO AS ABAS ---
tab_reservas, tab_lista = st.tabs(["📅 Fazer Reserva", "📋 Lista de Participantes"])

# --- CONTEÚDO DA ABA 1: RESERVAS ---
with tab_reservas:
    st.markdown("### 📅 Partidas Disponíveis Hoje")

    for p in partidas:
        vagas_disponiveis = p["vagas_totais"] - p["vagas_ocupadas"]
        
        col1, col2, col3 = st.columns([2, 2, 1])
        
        with col1:
            st.markdown(f"**⏰ {p['horario']}**")
            st.text(f"Preço: R$ {p['preco']:.2f}")
            
        with col2:
            if vagas_disponiveis > 0:
                st.success(f"🟢 {vagas_disponiveis} vagas livres")
            else:
                st.error("🔴 Partida Lotada")
                
        with col3:
            if vagas_disponiveis > 0:
                if st.button("Garantir Vaga", key=f"btn_{p['id']}"):
                    st.session_state[f"reservando_{p['id']}"] = True
            else:
                st.button("Lotado", disabled=True, key=f"btn_{p['id']}")
                
        if st.session_state.get(f"reservando_{p['id']}", False):
            if f"pix_gerado_{p['id']}" in st.session_state:
                st.success("Vaga garantida! Pague via Pix abaixo:")
                st.text_input("Código Pix (Copia e Cola):", value=st.session_state[f"pix_gerado_{p['id']}"], key=f"pix_{p['id']}")
                
                if st.button("🟢 Simular Pagamento Aprovado", key=f"simular_{p['id']}"):
                    sim_payload = {
                        "partida_id": p["id"],
                        "nome": st.session_state[f"nome_{p['id']}"],
                        "telefone": st.session_state[f"tel_{p['id']}"]
                    }
                    sim_res = requests.post(f"{API_URL}/simular-pagamento", json=sim_payload)
                    if sim_res.status_code == 200:
                        st.success("Pagamento confirmado! Nome adicionado à lista.")
                        st.session_state[f"reservando_{p['id']}"] = False
                        del st.session_state[f"pix_gerado_{p['id']}"]
                        st.rerun()
                    else:
                        st.error("Erro ao simular pagamento.")
                
                st.info("Assim que pagar, o seu nome entra na lista da partida!")
            else:
                with st.form(key=f"form_reserva_{p['id']}"):
                    st.markdown(f"**Preencher dados para o horário: {p['horario']}**")
                    nome_jogador = st.text_input("Seu Nome Completo", key=f"input_nome_{p['id']}")
                    tel_jogador = st.text_input("Seu WhatsApp / Telefone", key=f"input_tel_{p['id']}")
                    
                    submitted = st.form_submit_button("Confirmar e Gerar Pix")
                    
                    if submitted:
                        if nome_jogador and tel_jogador:
                            payload = {
                                "partida_id": p["id"],
                                "nome": nome_jogador,
                                "telefone": tel_jogador
                            }
                            res = requests.post(f"{API_URL}/reservar", json=payload)
                            if res.status_code == 200:
                                dados_reserva = res.json()
                                st.session_state[f"pix_gerado_{p['id']}"] = dados_reserva["pix_copia_cola"]
                                st.session_state[f"nome_{p['id']}"] = nome_jogador
                                st.session_state[f"tel_{p['id']}"] = tel_jogador
                                st.rerun()
                            else:
                                st.error(res.json().get("detail", "Erro ao reservar."))
                        else:
                            st.warning("Por favor, preencha o nome e o telemóvel.")
                        
        st.markdown("---")

# --- CONTEÚDO DA ABA 2: LISTA DE PARTICIPANTES ---
with tab_lista:
    st.markdown("### 📋 Jogadores Confirmados por Horário")
    
    if not partidas:
        st.info("Nenhuma partida encontrada.")
    else:
        for p in partidas:
            with st.expander(f"⚽ Horário: {p['horario']} ({p['vagas_ocupadas']}/{p['vagas_totais']} vagas)"):
                participantes = p.get("participantes", [])
                if participantes:
                    st.markdown("**Atletas confirmados:**")
                    for idx, nome_atleta in enumerate(participantes, 1):
                        st.write(f"{idx}. ⚽ {nome_atleta}")
                else:
                    st.caption("Nenhum atleta confirmado ainda para este horário.")