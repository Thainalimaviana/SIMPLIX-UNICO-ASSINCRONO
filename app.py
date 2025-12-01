from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime, timedelta
import pytz
import requests
import time
import json
import sqlite3
import threading
import os, re
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psycopg
except ImportError:
    psycopg = None

app = Flask(__name__)
app.secret_key = "chave_secreta"

API_LOGIN = "https://simplix-integration.partner1.com.br/api/Login"
WEBHOOK_URL = "https://simplix-unico-assincrono.onrender.com/webhook-simplix"
API_BALANCE = "https://simplix-integration.partner1.com.br/api/Fgts/balance-request"

TOKEN = ""
TOKEN_EXPIRA = 0

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_FILE = "users.db"


def get_conn():
    if DATABASE_URL and psycopg:
        return psycopg.connect(DATABASE_URL)
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def adapt_queries_for_db(conn, query):
    if isinstance(conn, sqlite3.Connection):
        return query.replace("%s", "?")
    return query

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

    c.execute("""
        CREATE TABLE IF NOT EXISTS simulacoes (
            id SERIAL PRIMARY KEY,
            transaction_id TEXT UNIQUE,
            simulation_id TEXT,
            periodos TEXT,
            cpf TEXT,
            bancarizadora TEXT,
            tabela_id TEXT,
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        query = f"SELECT * FROM users WHERE nome = {ph}"
        query = adapt_queries_for_db(conn, query)
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
            return redirect(url_for("dashboard"))
        return render_template("login.html", erro="Login inválido")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("dashboard"))

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
        return redirect(url_for("dashboard"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, nome, role FROM users")
    usuarios = c.fetchall()
    conn.close()
    return render_template("usuarios.html", usuarios=usuarios)


@app.route("/editar/<int:user_id>", methods=["GET", "POST"])
def editar_usuario(user_id):
    if "user" not in session or session.get("role") != "admin":
        return redirect(url_for("usuarios"))

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
        "callBackBalance": {
            "url": "https://simplix-unico-assincrono.onrender.com/webhook-simplix",
            "method": "POST"
        }
    }

    try:
        print(f"📩 Enviando {cpf} para balance-request Simplix...")
        resp = requests.post(API_BALANCE, json=payload, headers=headers, timeout=60)
        data_resp = resp.json()
        transaction_id = data_resp.get("objectReturn", {}).get("transactionId")

        if not transaction_id:
            return jsonify({"erro": "Simplix não retornou transactionId.", "resposta": data_resp}), 400

        agora = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        query = f"""
            INSERT INTO fila_async (transaction_id, cpf, status, usuario, data_inclusao, ultima_atualizacao)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
        """
        query = adapt_queries_for_db(conn, query)
        c.execute(query, (transaction_id, cpf, "Aguardando Webhook", usuario, agora, agora))

        query_sim = f"""
            INSERT INTO simulacoes (transaction_id, cpf, bancarizadora, tabela_id, periodos)
            VALUES ({ph}, {ph}, {ph}, {ph}, {ph})
            ON CONFLICT (transaction_id) DO NOTHING
        """
        c.execute(query_sim, (transaction_id, cpf, None, None, "[]"))

        conn.commit()
        conn.close()

        print(f"✅ CPF {cpf} inserido na fila e registrado em simulacoes (TransactionID={transaction_id})")
        limpar_fila_antiga()

        return jsonify({
            "sucesso": True,
            "transactionId": transaction_id,
            "mensagem": f"CPF {cpf} adicionado à fila."
        }), 200

    except Exception as e:
        print(f"❌ Erro no /simplix-passo12: {e}")
        return jsonify({"erro": str(e)}), 500

@app.route("/simplix-cadastrar", methods=["POST"])
def simplix_cadastrar():
    try:
        form = request.form
        simulation_id = form.get("simulationId")

        conn = get_conn()
        cur = conn.cursor()
        ph = get_placeholder(conn)

        cur.execute(f"SELECT periodos FROM simulacoes WHERE transaction_id = {ph} LIMIT 1", (form.get('transactionId'),))
        row = cur.fetchone()
        conn.close()

        if row and row[0]:
            try:
                periodos = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            except Exception:
                periodos = []
        else:
            periodos = []

        data = {
            "cliente": {
                "rg": form.get("rg"),
                "cpf": form.get("cpf"),
                "nome": form.get("nome"),
                "email": form.get("email"),
                "endereco": {
                    "cep": form.get("cep"),
                    "bairro": form.get("bairro"),
                    "cidade": form.get("cidade"),
                    "estado": form.get("estado"),
                    "numero": form.get("numero"),
                    "logradouro": form.get("logradouro"),
                    "complemento": form.get("complemento")
                },
                "ocupacao": form.get("ocupacao"),
                "telefone": form.get("telefone"),
                "estadoCivil": form.get("estadoCivil"),
                "contaBancaria": {
                    "conta": form.get("conta"),
                    "agencia": form.get("agencia"),
                    "tipoDeConta": form.get("tipoDeConta"),
                    "codigoDoBanco": form.get("codigoDoBanco"),
                    "digitoDaConta": form.get("digitoDaConta"),
                    "tipoDeOperacao": form.get("tipoDeOperacao")
                },
                "nacionalidade": form.get("nacionalidade"),
                "dataDeNascimento": form.get("dataDeNascimento")
            },
            "operacao": {
                "periodos": periodos,
                "simulationId": simulation_id or ""
            },
            "loginDigitador": "477f702a-4a6f-4b02-b5eb-afcd38da99f8",
            "callback": {
                "url": "https://simplix-unico-assincrono.onrender.com/webhook-simplix",
                "method": "POST"
            }
        }

        print("📥 Dados montados para API Simplix:")
        print(json.dumps(data, indent=2, ensure_ascii=False))

        headers = {
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        print("📤 Enviando proposta para API Simplix...")
        response = requests.post(
            "https://simplix-integration.partner1.com.br/api/Proposal/Create",
            json=data,
            headers=headers,
            timeout=30
        )

        print("📨 Retorno da API Simplix:", response.text)
        result = response.json()

        if result.get("success") and "objectReturn" in result:
            link = result["objectReturn"].get("link")
            proposta = result["objectReturn"].get("proposta")
            proposta_id = result["objectReturn"].get("propostaId")

            print(f"✅ Proposta criada com sucesso: {proposta} | ID={proposta_id}")
            return render_template(
                "cadastro_finalizado.html",
                link=link,
                proposta=proposta,
                proposta_id=proposta_id
            )

        descricao = ""
        try:
            descricao = result.get("objectReturn", {}).get("description", "")
        except Exception:
            pass

        return render_template(
            "cadastro_finalizado.html",
            erro=descricao or "Falha ao criar proposta. Verifique os dados e tente novamente."
        )

    except Exception as e:
        print("❌ Erro ao cadastrar:", str(e))
        return render_template("cadastro_finalizado.html", erro=str(e))

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
    query = adapt_queries_for_db(conn, query)
    c.execute(query, (por_pagina, offset))
    registros = c.fetchall()
    conn.close()

    total_paginas = max(1, (total_registros + por_pagina - 1) // por_pagina)
    return render_template("fila.html",
                           registros=registros,
                           pagina=pagina,
                           total_paginas=total_paginas)

@app.route("/api/fila-atualizada")
def fila_atualizada():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT transaction_id, cpf, status, usuario, data_inclusao, ultima_atualizacao
            FROM fila_async
            ORDER BY id DESC
        """)
        registros = c.fetchall()
        conn.close()

        return jsonify([
            {
                "transaction_id": r[0],
                "cpf": r[1],
                "status": r[2],
                "usuario": r[3],
                "data_inclusao": r[4],
                "ultima_atualizacao": r[5]
            }
            for r in registros
        ])
    except Exception as e:
        print(f"❌ Erro ao buscar fila atualizada: {e}")
        return jsonify([])


@app.route("/excluir-fila/<transaction_id>", methods=["POST"])
def excluir_fila(transaction_id):
    try:
        transaction_id = transaction_id.strip()
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)
        query = f"DELETE FROM fila_async WHERE TRIM(transaction_id)={ph}"
        query = adapt_queries_for_db(conn, query)
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

    transaction_id = request.args.get("transactionId")
    tabela_id = request.args.get("tabelaId")
    bancarizadora = request.args.get("bancarizadora")

    cpf_url = request.args.get("cpf", "")

    conn = get_conn()
    cur = conn.cursor()
    ph = get_placeholder(conn)

    query = f"""
        SELECT simulation_id, periodos, cpf
        FROM simulacoes
        WHERE transaction_id = {ph}
        LIMIT 1
    """
    cur.execute(query, (transaction_id,))
    row = cur.fetchone()
    conn.close()

    if not row:
        return "Simulação não encontrada. Refaça o processo.", 404

    simulation_id, periodos_json, cpf_db = row

    cpf = cpf_url or cpf_db or ""

    return render_template(
        "cadastrar.html",
        transaction_id=transaction_id,
        tabela_id=tabela_id,
        bancarizadora=bancarizadora,
        simulation_id=simulation_id,
        cpf=cpf,
        periodos=periodos_json
    )

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

@app.route("/")
def home():
    return redirect("/dashboard")

@app.route("/webhook-simplix", methods=["POST"])
def webhook_simplix():
    try:
        data = request.get_json(force=True)
        print(f"📬 Webhook recebido Simplix:\n{json.dumps(data, indent=2, ensure_ascii=False)}")

        transaction_id = (
            data.get("transactionId")
            or data.get("objectReturn", {}).get("transactionId")
        )

        descricao = (
            data.get("objectReturn", {}).get("description")
            or data.get("description")
            or data.get("observacao")
            or data.get("statusDescription")
            or "Sem descrição"
        )

        threading.Thread(target=atualizar_status, args=(transaction_id, descricao)).start()
        return jsonify({"success": True}), 200

    except Exception as e:
        print(f"❌ Erro no webhook Simplix: {e}")
        return jsonify({"erro": str(e)}), 500


def atualizar_status(transaction_id, descricao):
    try:
        if not transaction_id:
            print("⚠️ Webhook sem transactionId, ignorando.")
            return

        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)
        agora = datetime.now(pytz.timezone("America/Sao_Paulo")).strftime("%Y-%m-%d %H:%M:%S")

        query = "UPDATE fila_async SET status={p}, ultima_atualizacao={p} WHERE transaction_id={p}".format(p=ph)
        query = adapt_queries_for_db(conn, query)
        c.execute(query, (descricao, agora, transaction_id))
        conn.commit()
        conn.close()

        print(f"✅ Transaction {transaction_id} atualizada com: {descricao}")

    except Exception as e:
        print(f"❌ Erro no update assíncrono: {e}")

@app.route("/health")
def health():
    return "OK", 200

