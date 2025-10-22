from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime, timedelta
import requests
import time
import json
import sqlite3
import os
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
except ImportError:
    psycopg = None

app = Flask(__name__)
app.secret_key = "chave_secreta"

API_LOGIN = "https://simplix-integration.partner1.com.br/api/Login"
API_SIMULATE = "https://simplix-integration.partner1.com.br/api/Proposal/Simulate"
API_ASYNC_RESULT = "https://simplix-integration.partner1.com.br/api/Proposal/SimulateAsyncResult"
API_BALANCE = "https://simplix-integration.partner1.com.br/api/Fgts/balance-request"
WEBHOOK_URL = "https://webhook.site/b1348a3c-d2fd-45a9-93e3-282c83633587"

TOKEN = ""
TOKEN_EXPIRA = 0

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_FILE = "users.db"


def get_conn():
    if DATABASE_URL and psycopg:
        return psycopg.connect(DATABASE_URL)
    return sqlite3.connect(DB_FILE, check_same_thread=False)


def get_placeholder(conn):
    if isinstance(conn, sqlite3.Connection):
        return "?"
    return "%s"


def hash_senha(senha):
    return generate_password_hash(senha)


def verificar_senha(senha_digitada, senha_hash):
    return check_password_hash(senha_hash, senha_digitada)


def is_admin():
    return session.get("role") == "admin"


