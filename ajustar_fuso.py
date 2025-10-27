import sqlite3
from datetime import datetime

# Caminho do banco
DB_PATH = "users.db"

def ajustar_horarios():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("⏳ Ajustando horários no banco...")

    # Comandos SQL para ajustar o fuso horário (-3h)
    queries = [
        """
        UPDATE fila_async
        SET data_inclusao = strftime('%Y-%m-%d %H:%M:%S',
            datetime(data_inclusao, '-3 hours'))
        WHERE data_inclusao IS NOT NULL;
        """,
        """
        UPDATE fila_async
        SET ultima_atualizacao = strftime('%Y-%m-%d %H:%M:%S',
            datetime(ultima_atualizacao, '-3 hours'))
        WHERE ultima_atualizacao IS NOT NULL;
        """,
        """
        UPDATE esteira
        SET data_hora = strftime('%Y-%m-%d %H:%M:%S',
            datetime(data_hora, '-3 hours'))
        WHERE data_hora IS NOT NULL;
        """
    ]

    total_alteradas = 0
    for query in queries:
        cur.execute(query)
        total_alteradas += cur.rowcount

    conn.commit()
    conn.close()

    print(f"✅ Ajuste concluído! {total_alteradas} registros atualizados com sucesso.")
    print(f"🕒 Concluído em: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    ajustar_horarios()