@app.route("/simulate/<transaction_id>")
def simulate(transaction_id):
    try:
        token = obter_token()
        url = "https://simplix-integration.partner1.com.br/api/Fgts/simulate"

        payload = {"transactionId": transaction_id}
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        print(f"📤 Enviando simulação para Simplix (TransactionID={transaction_id})...")
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        data = resp.json()
        print("📨 Retorno da API Simplix:", json.dumps(data, indent=2, ensure_ascii=False))

        if not data.get("success"):
            mensagem = f"Erro: {data.get('message', 'Falha na simulação')}"
            return render_template("simular.html", transaction_id=transaction_id, mensagem=mensagem)

        simulacoes = data.get("objectReturn", {}).get("retornoSimulacao", [])
        if not simulacoes:
            return render_template(
                "simular.html",
                transaction_id=transaction_id,
                mensagem="Cliente sem saldo disponível.",
                tabelas=[]
            )

        tabelas = []
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        for s in simulacoes:
            simulation_id = s.get("simulationId")
            bancarizadora = s.get("bancarizadora")
            tabela_id = s.get("tabelaId")
            tabela_titulo = s.get("tabelaTitulo")
            valor_liquido = s.get("valorLiquido")
            taxa = s.get("detalhes", {}).get("taxa")
            periodos = s.get("detalhes", {}).get("parcelas", [])

            tabelas.append({
                "simulationId": simulation_id,
                "bancarizadora": bancarizadora,
                "tabelaId": tabela_id,
                "tabelaTitulo": tabela_titulo,
                "valorLiquido": valor_liquido,
                "taxa": taxa
            })

            query = f"""
                INSERT INTO simulacoes (transaction_id, simulation_id, cpf, bancarizadora, tabela_id, periodos)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph})
                ON CONFLICT (transaction_id) DO UPDATE
                SET simulation_id = EXCLUDED.simulation_id,
                    bancarizadora = EXCLUDED.bancarizadora,
                    tabela_id = EXCLUDED.tabela_id,
                    periodos = EXCLUDED.periodos
            """
            query = adapt_queries_for_db(conn, query)
            c.execute(query, (
                transaction_id,
                simulation_id,
                None,
                bancarizadora,
                tabela_id,
                json.dumps(periodos, ensure_ascii=False)
            ))

            print(f"Simulação salva: SimulationID={simulation_id} | Bancarizadora={bancarizadora} | TabelaID={tabela_id}")

        conn.commit()
        conn.close()

        return render_template(
            "simular.html",
            transaction_id=transaction_id,
            mensagem="Tabelas disponíveis para simulação",
            tabelas=tabelas
        )

    except Exception as e:
        print(f"❌ Erro ao simular transaction {transaction_id}: {e}")
        return render_template(
            "simular.html",
            transaction_id=transaction_id,
            mensagem=f"Erro ao processar simulação: {e}",
            tabelas=[]
        )
#*************************************************************************************************** 
#PRESENÇA 

PRESENCA_TOKEN = ""
PRESENCA_TOKEN_EXPIRA = 0

def presenca_token():
    global PRESENCA_TOKEN, PRESENCA_TOKEN_EXPIRA

    if PRESENCA_TOKEN and time.time() < PRESENCA_TOKEN_EXPIRA:
        return PRESENCA_TOKEN

    try:
        url = "https://presenca-bank-api.azurewebsites.net/login"
        payload = {
            "login": "30612588840_BWzs",
            "senha": "Tech@@2025"
        }

        headers = {"Content-Type": "application/json"}
        r = requests.post(url, json=payload, headers=headers, timeout=20)

        data = r.json()
        PRESENCA_TOKEN = data.get("token") or data.get("accessToken") or None
        PRESENCA_TOKEN_EXPIRA = time.time() + 3600

        print("🔑 TOKEN PRESENÇA GERADO:", PRESENCA_TOKEN)
        return PRESENCA_TOKEN

    except Exception as e:
        print("❌ ERRO AO GERAR TOKEN PRESENÇA:", e)
        return None


@app.route("/presenca")
def presenca():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("presenca.html")

@app.route("/api/presenca/gerar-link", methods=["POST"])
def api_presenca_gerar_link():
    try:
        data = request.get_json()

        nome = data.get("nome")
        cpf = limpar_cpf(data.get("cpf"))
        telefone_raw = data.get("telefone")
        cpfRep = limpar_cpf(data.get("cpfRep"))
        nomeRep = data.get("nomeRep")

        ddd, telefone = normalizar_telefone(telefone_raw)
        if not ddd:
            return jsonify({"html": "<b class='status-erro'>Telefone inválido.</b>"})

        token = presenca_token()
        if not token:
            return jsonify({"html": "<b class='status-erro'>Erro ao gerar token.</b>"}), 500

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "cpf": cpf,
            "nome": nome,
            "telefone": f"{ddd}{telefone}",
            "cpfRepresentante": cpfRep,
            "nomeRepresentante": nomeRep,
            "produtoId": 28
        }

        resp = requests.post(
            "https://presenca-bank-api.azurewebsites.net/consultas/termo-inss",
            json=payload,
            headers=headers,
            timeout=20
        )

        resposta = resp.json()
        print("📩 RETORNO GERAR TERMO:", resposta)

        link = (
            resposta.get("shortUrl")
            or resposta.get("autorizacaoId")
            or resposta.get("objectReturn", {}).get("shortUrl")
        )

        v_payload = {"cpf": cpf}

        resp2 = requests.post(
            "https://presenca-bank-api.azurewebsites.net/v3/operacoes/consignado-privado/consultar-vinculos",
            json=v_payload,
            headers=headers,
            timeout=20
        )

        vinc = resp2.json()
        matricula = None

        try:
            matricula = vinc["objectReturn"][0]["matricula"]
        except:
            pass

        html = f"""
        <b style='font-size:18px;'>Link Gerado:</b><br><br>

        <div style="display:flex; flex-direction:column; align-items:center; width:100%;">

            <input value="{link}" readonly
                style="
                    width:90%;
                    padding:12px 14px;
                    border-radius:12px;
                    border:1px solid #bfbfbf;
                    background: var(--bg-input);
                    color: var(--cor-texto);
                    font-size:15px;
                    margin-bottom:15px;
                ">

            <button class='copy-btn' onclick="navigator.clipboard.writeText('{link}')"
                style="
                    background:#0aff73;
                    color:#000;
                    padding:12px 20px;
                    border-radius:12px;
                    border:none;
                    cursor:pointer;
                    font-weight:bold;
                    font-size:15px;
                    transition:0.25s;
                "
                onmouseover="this.style.transform='scale(1.06)'"
                onmouseout="this.style.transform='scale(1)'"
            >
                <i class="fa fa-copy"></i> Copiar
            </button>

        </div>
        """

        return jsonify({
            "sucesso": True,
            "html": html,
            "debug": resposta
        })

    except Exception as e:
        return jsonify({
            "sucesso": False,
            "html": f"<b class='status-erro'>Erro: {str(e)}</b>"
        }), 500