def init_db():
    conn = get_conn()
    c = conn.cursor()
    ph = get_placeholder(conn)

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            nome TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            role TEXT DEFAULT 'user',
            background TEXT DEFAULT '#133abb,#00e1ff'
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS fila_async (
            id SERIAL PRIMARY KEY,
            transaction_id TEXT,
            cpf TEXT,
            status TEXT DEFAULT 'Aguardando Webhook',
            usuario TEXT,
            data_inclusao TEXT,
            ultima_atualizacao TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS esteira (
            id SERIAL PRIMARY KEY,
            digitador TEXT NOT NULL,
            cpf TEXT NOT NULL,
            bancarizadora TEXT,
            data_hora TEXT,
            valor_contrato REAL
        )
    """)

    try:
        query = f"SELECT * FROM users WHERE nome = {ph}"
        c.execute(query, ("Leonardo",))
        if not c.fetchone():
            admin_user = "Leonardo"
            admin_pass = hash_senha("123456")
            query_insert = f"INSERT INTO users (nome, senha, role) VALUES ({ph}, {ph}, {ph})"
            c.execute(query_insert, (admin_user, admin_pass, "admin"))
            print("✅ Usuário admin criado: login=Leonardo senha=123456")
    except Exception as e:
        print(f"⚠️ Erro ao criar admin: {e}")

    conn.commit()
    conn.close()

@app.before_request
def ensure_db():
    if not hasattr(app, "_db_initialized"):
        try:
            init_db()
            print("✅ Banco inicializado com sucesso.")
        except Exception as e:
            print(f"⚠️ Erro ao inicializar banco: {e}")
        app._db_initialized = True

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        nome = request.form["nome"]
        senha = request.form["senha"]

        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        query = f"SELECT * FROM users WHERE nome = {ph}"
        c.execute(query, (nome,))
        user = c.fetchone()
        conn.close()

        if user and verificar_senha(senha, user[2]):
            session["user"] = nome
            session["role"] = user[3]
            return redirect(url_for("index"))
        return render_template("login.html", erro="Login inválido")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("index"))

    if request.method == "POST":
        nome = request.form["nome"]
        senha = hash_senha(request.form["senha"])
        role = request.form.get("role", "user")

        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        try:
            query = f"INSERT INTO users (nome, senha, role) VALUES ({ph}, {ph}, {ph})"
            c.execute(query, (nome, senha, role))
            conn.commit()
            conn.close()
            return redirect(url_for("gerenciar_usuarios"))
        except Exception:
            return render_template("register.html", erro="Nome já existe!")
    return render_template("register.html")


@app.route("/usuarios")
def gerenciar_usuarios():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("index"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, nome, role FROM users")
    usuarios = c.fetchall()
    conn.close()
    return render_template("usuarios.html", usuarios=usuarios)


@app.route("/editar/<int:user_id>", methods=["GET", "POST"])
def editar_usuario(user_id):
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("index"))

    conn = get_conn()
    c = conn.cursor()
    ph = get_placeholder(conn)
    query = f"SELECT id, nome, role, background FROM users WHERE id={ph}"
    c.execute(query, (user_id,))
    user = c.fetchone()

    if not user:
        conn.close()
        return "Usuário não encontrado", 404

    if request.method == "POST":
        novo_nome = request.form["nome"]
        nova_senha = request.form["senha"]
        novo_background = request.form.get("background", user[3])

        if nova_senha.strip():
            senha_hash = hash_senha(nova_senha)
            query = f"UPDATE users SET nome={ph}, senha={ph}, background={ph} WHERE id={ph}"
            c.execute(query, (novo_nome, senha_hash, novo_background, user_id))
        else:
            query = f"UPDATE users SET nome={ph}, background={ph} WHERE id={ph}"
            c.execute(query, (novo_nome, novo_background, user_id))

        conn.commit()
        conn.close()
        return redirect(url_for("gerenciar_usuarios"))

    conn.close()
    return render_template("editar.html", user=user)


@app.route("/excluir/<int:user_id>", methods=["POST"])
def excluir_usuario(user_id):
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("index"))

    conn = get_conn()
    c = conn.cursor()
    ph = get_placeholder(conn)
    query = f"DELETE FROM users WHERE id={ph}"
    c.execute(query, (user_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("gerenciar_usuarios"))

@app.route("/esteira")
def esteira():
    if "user" not in session:
        return redirect(url_for("login"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT digitador, cpf, bancarizadora, data_hora, valor_contrato FROM esteira ORDER BY id DESC")
    registros = c.fetchall()
    conn.close()
    return render_template("esteira.html", registros=registros)

def gerar_token():
    global TOKEN, TOKEN_EXPIRA
    try:
        dados = {
            "username": "477f702a-4a6f-4b02-b5eb-afcd38da99f8",
            "password": "b5iTIZ2n"
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        resp = requests.post(API_LOGIN, json=dados, headers=headers, timeout=15)
        if resp.status_code == 200 and resp.json().get("success"):
            TOKEN = resp.json()["objectReturn"]["access_token"]
            TOKEN_EXPIRA = time.time() + 3600
            print("🔑 Token Simplix gerado com sucesso.")
            return TOKEN
        else:
            print("❌ Falha ao gerar token Simplix:", resp.text)
    except Exception as e:
        print("⚠️ Erro ao gerar token:", e)
    return ""


def obter_token():
    global TOKEN
    if not TOKEN or time.time() >= TOKEN_EXPIRA:
        TOKEN = gerar_token()
    return TOKEN

def limpar_fila_antiga():
    try:
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)
        limite = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        query = f"DELETE FROM fila_async WHERE data_inclusao < {ph}"
        c.execute(query, (limite,))
        conn.commit()
        conn.close()
        print(" Fila limpa (registros antigos removidos).")
    except Exception as e:
        print(f" Erro ao limpar fila: {e}")

@app.route("/simplix-passo12", methods=["POST"])
def simplix_passo12():
    if "user" not in session:
        return jsonify({"erro": "Sessão expirada. Faça login novamente."}), 401

    data = request.get_json()
    cpf = data.get("cpf")
    usuario = session.get("user")
    token = obter_token()

    if not cpf:
        return jsonify({"erro": "CPF é obrigatório"}), 400

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    payload = {
        "cpf": cpf,
        "callBackBalance": {"url": WEBHOOK_URL, "method": "POST"}
    }

    try:
        print(f"📩 Enviando {cpf} para balance-request Simplix...")
        resp = requests.post(API_BALANCE, json=payload, headers=headers, timeout=60)
        data_resp = resp.json()
        transaction_id = data_resp.get("objectReturn", {}).get("transactionId")

        if not transaction_id:
            return jsonify({"erro": "Simplix não retornou transactionId.", "resposta": data_resp}), 400

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        query = f"""
            INSERT INTO fila_async (transaction_id, cpf, status, usuario, data_inclusao, ultima_atualizacao)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """
        c.execute(query, (transaction_id, cpf, "Aguardando Webhook", usuario, agora, agora))
        conn.commit()
        conn.close()

        print(f"✅ CPF {cpf} inserido na fila (TransactionID={transaction_id})")
        limpar_fila_antiga()

        return jsonify({
            "sucesso": True,
            "transactionId": transaction_id,
            "mensagem": f"CPF {cpf} adicionado à fila."
        }), 200

    except Exception as e:
        print(f"❌ Erro no /simplix-passo12: {e}")
        return jsonify({"erro": str(e)}), 500

@app.route("/fila")
def visualizar_fila():
    if "user" not in session:
        return redirect(url_for("login"))

    pagina = int(request.args.get("pagina", 1))
    por_pagina = 20
    offset = (pagina - 1) * por_pagina

    conn = get_conn()
    c = conn.cursor()
    ph = get_placeholder(conn)

    c.execute("SELECT COUNT(*) FROM fila_async")
    total_registros = c.fetchone()[0]

    query = f"""
        SELECT transaction_id, cpf, status, usuario, data_inclusao, ultima_atualizacao
        FROM fila_async
        ORDER BY id DESC
        LIMIT {ph} OFFSET {ph}
    """
    c.execute(query, (por_pagina, offset))
    registros = c.fetchall()
    conn.close()

    total_paginas = max(1, (total_registros + por_pagina - 1) // por_pagina)
    return render_template("fila.html",
                           registros=registros,
                           pagina=pagina,
                           total_paginas=total_paginas)

@app.route("/excluir-fila/<transaction_id>", methods=["POST"])
def excluir_fila(transaction_id):
    try:
        transaction_id = transaction_id.strip()
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)
        query = f"DELETE FROM fila_async WHERE TRIM(transaction_id)={ph}"
        c.execute(query, (transaction_id,))
        conn.commit()
        linhas = c.rowcount
        conn.close()

        if linhas > 0:
            print(f"🗑️ CPF removido da fila: {transaction_id}")
            return jsonify({"success": True, "mensagem": f"Registro {transaction_id} removido com sucesso."}), 200
        else:
            print(f"⚠️ Nenhum registro encontrado com TransactionID={transaction_id}")
            return jsonify({"success": False, "erro": "Registro não encontrado."}), 404

    except Exception as e:
        print(f"❌ Erro ao excluir da fila: {e}")
        return jsonify({"success": False, "erro": str(e)}), 500

@app.route("/index")
def index():
    if "user" not in session:
        return redirect(url_for("login"))

    cor1 = session.get("cor1", "#133abb")
    cor2 = session.get("cor2", "#00e1ff")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM fila_async")
    total_fila = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM esteira")
    total_esteira = c.fetchone()[0]
    conn.close()

    return render_template("index.html",
                           usuario=session["user"],
                           cor1=cor1,
                           cor2=cor2,
                           total_fila=total_fila,
                           total_esteira=total_esteira)


@app.route("/cadastrar")
def cadastrar():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("cadastrar.html")

@app.route("/excluir-proposta/<cpf>", methods=["POST"])
def excluir_proposta(cpf):
    try:
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)
        query = f"DELETE FROM esteira WHERE cpf={ph}"
        c.execute(query, (cpf,))
        conn.commit()
        conn.close()
        print(f"🗑️ Proposta excluída: {cpf}")
        return jsonify({"success": True, "mensagem": "Proposta excluída com sucesso."}), 200
    except Exception as e:
        print(f"❌ Erro ao excluir proposta: {e}")
        return jsonify({"success": False, "erro": str(e)}), 500

@app.route("/home")
def home():
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8600)
