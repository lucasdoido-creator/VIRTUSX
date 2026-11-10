"""Operação real (ambiente de produção limpo): primeiro acesso, cadastros, freelancers, checklists de TI,
fechamento mensal de serviços, CEO de serviços e isolamento."""
import base64
import uuid

import pytest
from starlette.testclient import TestClient

from app import agents, brain, db as dbmod, research
from app.core import R
from app.main import create_app

PW = "senha-forte-123"


@pytest.fixture()
def prod(tmp_path):
    dbmod.reset_clock()
    app = create_app(env="production", data_dir=tmp_path, scheduler_enabled=False, research_enabled=False,
                     ollama_url="http://127.0.0.1:9", extra={"sync_agents": True})
    brain._ollama_state.update(checked_at=1e18, ok=False, models=[], error="teste")
    yield app
    brain._ollama_state.update(checked_at=0.0)


def client(app, email=None, pw=PW):
    c = TestClient(app)
    if email:
        r = c.post("/api/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 200, r.text
    c.headers.update({"X-Requested-With": "plataforma"})
    return c


def setup_admin(app):
    pub = TestClient(app)
    assert pub.get("/api/setup").json()["data"]["needed"] is True
    assert pub.post("/api/setup", json={"name": "Lucas", "email": "lucas@empresa.com", "password": "curta"}).status_code == 400
    assert pub.post("/api/setup", json={"name": "Lucas", "email": "lucas@empresa.com", "password": PW}).status_code == 200
    assert pub.post("/api/setup", json={"name": "Outro", "email": "x@x.com", "password": PW}).status_code == 409  # só uma vez
    return client(app, "lucas@empresa.com")


def cadastrar(adm):
    b1 = adm.post("/api/cadastros/bases", json={"name": "Base Centro", "city": "São Paulo", "state": "SP"}).json()["data"]["id"]
    b2 = adm.post("/api/cadastros/bases", json={"name": "Base Campinas", "city": "Campinas", "state": "SP"}).json()["data"]["id"]
    c1 = adm.post("/api/cadastros/clientes", json={"name": "Clínica Vida", "segment": "saúde", "contract": "1.500,00"}).json()["data"]["id"]
    u1 = adm.post("/api/cadastros/unidades", json={"client_id": c1, "name": "Matriz", "city": "São Paulo", "state": "SP",
                                                   "address": "Av. X, 10", "location_confirmed": True, "base_id": b1}).json()["data"]["id"]
    u2 = adm.post("/api/cadastros/unidades", json={"client_id": c1, "name": "Filial Campinas", "city": "Campinas", "state": "SP",
                                                   "base_id": b2}).json()["data"]["id"]
    return b1, b2, c1, u1, u2


def test_producao_limpa_sem_dados_ficticios(prod):
    assert R.db.val("SELECT COUNT(*) FROM partners") == 0 and R.db.val("SELECT COUNT(*) FROM products") == 0
    assert R.db.val("SELECT COUNT(*) FROM regions") == 3 and R.db.val("SELECT COUNT(*) FROM regions WHERE sales_enabled=1") == 0
    assert R.db.val("SELECT COUNT(*) FROM events WHERE is_demo=1") == 0
    adm = setup_admin(prod)
    assert adm.get("/api/me").json()["data"]["environment"] == "production"
    assert client(prod).post("/api/auth/login", json={"email": "admin@central.demo", "password": "demo1234"}).status_code == 401


def test_cadastros_completos_e_auditados(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    d = adm.get("/api/cadastros").json()["data"]
    assert len(d["bases"]) == 2 and len(d["clients"]) == 1 and len(d["units"]) == 2
    cli = d["clients"][0]
    assert cli["commercial_model"] == "cliente_servico" and cli["contract_minor"] == 150000 and cli["is_demo"] == 0
    units = {u["id"]: u for u in d["units"]}
    assert units[u1]["location_status"] == "confirmada" and units[u2]["location_status"] == "nao_confirmada"
    assert units[u1]["base_name"] == "Base Centro"
    assert adm.post("/api/cadastros/clientes", json={"name": "clínica vida"}).status_code == 409
    assert adm.post("/api/cadastros/unidades", json={"client_id": c1, "name": "X", "city": "Y", "state": "ZZ"}).status_code == 400
    assert R.db.val("SELECT COUNT(*) FROM agents WHERE code=?", f"ceo_{c1}") == 1  # CEO do cliente criado
    assert {a["action"] for a in adm.get("/api/audit").json()["data"]} >= {"base.criada", "cliente.criado", "unidade.criada"}
    w = adm.get("/api/world").json()["data"]
    assert len(w["service_bases"]) == 2 and w["partners"][0]["id"] == c1


def test_freelancer_autorizado_ve_so_as_proprias_tarefas(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    assert adm.post("/api/cadastros/usuarios", json={"name": "Téc", "email": "tec@x.com", "role": "tecnico", "password": PW}).status_code == 400  # sem base
    tid = adm.post("/api/cadastros/usuarios", json={"name": "Ana Técnica", "email": "ana@freela.com", "role": "tecnico",
                                                    "password": PW, "base_id": b1}).json()["data"]["id"]
    # pendente de autorização: não entra e não recebe tarefa
    assert client(prod).post("/api/auth/login", json={"email": "ana@freela.com", "password": PW}).status_code == 401
    r = adm.post("/api/tasks", json={"template": "preventiva_ti", "partner_id": c1, "unit_id": u1, "assignee_type": "humano", "assignee_id": tid})
    assert r.status_code == 400 and r.json()["error"] == "responsavel_inativo"
    adm.put(f"/api/cadastros/usuarios/{tid}", json={"name": "Ana Técnica", "email": "ana@freela.com", "role": "tecnico", "base_id": b1, "active": True})
    mine = adm.post("/api/tasks", json={"template": "preventiva_ti", "partner_id": c1, "unit_id": u1, "assignee_type": "humano",
                                        "assignee_id": tid}).json()["data"]
    other = adm.post("/api/tasks", json={"template": "backup", "partner_id": c1, "unit_id": u2}).json()["data"]
    ana = client(prod, "ana@freela.com")
    assert [t["id"] for t in ana.get("/api/tasks").json()["data"]] == [mine["id"]]
    assert ana.get(f"/api/tasks/{other['id']}").status_code == 404
    assert ana.get("/api/world").status_code == 403 and ana.get("/api/orders").status_code == 403
    assert ana.get(f"/api/partners/{c1}/building").status_code == 403
    # técnico não reatribui nem muda prazo
    ana.patch(f"/api/tasks/{mine['id']}", json={"assignee_id": None, "due_at": "2030-01-01T00:00:00Z"})
    t = adm.get(f"/api/tasks/{mine['id']}").json()["data"]
    assert t["assignee_id"] == tid and t["due_at"] is None


def test_checklist_ti_obrigatorio_e_evidencia(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    tid = adm.post("/api/cadastros/usuarios", json={"name": "Bruno", "email": "bruno@freela.com", "role": "tecnico", "password": PW,
                                                    "base_id": b1, "authorize_now": True}).json()["data"]["id"]
    t = adm.post("/api/tasks", json={"title": None, "template": "chamado_ti", "partner_id": c1, "unit_id": u1, "assignee_type": "humano",
                                     "assignee_id": tid}).json()["data"]
    assert t["title"] == "Atendimento de chamado" and len(t["checklist"]) == 5
    bru = client(prod, "bruno@freela.com")
    r = bru.patch(f"/api/tasks/{t['id']}", json={"status": "concluida"})
    assert r.status_code == 409 and r.json()["error"] == "checklist_incompleto"
    for i in range(5):
        cl = bru.post(f"/api/tasks/{t['id']}/checklist", json={"index": i, "done": True}).json()["data"]["checklist"]
    assert all(x["done"] and x["by"] == f"user:{tid}" for x in cl)
    pdf = base64.b64encode(b"%PDF-1.4 relatorio").decode()
    assert bru.post(f"/api/tasks/{t['id']}/attachments", json={"filename": "relatorio.pdf", "mime": "application/pdf",
                                                               "content_base64": pdf, "is_evidence": True}).status_code == 200
    done = bru.patch(f"/api/tasks/{t['id']}", json={"status": "concluida", "note": "Resolvido"}).json()["data"]
    assert done["completed_by"] == f"user:{tid}" and done["status"] == "concluida"


def test_recorrencia_preventiva_mensal_por_unidade(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    adm.post("/api/tasks", json={"template": "preventiva_ti", "partner_id": c1, "unit_id": u1, "recurrence_rule": "mensal"})
    prod.state.tick()
    inst = R.db.all("SELECT * FROM tasks WHERE recurrence_parent_id IS NOT NULL")
    assert len(inst) == 1 and inst[0]["template"] == "preventiva_ti" and inst[0]["checklist_json"]


def test_fechamento_mensal_de_servicos(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    t1 = adm.post("/api/tasks", json={"template": "backup", "partner_id": c1, "unit_id": u1}).json()["data"]
    for i in range(4):
        adm.post(f"/api/tasks/{t1['id']}/checklist", json={"index": i, "done": True})
    adm.patch(f"/api/tasks/{t1['id']}", json={"status": "concluida"})
    adm.post("/api/tasks", json={"title": "Trocar switch", "partner_id": c1, "unit_id": u2, "due_at": dbmod.iso(dbmod.now() - __import__("datetime").timedelta(days=1))})
    from app.tz import get_tz
    today = dbmod.now().astimezone(get_tz("America/Sao_Paulo")).date().isoformat()
    cid = adm.post("/api/closings", json={"kind": "servicos", "partner_id": c1, "period_type": "mensal", "local_day": today}).json()["data"]["id"]
    cl = next(x for x in adm.get("/api/closings").json()["data"] if x["id"] == cid)
    assert cl["totals"]["servicos_concluidos"] == 1 and cl["totals"]["valor_contrato_minor"] == 150000
    assert cl["totals"]["atrasados_em_aberto"] == 1 and cl["settlement_status"] == "pendente"
    tipos = {d["tipo"] for d in cl["divergences"]}
    assert {"sem_evidencia", "atrasado"} <= tipos
    # produção: liquidação exige referência real (nota/boleto)
    fin = adm.post("/api/cadastros/usuarios", json={"name": "Fin", "email": "fin@x.com", "role": "financeiro_central", "password": PW}).json()["data"]["id"]
    f = client(prod, "fin@x.com")
    rev = adm.post("/api/cadastros/usuarios", json={"name": "Rev", "email": "rev@x.com", "role": "operacoes_central", "password": PW})
    assert f.post(f"/api/closings/{cid}/advance").status_code == 200  # conferido (preparado pelo admin)
    assert adm.post(f"/api/closings/{cid}/advance", json={"note": "ciente"}).status_code == 403  # quem preparou não aprova
    assert f.post(f"/api/closings/{cid}/advance", json={"note": "ciente"}).status_code == 403  # quem conferiu não aprova
    r2 = client(prod)
    r2.post("/api/setup")  # sem efeito
    admin2 = adm.post("/api/cadastros/usuarios", json={"name": "Sócio", "email": "socio@x.com", "role": "admin_central", "password": PW})
    s = client(prod, "socio@x.com")
    assert s.post(f"/api/closings/{cid}/advance", json={"note": "ciente das divergências"}).json()["data"]["status"] == "aprovado"
    assert s.post(f"/api/closings/{cid}/settle", json={"reference": ""}).status_code == 400
    assert s.post(f"/api/closings/{cid}/settle", json={"reference": "NF 123 / boleto pago 05/12"}).status_code == 200
    assert R.db.val("SELECT is_simulated FROM payouts") == 0


def test_ceo_de_servicos_usa_so_funcionarios_de_servico(prod):
    adm = setup_admin(prod)
    b1, b2, c1, u1, u2 = cadastrar(adm)
    adm.post(f"/api/partners/{c1}/ideas", json={"idea": "Oferecer backup em nuvem gerenciado para todas as unidades"})
    r = agents.run_agent(f"ceo_{c1}", "teste")
    assert r["status"] == "concluido"
    staff = {s["agent_id"] for s in R.db.all("SELECT * FROM venture_staff")}
    assert staff == {"agt_pesquisa", "agt_analise", "agt_conteudo", "agt_financeiro"}
    agents.run_agent("analise", "teste")
    note = R.db.val("SELECT c.body FROM task_comments c JOIN tasks t ON t.id=c.task_id WHERE t.assignee_id='agt_analise' ORDER BY c.created_at DESC")
    assert "Unidades ativas: 2" in note


def test_banco_de_demo_antigo_e_migrado_sem_perder_dados(tmp_path):
    import sqlite3
    from app.db import SCHEMA, migrate
    old = SCHEMA.replace("'central_commission','b2b_resale','cliente_servico'", "'central_commission','b2b_resale'") \
                .replace(", segment TEXT, contract_minor INTEGER, notes TEXT", "")
    c = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    c.executescript(old)
    c.execute("INSERT INTO regions(code,name,currency,locale,timezone,tax_mode,seller_entity) VALUES ('BR','Brasil','BRL','pt-BR','America/Sao_Paulo','x','y')")
    c.execute("INSERT INTO partners(id,name,region_code,commercial_model,created_at) VALUES ('p1','Antigo','BR','central_commission','t')")
    migrate(c)
    migrate(c)  # idempotente
    assert c.execute("SELECT name FROM partners").fetchone()[0] == "Antigo"
    c.execute("INSERT INTO partners(id,name,region_code,commercial_model,created_at) VALUES ('p2','Novo','BR','cliente_servico','t')")