@app.route("/api/presenca/consultar", methods=["POST"])
def api_presenca_consultar():
    try:
        data = request.get_json()
        cpf = data.get("cpf")

        token = presenca_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {"cpf": cpf}

        r = requests.post(
            "https://presenca-bank-api.azurewebsites.net/v3/operacoes/consignado-privado/consultar-margem",
            json=payload,
            headers=headers,
            timeout=20
        )

        resposta = r.json()
        print("📌 RESPOSTA MARGEM:", resposta)

        if not isinstance(resposta, list) or len(resposta) == 0:
            return jsonify({
                "erro": True,
                "html": "<div class='resultado-erro'><i class='fa fa-times-circle'></i> Nenhuma matrícula encontrada.</div>"
            })

        info = resposta[0]

        dados = {
            "cpf": cpf,
            "nomeMae": info.get("nomeMae", ""),
            "sexo": info.get("sexo", ""),
            "dataNascimento": info.get("dataNascimento", ""),
            "matricula": info.get("matricula", ""),
            "numeroInscricaoEmpregador": info.get("numeroInscricaoEmpregador", ""),
            "tipoInscricaoEmpregador": info.get("tipoInscricaoEmpregador", 1),
        }

        html = f"""
        <div class='resultado-wrapper'>

            <div style="text-align:center; font-size:18px; font-weight:bold; margin-bottom:20px;">
                CPF: {cpf}
            </div>

            <div class='resultado-grid'>
                <div class='resultado-item'><b>Matrícula:</b> {dados['matricula']}</div>
                <div class='resultado-item'><b>Inscrição Empregador:</b> {dados['numeroInscricaoEmpregador']}</div>
                <div class='resultado-item'><b>Nome da mãe:</b> {dados['nomeMae']}</div>
                <div class='resultado-item'><b>Sexo:</b> {dados['sexo']}</div>
                <div class='resultado-item'><b>Data nascimento:</b> {dados['dataNascimento']}</div>
            </div>

        </div>
        """

        return jsonify({"erro": False, "html": html, "dados": dados})

    except Exception as e:
        return jsonify({
            "erro": True,
            "html": f"<div class='resultado-erro'><i class='fa fa-times-circle'></i> Erro: {str(e)}</div>"
        })
    
@app.route("/api/presenca/tabelas", methods=["POST"])
def api_presenca_tabelas():
    try:
        data = request.get_json()

        token = presenca_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "tomador": {
                "telefone": {
                    "ddd": data["ddd"],
                    "numero": data["telefone"]
                },
                "cpf": data["cpf"],
                "nome": data["nome"],
                "dataNascimento": data["dataNascimento"],
                "nomeMae": data["nomeMae"],
                "email": data["email"],
                "sexo": data["sexo"],
                "vinculoEmpregaticio": {
                    "cnpjEmpregador": data["numeroInscricaoEmpregador"],
                    "registroEmpregaticio": data["matricula"]
                },
                "dadosBancarios": {
                    "codigoBanco": data["banco"],
                    "agencia": data["agencia"],
                    "conta": data["conta"],
                    "digitoConta": data["digito"],
                    "formaCredito": data["formaCredito"]
                },
                "endereco": data["endereco"]
            },
            "proposta": {
                "valorSolicitado": data["valorSolicitado"],
                "quantidadeParcelas": data["parcelas"],
                "produtoId": 28,
                "valorParcela": data["valorParcela"]
            },
            "documentos": []
        }

        r = requests.post(
            "https://presenca-bank-api.azurewebsites.net/v3/tabelas/simulacao/inss/disponiveis",
            json=payload,
            headers=headers,
            timeout=20
        )

        tabelas = r.json()
        print("📌 TABELAS DISPONÍVEIS:", tabelas)

        if "errors" in tabelas:
            return jsonify({
                "sucesso": False,
                "errors": tabelas["errors"]
            })

        return jsonify({"sucesso": True, "tabelas": tabelas})

    except Exception as e:
        return jsonify({"sucesso": False, "erro": str(e)})
    
@app.route("/api/presenca/criar-operacao", methods=["POST"])
def api_presenca_criar_operacao():
    try:
        data = request.get_json()

        token = presenca_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        payload = {
            "type": "credito-privado-v3",
            "tomador": data["tomador"],
            "proposta": data["proposta"],
            "documentos": []
        }

        r = requests.post(
            "https://presenca-bank-api.azurewebsites.net/v3/operacoes",
            json=payload,
            headers=headers,
            timeout=20
        )

        resp = r.json()
        print("📌 CRIAR OPERAÇÃO:", resp)

        return jsonify(resp)

    except Exception as e:
        return jsonify({"erro": str(e)})

    
#************************************************************************************************************
# Dashboard

@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

#*************************************************************************************************************
#C6 Bank

C6_TOKEN = None
C6_EXPIRA = 0

@app.route("/c6bank")
def c6bank_page():
    return render_template("c6bank.html")

def c6_gerar_token():
    import time
    global C6_TOKEN, C6_EXPIRA

    agora = time.time()

    if C6_TOKEN and agora < C6_EXPIRA:
        return C6_TOKEN

    url = "https://marketplace-proposal-service-api-p.c6bank.info/auth/token"

    payload = {
        "username": "51077297890_004500",
        "password": "Tech@2025"
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    r = requests.post(url, data=payload, headers=headers, timeout=20)

    if r.status_code != 200:
        print("❌ Erro ao gerar token C6:", r.text)
        return None

    token_data = r.json()
    C6_TOKEN = token_data.get("access_token")

    C6_EXPIRA = agora + 25

    print("🔄 Novo token C6 gerado!")

    return C6_TOKEN

def normalizar_data(data_str):
    data_str = data_str.replace("/", "-").replace(" ", "-")

    if re.fullmatch(r"\d{8}", data_str):
        return f"{data_str[4:8]}-{data_str[2:4]}-{data_str[0:2]}"

    formatos = [
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d-%m-%y",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d/%m/%y",
    ]

    for fmt in formatos:
        try:
            return datetime.strptime(data_str, fmt).strftime("%Y-%m-%d")
        except:
            pass

    return None

def limpar_cpf(cpf_raw):
    return re.sub(r"\D", "", cpf_raw)

def normalizar_telefone(numero_raw):
    numeros = re.sub(r"\D", "", numero_raw)

    if numeros.startswith("55") and len(numeros) > 11:
        numeros = numeros[2:]

    if len(numeros) < 10:
        return None, None

    ddd = numeros[:2]
    telefone = numeros[2:]

    return ddd, telefone

@app.route("/api/c6bank/gerar-link", methods=["POST"])
def api_c6_gerar_link():
    data = request.get_json()

    nome = data.get("nome")
    cpf = limpar_cpf(data.get("cpf"))
    nascimento = data.get("nascimento")
    telefone_raw = data.get("telefone")

    nascimento_final = normalizar_data(nascimento)
    if not nascimento_final:
        return jsonify({"html": "<b class='status-erro'>Data inválida.</b>"})

    ddd, telefone = normalizar_telefone(telefone_raw)
    if not ddd:
        return jsonify({"html": "<b class='status-erro'>Telefone inválido.</b>"})

    token = c6_gerar_token()
    if not token:
        return jsonify({"html": "<b class='status-erro'>Erro ao gerar token do C6.</b>"}), 500

    url = "https://marketplace-proposal-service-api-p.c6bank.info/marketplace/authorization/generate-liveness"

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/vnd.c6bank_authorization_generate_liveness_v1+json"
    }

    payload = {
        "nome": nome,
        "cpf": cpf,
        "data_nascimento": nascimento_final,
        "telefone": {
            "numero": telefone or "",
            "codigo_area": ddd or ""
        }
    }

    r = requests.post(url, json=payload, headers=headers, timeout=20)
    resp = r.json()

    print("📌 RETORNO C6 LINK:", resp)

    link = resp.get("link")

    html = f"""
        <b>Link Gerado:</b><br>

        <div style="display:flex; flex-direction:column; align-items:center; width:100%;">

            <input id="linkC6" value="{link or ''}"
                style='width:90%; padding:10px 12px; border-radius:10px;
                border:1px solid #ccc; margin-bottom:12px;'>

            <button class="copy-btn" onclick="copiarLinkC6()">
                <i class="fa fa-copy"></i> Copiar
            </button>

        </div>
    """

    return jsonify({"sucesso": True, "html": html})

@app.route("/api/c6bank/consultar", methods=["POST"])
def api_c6_consultar():
    data = request.get_json()
    cpf = data.get("cpf")

    token = c6_gerar_token()
    if not token:
        return jsonify({"html": "<div class='resultado-erro'>Erro ao gerar token C6.</div>"}), 500

    url = "https://marketplace-proposal-service-api-p.c6bank.info/marketplace/authorization/status"

    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "Accept": "application/vnd.c6bank_authorization_status_v1+json"
    }

    payload = {"cpf": cpf}

    r = requests.post(url, json=payload, headers=headers, timeout=20)
    resp = r.json()

    print("📌 STATUS C6:", resp)

    status_raw = (
        resp.get("status")
        or resp.get("type")
        or resp.get("observacao")
        or resp.get("message")
        or ""
    ).upper()

    STATUS_MAP = {
        "AUTHORIZED": "Autorizado",
        "WAITING_FOR_AUTHORIZATION": "Aguardando Autorização",
        "NOT_AUTHORIZED": "Não Autorizado",
        "PENDING_OF_LIVENESS": "Aguardando Liveness",

        "CPF_NOT_FOUND_AT_AUTHORIZER": "Nenhuma autorização encontrada",
        "ENTITY_NOT_FOUND": "Nenhuma autorização encontrada",
        "[DEFAULT_RESPONSE_HANDLER] NOT FOUND": "Nenhuma autorização encontrada",
        "NOT FOUND": "Nenhuma autorização encontrada",

        "EXPIRED": "Expirado",
        "CANCELED": "Cancelado",
        "UNAUTHORIZED": "Token expirado ou inválido",
    }

    status_formatado = None
    for chave, valor in STATUS_MAP.items():
        if status_raw.startswith(chave):
            status_formatado = valor
            break

    if not status_formatado:
        status_formatado = "Desconhecido"

    html = f"""
        <div class='resultado-wrapper'>
            <div class='resultado-cpf-topo'>
                CPF: {cpf}
            </div>

            <div class='resultado-grid'>
                <div class='resultado-item'><b>Status:</b> {status_formatado}</div>
            </div>
        </div>
    """

    return jsonify({"html": html})

#*************************************************************************************************************
#HUB

HUB_TOKEN = None
HUB_EXPIRA = 0

def safe_json(response):
    try:
        return response.json()
    except Exception:
        print("\n❌ JSON inválido recebido da API HUB:")
        print(response.text)
        return None

@app.route("/hub")
def hub_page():
    return render_template("hub.html")

def hub_gerar_token():
    import time
    global HUB_TOKEN, HUB_EXPIRA

    if HUB_TOKEN and time.time() < HUB_EXPIRA:
        return HUB_TOKEN

    url = "https://api.hubcredito.com.br/api/Login"

    payload = {
        "userName": "thaina.admin458",
        "password": "123456",
        "grantTypes": "password"
    }

    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, json=payload, headers=headers, timeout=20)
        print("RAW TOKEN HUB:", r.text)
        resp = r.json()
    except Exception:
        print("❌ ERRO AO PARSEAR TOKEN HUB:", r.text)
        return None

    HUB_TOKEN = resp["value"]["token"]["accessToken"]
    HUB_EXPIRA = time.time() + 480

    print("🔑 Novo token HUB gerado!")
    return HUB_TOKEN

def normalizar_cpf_hub(cpf):
    return re.sub(r"\D", "", cpf)

def normalizar_telefone_hub(tel):
    tel = re.sub(r"\D", "", tel)
    if len(tel) < 10:
        return None
    return tel

def normalizar_data_hub(data):
    data = data.replace("/", "-")
    formatos = ["%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d/%m/%Y", "%d/%m/%y"]
    for fmt in formatos:
        try:
            return datetime.strptime(data, fmt).strftime("%Y-%m-%d")
        except:
            pass
    return None

@app.route("/api/hub/gerar-termo", methods=["POST"])
def hub_gerar_termo():
    data = request.get_json()

    nome = data.get("nome")
    cpf = normalizar_cpf_hub(data.get("cpf"))
    email = data.get("email")
    telefone = normalizar_telefone_hub(data.get("telefone"))
    nascimento = normalizar_data_hub(data.get("nascimento"))
    sexo = data.get("sexo")
    loja_id = 13546

    if not telefone:
        return jsonify({"html": "<b class='status-erro'>Telefone inválido.</b>"}), 400

    token = hub_gerar_token()
    if not token:
        return jsonify({"html": "<b class='status-erro'>Erro ao gerar token HUB.</b>"}), 500

    url = "https://api.hubcredito.com.br/api/Clt/gerar-termo-aceite"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "tipoTermo": "AutorizacaoDataprev",
        "lojaId": loja_id,
        "nome": nome,
        "cpf": cpf,
        "email": email,
        "telefone": telefone,
        "dataNascimento": nascimento,
        "sexo": sexo
    }

    r = requests.post(url, json=payload, headers=headers, timeout=20)

    print("\n--- RESPOSTA GERAR TERMO (RAW) ---")
    print("STATUS:", r.status_code)
    print("BODY:", r.text)
    print("----------------------------------\n")

    try:
        resp = r.json()
    except:
        return jsonify({
            "sucesso": False,
            "html": """
                <div style='padding:20px; background:#ffd6d6; color:#b30000;
                            border-radius:10px; text-align:center;'>
                    ❌ A API do HUB retornou uma resposta inválida.<br>
                    Tente novamente em alguns instantes.
                </div>
            """
        })

    termo_id = resp.get("value", {}).get("id")
    link_assinatura = "https://termo.hubcredito.com.br/"

    html = f"""
        <b style='font-size:18px;'>Termo criado com sucesso!</b><br><br>

        <div style="display:flex; flex-direction:column; align-items:center; width:100%;">

            <input value="{link_assinatura}" readonly
                style="
                    width:90%;
                    padding:12px 14px;
                    border-radius:12px;
                    border:1px solid #bfbfbf;
                    background: var(--bg-input);
                    color: var(--cor-texto);
                    font-size:15px;
                    margin-bottom:15px;
                ">

            <button class='copy-btn' onclick="navigator.clipboard.writeText('{link_assinatura}')"
                style="
                    background:#0aff73;
                    color:#000;
                    padding:12px 20px;
                    border-radius:12px;
                    border:none;
                    cursor:pointer;
                    font-weight:bold;
                    font-size:15px;
                    transition:0.25s;
                ">
                <i class="fa fa-copy"></i> Copiar
            </button>

        </div>

        <br><br>
        <b>ID Gerado:</b> {termo_id}
    """

    return jsonify({"html": html, "sucesso": True})

def hub_request_get(url, headers):
    try:
        r = requests.get(url, headers=headers, timeout=20)
        return r
    except requests.exceptions.ReadTimeout:
        print("⏳ Timeout (1ª tentativa). Retentando...")
        try:
            r = requests.get(url, headers=headers, timeout=20)
            return r
        except requests.exceptions.ReadTimeout:
            print("⛔ Timeout novamente. API HUB muito lenta.")
            return None
    except Exception as e:
        print("❌ ERRO DE CONEXÃO HUB:", e)
        return None

