import streamlit as st
import requests

# URL para testes locais. Quando for enviar pro GitHub/Render, mantenha o link do Render.
API_URL = "http://127.0.0.1:8000"

st.set_page_config(page_title="Pelada 40º", page_icon="⚽", layout="centered")

st.title("⚽ Pelada 40º")
st.subheader("Garanta sua vaga na partida de 40 minutos!")

# Criando as 3 abas
tab_reserva, tab_lista, tab_admin = st.tabs(["📅 Fazer Reserva", "📜 Lista de Participantes", "⚙️ Sala de Controle (Admin)"])

# --- ABA 1: FAZER RESERVA ---
with tab_reserva:
    st.header("Partidas Disponíveis Hoje")
    
    try:
        response = requests.get(f"{API_URL}/partidas")
        if response.status_code == 200:
            partidas = response.json()
            
            for p in partidas:
                with st.container():
                    col1, col2, col3 = st.columns([2, 2, 1.5])
                    with col1:
                        st.write(f"⏰ **{p['horario']}**")
                        st.write(f"Preço: **R$ {p['preco']:.2f}**")
                    with col2:
                        if p['vagas_livres'] > 0:
                            st.success(f"🟢 {p['vagas_livres']} vagas livres")
                        else:
                            st.error("🔴 Esgotado")
                    with col3:
                        if st.button("Garantir Vaga", key=f"btn_{p['id']}"):
                            st.session_state['partida_selecionada'] = p['id']
                            st.session_state['horario_selecionado'] = p['horario']
                    st.divider()
        else:
            st.error("Erro ao carregar as partidas.")
    except Exception:
        st.error("Não foi possível conectar ao servidor backend.")

    if 'partida_selecionada' in st.session_state:
        st.subheader(f"Confirmar Reserva - Horário {st.session_state['horario_selecionado']}")
        
        nome = st.text_input("Seu Nome Completo:")
        telefone = st.text_input(
            "Seu WhatsApp com DDD:",
            placeholder="11999999999"
        )
        
        if st.button("Gerar Pagamento PIX"):
            if nome.strip() and telefone.strip():
                payload = {
                    "partida_id": st.session_state['partida_selecionada'],
                    "nome_jogador": nome.strip(),
                    "telefone": telefone.strip()
                }
                res = requests.post(f"{API_URL}/reservar", json=payload)
                
                if res.status_code == 200:
                    data = res.json()
                    st.success("Reserva gerada com sucesso! Copie o código PIX abaixo:")
                    st.code(data['pix_copia_cola'], language="text")
                else:
                    st.error(f"Erro ao reservar: {res.json().get('detail', 'Tente novamente')}")
            else:
                st.warning("Por favor, informe seu nome e WhatsApp com DDD.")

# --- ABA 2: LISTA DE PARTICIPANTES ---
with tab_lista:
    st.header("Lista de Confirmados")
    try:
        response = requests.get(f"{API_URL}/partidas")
        if response.status_code == 200:
            partidas = response.json()
            partida_opcoes = {f"{p['horario']}": p['id'] for p in partidas}
            
            escolha = st.selectbox("Selecione o Horário:", list(partida_opcoes.keys()))
            partida_id = partida_opcoes[escolha]
            
            res_lista = requests.get(f"{API_URL}/participantes/{partida_id}")
            if res_lista.status_code == 200:
                jogadores = res_lista.json().get("jogadores", [])
                if jogadores:
                    for idx, jog in enumerate(jogadores, 1):
                        st.write(f"**{idx}.** {jog}")
                else:
                    st.info("Nenhum participante confirmado ainda para este horário.")
    except Exception:
        st.error("Erro ao carregar lista de participantes.")

# --- ABA 3: SALA DE CONTROLE (ADMIN) ---
with tab_admin:
    st.header("⚙️ Painel do Administrador")
    
    senha_admin = st.text_input("Senha de Acesso:", type="password")
    
    if senha_admin == "admin123":
        st.success("Acesso Autorizado!")
        headers = {"x-admin-key": senha_admin}
        
        try:
            res_p = requests.get(f"{API_URL}/partidas")
            if res_p.status_code == 200:
                partidas = res_p.json()
                
                st.subheader("1. Editar Partida (Horário / Preço / Vagas)")
                id_edit = st.selectbox("Escolha a partida para editar:", [p['id'] for p in partidas], format_func=lambda x: f"Partida ID {x}")
                
                partida_atual = next(p for p in partidas if p['id'] == id_edit)
                novo_horario = st.text_input("Horário:", value=partida_atual['horario'])
                novo_preco = st.number_input("Preço (R$):", value=float(partida_atual['preco']))
                novas_vagas = st.number_input("Vagas Totais:", value=int(partida_atual['vagas_totais']))
                
                if st.button("Salvar Alterações da Partida"):
                    update_payload = {
                        "horario": novo_horario,
                        "preco": novo_preco,
                        "vagas_totais": novas_vagas
                    }
                    res_up = requests.put(f"{API_URL}/admin/partida/{id_edit}", json=update_payload, headers=headers)
                    if res_up.status_code == 200:
                        st.success("Partida atualizada com sucesso!")
                        st.rerun()
                    else:
                        st.error("Erro ao atualizar partida.")

                st.divider()
                st.subheader("2. Remover Jogador da Lista")
                partida_rem = st.selectbox("Selecione a Partida:", [p['id'] for p in partidas], key="select_rem", format_func=lambda x: f"Partida ID {x}")
                
                res_j = requests.get(f"{API_URL}/participantes/{partida_rem}")
                if res_j.status_code == 200:
                    lista_j = res_j.json().get("jogadores", [])
                    if lista_j:
                        jogador_remover = st.selectbox("Selecione o Jogador:", lista_j)
                        if st.button("Remover Jogador"):
                            rem_payload = {"partida_id": partida_rem, "nome_jogador": jogador_remover}
                            res_del = requests.post(f"{API_URL}/admin/remover-jogador", json=rem_payload, headers=headers)
                            if res_del.status_code == 200:
                                st.warning(f"Jogador {jogador_remover} removido!")
                                st.rerun()
                    else:
                        st.info("Sem jogadores nesta partida.")
        except Exception:
            st.error("Erro ao carregar dados do admin.")
    elif senha_admin != "":
        st.error("Senha incorreta!")