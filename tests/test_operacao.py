"""Permissões, isolamento, tarefas, fechamentos, agentes, sinalização dos prédios e auditoria."""
import base64
import sqlite3

import pytest

from conftest import make_store, order
from app.core import R
from app import db as dbmod


def _paid(public):
    d = order(public, [{"product_id": "prd_cafe", "qty": 1}]).json()["data"]
    public.post(f"/api/public/orders/{d['order_code']}/pay", json={"token": d["access_token"], "test_card": "4242424242424242"})
    return R.db.val("SELECT id FROM orders WHERE code=?", d["order_code"])


# ---------------------------------------------------------------- isolamento e permissões
def test_isolamento_entre_parceiros(marina, joao, public):
    make_store(marina)
    _paid(public)
    assert joao.get("/api/partners/prt_aurora/building").status_code == 403
    assert joao.get("/api/orders?partner_id=prt_aurora").status_code == 403
    assert joao.get("/api/orders").json()["data"] == []
    oid = R.db.val("SELECT id FROM orders")
    assert joao.get(f"/api/orders/{oid}").status_code == 403
    t = R.db.val("SELECT id FROM tasks WHERE partner_id='prt_aurora' LIMIT 1")
    assert joao.get(f"/api/tasks/{t}").status_code == 403
    assert joao.patch(f"/api/tasks/{t}", json={"status": "concluida"}).status_code == 403
    evs = joao.get("/api/events?after=0&limit=1000").json()["data"]["events"]
    assert evs and all(e["partner_id"] == "prt_lisboa" for e in evs)
    world = joao.get("/api/world").json()["data"]
    assert [p["id"] for p in world["partners"]] == ["prt_lisboa"] and world["view"] == "parceiro"
    assert all(w["region_code"] == "PT" for w in world["warehouses"])
    # parceiro não registra envio, não aprova, não pausa agentes
    assert marina.post(f"/api/orders/{oid}/ship").status_code == 403
    assert marina.post("/api/agents/pause", json={"paused": True}).status_code == 403


def test_csrf_e_autenticacao(app, marina):
    from starlette.testclient import TestClient
    assert TestClient(app).get("/api/world").status_code == 401
    c = TestClient(app)
    c.post("/api/auth/login", json={"email": "marina@aurora.demo", "password": "demo1234"})
    r = c.post("/api/tasks", json={"title": "x"})  # sem cabeçalho anti-CSRF
    assert r.status_code == 403 and r.json()["error"] == "csrf"
    assert TestClient(app).post("/api/auth/login", json={"email": "marina@aurora.demo", "password": "errada"}).status_code == 401


def test_auditoria_somente_inclusao(admin):
    admin.post("/api/agents/pause", json={"paused": True})
    c = R.db.connect()
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE audit_log SET action='x'")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("DELETE FROM audit_log")
    assert any(a["action"] == "flag.set" for a in admin.get("/api/audit").json()["data"])


