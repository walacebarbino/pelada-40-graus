import sqlite3

DB_PATH = "pelada.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Tabela de partidas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS partidas (
            id INTEGER PRIMARY KEY,
            horario TEXT NOT NULL,
            vagas_totais INTEGER NOT NULL,
            preco REAL NOT NULL
        )
    """)
    
    # Tabela de reservas
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reservas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partida_id INTEGER NOT NULL,
            nome_jogador TEXT NOT NULL,
            pix_codigo TEXT,
            status TEXT DEFAULT 'confirmado',
            FOREIGN KEY (partida_id) REFERENCES partidas (id)
        )
    """)

        # Adiciona telefone em bancos existentes, sem apagar reservas.
    cursor.execute("PRAGMA table_info(reservas)")
    colunas = {coluna[1] for coluna in cursor.fetchall()}

    if "telefone" not in colunas:
        cursor.execute("ALTER TABLE reservas ADD COLUMN telefone TEXT")
    
    # Popula as partidas iniciais se a tabela estiver vazia
    cursor.execute("SELECT COUNT(*) FROM partidas")
    if cursor.fetchone()[0] == 0:
        cursor.executemany("""
            INSERT INTO partidas (id, horario, vagas_totais, preco)
            VALUES (?, ?, ?, ?)
        """, [
            (1, "19:00 - 19:40", 10, 18.0),
            (2, "19:45 - 20:25", 10, 18.0),
            (3, "20:30 - 21:10", 10, 18.0)
        ])
    
    conn.commit()
    conn.close()

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn