from flask import Flask, render_template, request, jsonify, redirect, url_for, session
from datetime import datetime, timedelta
import requests
import time
import json
import sqlite3
import threading
import os
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

        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
        reprocessar_consultas_travadas()
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

@app.route("/home")
def home():
    return redirect(url_for("index"))

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
        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
    
def reprocessar_consultas_travadas():
    try:
        conn = get_conn()
        c = conn.cursor()
        ph = get_placeholder(conn)

        limite = (datetime.now() - timedelta(minutes=20)).strftime("%Y-%m-%d %H:%M:%S")
        query = f"""
            SELECT transaction_id, cpf, usuario, status
            FROM fila_async
            WHERE ultima_atualizacao < {ph}
            AND (status LIKE 'EM CONSULTA%%' OR status LIKE 'Reprocessando%%')
        """
        query = adapt_queries_for_db(conn, query)
        c.execute(query, (limite,))
        travados = c.fetchall()

        if not travados:
            conn.close()
            return

        token = obter_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        for t_id, cpf, usuario, status in travados:
            if not status.startswith("EM CONSULTA"):
                continue

            payload = {
                "cpf": cpf,
                "callBackBalance": {
                    "url": WEBHOOK_URL,
                    "method": "POST"
                }
            }
            print(f"♻️ Reprocessando CPF {cpf} (Transaction antigo={t_id})...")

            try:
                resp = requests.post(API_BALANCE, json=payload, headers=headers, timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    novo_transaction = data.get("objectReturn", {}).get("transactionId")
                    if novo_transaction:
                        agora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        update_q = f"""
                            UPDATE fila_async
                            SET transaction_id={ph}, status={ph}, data_inclusao={ph}, ultima_atualizacao={ph}
                            WHERE cpf={ph}
                        """
                        update_q = adapt_queries_for_db(conn, update_q)
                        c.execute(update_q, (novo_transaction, "Reprocessando...", agora, agora, cpf))
                        print(f"✅ CPF {cpf} reprocessado (novo TransactionID={novo_transaction})")
                else:
                    print(f"⚠️ Falha ao reprocessar CPF {cpf}: {resp.text}")
            except Exception as e:
                print(f"❌ Erro ao reprocessar {cpf}: {e}")

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"⚠️ Erro geral no reprocessamento automático: {e}")    

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8600)