# ---------------------------------------------------------------- tarefas
def test_tarefas_dependencias_conclusao_e_evidencias(marina):
    tasks = marina.get("/api/tasks?partner_id=prt_aurora").json()["data"]
    fotos = next(t for t in tasks if t["title"] == "Atualizar fotos da vitrine")
    assert fotos["overdue"] is True
    r = marina.patch(f"/api/tasks/{fotos['id']}", json={"status": "concluida"})
    assert r.status_code == 409 and r.json()["error"] == "dependencia_pendente"
    new = marina.post("/api/tasks", json={"title": "Enviar contrato assinado", "assignee_type": "humano", "assignee_id": "usr_aurora_op",
                                          "priority": "alta", "unit_id": "uni_pinheiros"}).json()["data"]
    assert new["partner_id"] == "prt_aurora"  # forçado ao parceiro do usuário
    content = base64.b64encode(b"%PDF-1.4 contrato").decode()
    r = marina.post(f"/api/tasks/{new['id']}/attachments", json={"filename": "contrato.pdf", "mime": "application/pdf",
                                                                 "content_base64": content, "is_evidence": True})
    assert r.status_code == 200
    r = marina.patch(f"/api/tasks/{new['id']}", json={"status": "concluida", "note": "Contrato anexado"})
    t = r.json()["data"]
    assert t["completed_by"] == "user:usr_aurora" and t["completed_at"]
    assert t["attachments"][0]["is_evidence"] == 1 and len(t["attachments"][0]["sha256"]) == 64
    assert {h["field"] for h in t["history"]} >= {"criada", "anexo", "status"}
    assert marina.patch(f"/api/tasks/{new['id']}", json={"status": "a_fazer"}).status_code == 409
    bad = marina.post(f"/api/tasks/{new['id']}/attachments", json={"filename": "x.exe", "mime": "application/x-msdownload",
                                                                   "content_base64": content})
    assert bad.status_code == 400
    assert marina.post("/api/tasks", json={"title": "x", "assignee_type": "humano", "assignee_id": "usr_lisboa"}).status_code == 400


def test_recorrencia_executa_no_servidor(app):
    before = R.db.val("SELECT COUNT(*) FROM tasks WHERE recurrence_parent_id IS NOT NULL")
    app.state.tick()  # nenhum navegador envolvido
    after1 = R.db.val("SELECT COUNT(*) FROM tasks WHERE recurrence_parent_id IS NOT NULL")
    app.state.tick()
    assert after1 == before + 1 and R.db.val("SELECT COUNT(*) FROM tasks WHERE recurrence_parent_id IS NOT NULL") == after1
    dbmod.advance_clock(days=1, minutes=1)
    app.state.tick()
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE recurrence_parent_id IS NOT NULL") == after1 + 1


# ---------------------------------------------------------------- fechamentos
def test_fechamentos_fluxo_segregacao_liquidacao_reabertura(marina, public, ops, fin, admin):
    make_store(marina)
    o = _paid(public)
    ops.post(f"/api/orders/{o}/ship")
    from app.tz import get_tz
    today = dbmod.now().astimezone(get_tz("America/Sao_Paulo")).date().isoformat()
    cid = fin.post("/api/closings", json={"kind": "comissoes", "partner_id": "prt_aurora", "period_type": "mensal", "local_day": today}).json()["data"]["id"]
    cl = next(c for c in fin.get("/api/closings").json()["data"] if c["id"] == cid)
    assert cl["totals"]["apurado_minor"] == 499 and cl["status"] == "preparado" and cl["settlement_status"] == "pendente"
    assert fin.post(f"/api/closings/{cid}/advance").status_code == 403  # quem preparou não confere
    assert marina.post(f"/api/closings/{cid}/advance").json()["data"]["status"] == "conferido"  # parceiro confere
    assert marina.post(f"/api/closings/{cid}/advance").status_code == 403  # parceiro não aprova
    r = admin.post(f"/api/closings/{cid}/advance")
    assert r.status_code == 400 and r.json()["error"] == "justificativa_obrigatoria"  # há comissão em carência
    assert admin.post(f"/api/closings/{cid}/advance", json={"note": "Carência ciente"}).json()["data"]["status"] == "aprovado"
    cl = next(c for c in admin.get("/api/closings").json()["data"] if c["id"] == cid)
    assert cl["status"] == "aprovado" and cl["settlement_status"] == "pendente"  # aprovado ≠ pago
    # reabertura preserva o original
    nid = admin.post(f"/api/closings/{cid}/reopen", json={"reason": "ajuste de devolução"}).json()["data"]["id"]
    rows = {c["id"]: c for c in admin.get("/api/closings").json()["data"]}
    assert rows[cid]["superseded_by_id"] == nid and rows[cid]["totals"]["apurado_minor"] == 499 and rows[cid]["status"] == "aprovado"
    assert rows[nid]["revision"] == 2 and rows[nid]["supersedes_id"] == cid
    assert admin.post(f"/api/closings/{cid}/settle", json={}).status_code == 409  # revisão substituída não liquida
    marina.post(f"/api/closings/{nid}/advance")
    assert admin.post(f"/api/closings/{nid}/advance", json={"note": "ok"}).status_code == 403  # admin preparou a revisão
    assert fin.post(f"/api/closings/{nid}/advance", json={"note": "ok"}).status_code == 200
    r = admin.post(f"/api/closings/{nid}/settle", json={})
    assert r.status_code == 200
    p = R.db.one("SELECT * FROM payouts")
    assert p["is_simulated"] == 1 and p["reference"] == "DEMO-SEM-TRANSFERENCIA"
    assert admin.post(f"/api/closings/{nid}/reopen", json={"reason": "x"}).status_code == 409  # liquidado → ajuste
    assert admin.post("/api/commissions/adjustments", json={"partner_id": "prt_aurora", "order_id": o, "amount_minor": -100,
                                                            "note": "Correção"}).status_code == 200