@app.route("/api/hub/vinculo", methods=["POST"])
def hub_vinculo():
    data = request.get_json()
    cpf = normalizar_cpf_hub(data.get("cpf"))

    token = hub_gerar_token()
    if not token:
        return jsonify({
            "html": """
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;text-align:center;'>
                    ❌ Erro ao gerar token Hub.
                </div>
            """,
            "elegivel": False
        }), 500

    url = f"https://api.hubcredito.com.br/api/Clt/wincred/listar-vinculos?cpfTrabalhador={cpf}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    r = hub_request_get(url, headers)

    if r is None:
        return jsonify({
            "html": """
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;
                text-align:center;font-size:18px;font-weight:bold;'>
                    ❌ A API Hub demorou demais para responder.<br>Tente novamente.
                </div>
            """,
            "elegivel": False
        })

    try:
        resp = r.json()
    except:
        print("❌ JSON INVÁLIDO HUB:", r.text)
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;text-align:center;'>
                    ❌ A API Hub retornou resposta inválida.<br>
                    <small>{r.text}</small>
                </div>
            """,
            "elegivel": False
        })

    print("📌 VÍNCULO HUB:", resp)

    if not isinstance(resp, dict):
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;
                text-align:center;font-size:18px;font-weight:bold;'>
                    ❌ A API HUB retornou erro inesperado.<br>
                    <small>{resp}</small>
                </div>
            """,
            "elegivel": False
        })

    if resp.get("hasError"):
        erro_msg = resp.get("errors", ["Erro desconhecido"])[0]
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;
                text-align:center;font-weight:bold;font-size:18px;'>
                    ❌ {erro_msg}
                </div>
            """,
            "elegivel": False
        })

    try:
        vinc = resp["value"]["vinculos"][0]
        idCotacao = resp["value"]["idCotacao"]
        matricula = vinc["matricula"]
        inscricao = vinc["inscricaoEmpregador"]["numeroInscricao"]
        tipo_inscricao = vinc["inscricaoEmpregador"].get("tipoInscricao", 1)
        elegivel = vinc["elegivel"]
    except Exception as e:
        print("❌ ERRO AO LER VÍNCULO:", e)
        return jsonify({
            "html": """
                <div style='padding:20px;background:#ffd6d6;color:#b30000;border-radius:10px;
                text-align:center;font-size:20px;font-weight:bold;'>
                    ❌ Cliente não elegível
                </div>
            """,
            "elegivel": False
        })

    session["hub_idCotacao"] = idCotacao
    session["hub_matricula"] = matricula
    session["hub_inscricao"] = inscricao
    session["hub_tipo_inscricao"] = tipo_inscricao
    session["hub_cpf"] = cpf

    html_vinculo = """
        <div style='padding:20px;font-size:20px;font-weight:bold;
        color:{cor};background:{bg};border-radius:10px;text-align:center;'>
            {msg}
        </div>
    """

    if elegivel:
        html_vinculo = html_vinculo.format(cor="#0a7a00", bg="#c8ffcc", msg="✔️ Cliente Elegível")
    else:
        html_vinculo = html_vinculo.format(cor="#b30000", bg="#ffd6d6", msg="❌ Cliente Não Elegível")

    return jsonify({
        "html": html_vinculo,
        "elegivel": elegivel,
        "simular": elegivel
    })

@app.route("/api/hub/simulacao", methods=["POST"])
def hub_simulacao():
    cpf = session.get("hub_cpf")
    idCotacao = session.get("hub_idCotacao")
    matricula = session.get("hub_matricula")
    inscricao = session.get("hub_inscricao")
    tipo_inscricao = session.get("hub_tipo_inscricao", 1)

    if not all([cpf, idCotacao, matricula, inscricao]):
        return jsonify({
            "html": """
                <div style='padding:18px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;'>
                    ❌ Consulte o vínculo antes de simular.
                </div>
            """
        })
    
    dados = request.get_json() or {}
    parcelas_str = str(dados.get("parcelas", "")).strip()
    valor_str = str(dados.get("valor", "")).replace(",", ".").strip()

    parcelas = None
    valor = None

    if not parcelas_str and not valor_str:
        parcelas = 12 
    else:
        if parcelas_str:
            try:
                parcelas = int(parcelas_str)
            except:
                return jsonify({"html": "<div class='resultado-erro'>❌ Número de parcelas inválido.</div>"})

        if valor_str:
            try:
                valor = float(valor_str)
            except:
                return jsonify({"html": "<div class='resultado-erro'>❌ Valor inválido.</div>"})

    token = hub_gerar_token()
    if not token:
        return jsonify({
            "html": """
                <div style='padding:18px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;'>
                    ❌ Erro ao gerar token para simulação.
                </div>
            """
        }), 500

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
        "cpf": cpf,
        "lojaId": 13546,
        "idCotacao": idCotacao,
        "matricula": matricula,
        "codigoInscricaoEmpregador": tipo_inscricao,
        "numeroInscricaoEmpregador": inscricao
    }

    if parcelas is not None:
        payload["numeroParcelas"] = parcelas
    if valor is not None:
        payload["valor"] = valor

    print("\n📤 ENVIANDO PAYLOAD PARA SIMULAÇÃO:")
    print(payload)

    try:
        r = requests.post(
            "https://api.hubcredito.com.br/api/Clt/wincred/simular",
            json=payload,
            headers=headers,
            timeout=25
        )
    except requests.exceptions.ReadTimeout:
        return jsonify({
            "html": """
                <div style='padding:20px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;'>
                    ⛔ A API Hub demorou para responder. Tente novamente.
                </div>
            """
        })

    try:
        resp = r.json()
    except:
        print("❌ JSON inválido na simulação:", r.text)
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;'>
                    ❌ A API Hub retornou resposta inválida.<br>
                    <small>{r.text}</small>
                </div>
            """
        })

    print("📌 SIMULAÇÃO HUB:", resp)

    if not isinstance(resp, dict):
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;font-size:18px;'>
                    ❌ Erro inesperado da Wincred.<br>
                    <small>{resp}</small>
                </div>
            """
        })

    if resp.get("hasError"):
        erro = resp.get("errors", ["Erro inesperado"])[0]
        return jsonify({
            "html": f"""
                <div style='padding:20px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;font-size:18px;'>
                    ❌ {erro}
                </div>
            """
        })

    if not resp.get("value"):
        return jsonify({
            "html": """
                <div style='padding:20px;background:#ffd6d6;color:#b30000;
                border-radius:10px;text-align:center;font-weight:bold;font-size:18px;'>
                    ❌ Nenhuma simulação foi encontrada para este CPF.
                </div>
            """
        })

    return jsonify({
        "html": """
            <div style='padding:20px;background:#ffffb8;color:#575700;
            border-radius:10px;text-align:center;font-size:20px;font-weight:bold;'>
                ✔️ Simulação disponível!
            </div>
        """,
        "simulacao": resp["value"]
    })

#*************************************************************************************************************
#V8

V8_TOKEN = None
V8_TOKEN_EXPIRA = 0

def gerar_token_v8():
    global V8_TOKEN, V8_TOKEN_EXPIRA

    if V8_TOKEN and time.time() < V8_TOKEN_EXPIRA:
        return V8_TOKEN

    url = "https://auth.v8sistema.com/oauth/token"

    payload = {
        "grant_type": "password",
        "username": "thaina737373@gmail.com",
        "password": "Tech@2028",
        "audience": "https://bff.v8sistema.com",
        "scope": "offline_access",
        "client_id": "DHWogdaYmEI8n5bwwxPDzulMlSK7dwIn"
    }

    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    r = requests.post(url, data=payload, headers=headers, timeout=20)
    data = r.json()
    print("🔑 TOKEN V8:", data)

    token = data.get("access_token")
    if not token:
        raise Exception("Erro ao gerar token V8.")

    V8_TOKEN = token
    V8_TOKEN_EXPIRA = time.time() + 3500

    return token

def esperar_termo_finalizar(termo_id, headers):
    url_consult = f"https://bff.v8sistema.com/private-consignment/consult/{termo_id}"

    for tent in range(40):
        try:
            r = requests.get(url_consult, headers=headers, timeout=20)
            data = r.json()

            status = str(data.get("status", "")).upper()
            print("🔄 STATUS TERMO:", status)

            if status in ["SUCCESS", "CONSENT_APPROVED", "APPROVED", "FINISHED"]:
                print("✅ TERMO FINALIZOU!")
                return True

            if status in ["CONSENT_PENDING", "WAITING", "PENDING"]:
                time.sleep(0.5)
                continue

            if status == "" or status == "404":
                print("⏳ TERMO AINDA NÃO DISPONÍVEL...")
                time.sleep(0.5)
                continue

        except:
            time.sleep(0.5)
            continue

    print("❌ TERMO NÃO FINALIZOU A TEMPO")
    return False

@app.route("/v8")
def pagina_v8():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("v8.html")


@app.route("/api/v8/termo", methods=["POST"])
def api_v8_termo():
    try:
        data = request.get_json()

        token = gerar_token_v8()

        headers_create = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        r = requests.post(
            "https://bff.v8sistema.com/private-consignment/consult",
            data=json.dumps(data),   
            headers=headers_create,
            timeout=20
        )

        resp = r.json()
        print("📌 TERMO V8:", resp)

        termo_id = resp.get("id")
        if not termo_id:
            return jsonify({"erro": resp}), 400

        url_aut = f"https://bff.v8sistema.com/private-consignment/consult/{termo_id}/authorize"

        headers_aut = {
            "Authorization": f"Bearer {token}",
            "Accept": "*/*"
        }

        r2 = requests.post(
            url_aut,
            headers=headers_aut,
            data=None
        )

        print("📌 AUTORIZAÇÃO RETORNO:", r2.status_code, r2.text)

        finalizado = esperar_termo_finalizar(termo_id, headers_create)

        if not finalizado:
            return jsonify({
                "erro": "Termo não finalizado — não é possível seguir.",
                "id": termo_id
            }), 400

        return jsonify({
            "id": termo_id,
            "finalizado": True
        })

    except Exception as e:
        return jsonify({"erro": str(e)})

@app.route("/api/v8/configs", methods=["POST"])
def api_v8_configs():
    try:
        data = request.get_json()
        consult_id = data.get("consult_id")

        if not consult_id:
            return jsonify({"erro": "consult_id obrigatório"}), 400

        token = gerar_token_v8()

        headers = {
            "Authorization": f"Bearer {token}",
        }

        r = requests.get(
            "https://bff.v8sistema.com/private-consignment/simulation/configs",
            headers=headers,
            timeout=20
        )

        resp = r.json()
        print("📌 CONFIGS V8:", resp)

        return jsonify({
            "consult_id": consult_id,
            "configs": resp.get("configs", [])
        })

    except Exception as e:
        return jsonify({"erro": str(e)})

@app.route("/api/v8/simular", methods=["POST"])
def api_v8_simular():
    try:
        data = request.get_json()

        token = gerar_token_v8()

        headers = {
            "Authorization": f"Bearer {token}",
        }

        r = requests.post(
            "https://bff.v8sistema.com/private-consignment/simulation",
            json=data,
            headers=headers,
            timeout=20
        )

        resp = r.json()
        print("📌 SIMULAÇÃO V8:", resp)

        return jsonify(resp)

    except Exception as e:
        return jsonify({"erro": str(e)})

@app.route("/api/v8/proposta", methods=["POST"])
def api_v8_proposta():
    try:
        data = request.get_json()
        token = gerar_token_v8()

        headers = {
            "Authorization": f"Bearer {token}",
        }

        r = requests.post(
            "https://bff.v8sistema.com/private-consignment/operation",
            json=data,
            headers=headers,
            timeout=20
        )

        resp = r.json()
        print("📌 PROPOSTA V8:", resp)

        return jsonify(resp)

    except Exception as e:
        return jsonify({"erro": str(e)})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8600)