def test_conciliacao_aponta_divergencia(marina, public, ops, fin):
    make_store(marina)
    d = order(public, [{"product_id": "prd_caneca", "qty": 1}]).json()["data"]
    ops.post(f"/api/orders/{R.db.val('SELECT id FROM orders')}/cancel", json={"reason": "x"})
    p = R.db.one("SELECT * FROM payments")
    from app.providers import DemoPaymentProvider
    from app import commerce
    b, h = DemoPaymentProvider.build_webhook("payment.captured", {"provider_ref": p["provider_ref"], "amount_minor": p["amount_minor"],
                                                                  "currency": "BRL", "fee_minor": 1})
    commerce.receive_webhook("demo", b, h)
    from app.tz import get_tz
    today = dbmod.now().astimezone(get_tz("America/Sao_Paulo")).date().isoformat()
    cid = fin.post("/api/closings", json={"kind": "conciliacao_pagamentos", "partner_id": "prt_aurora", "period_type": "diario",
                                          "local_day": today}).json()["data"]["id"]
    cl = next(c for c in fin.get("/api/closings").json()["data"] if c["id"] == cid)
    assert any(x["tipo"] == "cancelado_com_pagamento" for x in cl["divergences"])


# ---------------------------------------------------------------- agentes e políticas
def test_agente_executa_tarefa_atribuida_e_prepara_compra(admin):
    r = admin.post("/api/agents/estoque/run").json()["data"]
    assert r["status"] == "aguardando_decisao"  # compras preparadas, sem política → revisão
    t = R.db.one("SELECT * FROM tasks WHERE title LIKE 'Conferir estoque%'")
    assert t["status"] == "concluida" and t["completed_by"] == "agent:agt_estoque"
    assert "Verificação de estoque concluída" in R.db.val("SELECT body FROM task_comments WHERE task_id=?", t["id"])
    reqs = R.db.all("SELECT * FROM action_requests WHERE action='estoque.compra'")
    assert reqs and all(x["status"] == "aguardando_aprovacao" for x in reqs)
    assert "Sem política cadastrada" in reqs[0]["reason"]
    runs = admin.get("/api/agents/agt_estoque/runs").json()["data"]
    assert runs[0]["tools"] and runs[0]["cost_minor"] == 0
    # segunda execução não duplica solicitações
    admin.post("/api/agents/estoque/run")
    assert len(R.db.all("SELECT * FROM action_requests WHERE action='estoque.compra'")) == len(reqs)
    # aprovar compra não executa nada externo: não há integração com fornecedor
    fin_ok = admin.post(f"/api/approvals/{reqs[0]['id']}/decide", json={"approve": True})
    assert fin_ok.status_code == 200 and fin_ok.json()["data"]["status"] == "aguardando_integracao"


def test_pausa_global(admin, app):
    admin.post("/api/agents/pause", json={"paused": True})
    r = admin.post("/api/agents/coordenador/run")
    assert r.status_code == 409 and r.json()["error"] == "agentes_pausados"
    n = R.db.val("SELECT COUNT(*) FROM agent_runs")
    app.state.tick()
    assert R.db.val("SELECT COUNT(*) FROM agent_runs") == n
    admin.post("/api/agents/pause", json={"paused": False})
    assert admin.post("/api/agents/coordenador/run").status_code == 200


def test_texto_externo_e_dado_nao_instrucao(admin):
    r = admin.post("/api/agents/catalogo/run").json()["data"]
    assert r["status"] == "aguardando_integracao"  # sem credencial de IA: não finge sucesso
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE pendency_type='conteudo_suspeito'") == 1
    assert R.db.val("SELECT COUNT(*) FROM action_requests WHERE action='reembolso.executar'") == 0
    assert R.db.val("SELECT COUNT(*) FROM policies WHERE action='reembolso.executar' AND mode='revisao'") == 1


def test_agente_desativado(admin):
    assert admin.post("/api/agents/atendimento/run").json()["error"] == "agente_desativado"


def test_politica_proibida_bloqueia_agente(app):
    from app import policies
    from app.core import Principal, Forbidden
    with pytest.raises(Forbidden):
        with R.db.tx() as c:
            policies.request_action(c, "campanha.paga", {}, Principal("agent", "agt_analise", "agente"), 1000, "BRL")


def test_financeiro_prepara_mas_nao_aprova(admin):
    agent_run = admin.post("/api/agents/financeiro/run").json()["data"]
    assert agent_run["status"] == "concluido"
    assert R.db.val("SELECT COUNT(*) FROM closings WHERE approved_by LIKE 'agent:%'") == 0


# ---------------------------------------------------------------- sinalização dos prédios
def test_sinalizacao_dos_predios(admin):
    w = {p["id"]: p["status"] for p in admin.get("/api/world").json()["data"]["partners"]}
    assert w["prt_ipe"]["level"] == "cinza"  # sem dados ≠ saudável
    assert any("Nenhum dado" in r["message"] for r in w["prt_ipe"]["reasons"])
    assert w["prt_lisboa"]["level"] == "amarelo" and any("Integração com falha" in r["message"] for r in w["prt_lisboa"]["reasons"])
    assert w["prt_aurora"]["level"] in ("amarelo", "vermelho")
    for st in w.values():
        for r in st["reasons"]:
            assert r["source"] and r["updated_at"] and r["action"]["label"]
    dbmod.advance_clock(hours=30)  # sessão expira (12 h) e os dados ficam velhos
    assert admin.get("/api/world").status_code == 401
    from app import ops
    for pid in w:
        assert ops.building_status(pid)["level"] in ("cinza", "vermelho")


def test_predio_do_parceiro_brasileiro(marina):
    b = marina.get("/api/partners/prt_aurora/building").json()["data"]
    f = b["floors"]
    assert set(f) == {"recepcao", "comercial", "operacao", "financeiro", "pendencias", "aplicativos", "equipe"}
    assert b["partner"]["is_demo"] == 1 and b["environment"] == "demo"
    assert any(t["assignee_type"] == "agente" for t in f["operacao"]["tasks"])
    assert any(t["kind"] == "pendencia" for t in f["pendencias"]["items"])
    assert f["financeiro"]["closings"] and f["financeiro"]["closings"][0]["is_demo"] == 1
    kinds = {a["kind"] for a in f["aplicativos"]}
    assert kinds == {"modulo_interno", "integracao_conectada", "link_externo", "planejada"}
    link = next(a for a in f["aplicativos"] if a["kind"] == "link_externo")
    assert link["kind_label"] == "Link para serviço externo"
    units = {u["id"]: u for u in f["recepcao"]["units"]}
    assert units["uni_pinheiros"]["location_status"] == "confirmada" and units["uni_campinas"]["location_status"] == "nao_confirmada"
