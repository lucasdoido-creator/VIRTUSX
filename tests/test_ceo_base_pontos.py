"""CEO de cada base (organiza tarefas dos lugares atendidos), freelancers no mapa e pontos de cashback dos clientes."""
from datetime import timedelta

from app import pontos
from app.core import R
from app.db import iso, now


def _run_base(admin):
    r = admin.post("/api/agents/base_sb_demo/run")
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_base_tem_ceo_e_freelancers_so_para_central(admin, marina):
    ags = {a["code"]: a for a in admin.get("/api/agents").json()["data"]}
    assert ags["base_sb_demo"]["name"] == "CEO da base · Base Demo · Guarulhos"
    w = admin.get("/api/world").json()["data"]
    sb = next(b for b in w["service_bases"] if b["id"] == "sb_demo")
    assert {f["name"] for f in sb["freelancers"]} == {"Rafa · freelancer (DEMO)", "Bia · freelancer (DEMO)"}
    assert marina.get("/api/world").json()["data"]["service_bases"] == []          # parceiro não vê equipe nem bases
    assert marina.get("/api/bases/sb_demo/ceo").status_code == 403


def test_preventiva_mensal_e_distribuicao_com_aprovacao(admin):
    _run_base(admin)
    prev = R.db.all("SELECT * FROM tasks WHERE source='ceo_base' AND template='preventiva_ti'")
    assert {t["unit_id"] for t in prev} == {"uni_pinheiros", "uni_campinas"}
    reqs = R.db.all("SELECT * FROM action_requests WHERE action='tarefas.atribuir'")
    assert len(reqs) == 1 and reqs[0]["status"] == "aguardando_aprovacao"          # sem política: só propõe
    assert all(t["assignee_id"] is None for t in R.db.all("SELECT assignee_id FROM tasks WHERE source='ceo_base'"))
    _run_base(admin)                                                                # segunda volta: não duplica nada
    assert R.db.val("SELECT COUNT(*) FROM action_requests WHERE action='tarefas.atribuir'") == 1
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE source='ceo_base' AND template='preventiva_ti'") == 2
    r = admin.post(f"/api/approvals/{reqs[0]['id']}/decide", json={"approve": True})
    assert r.status_code == 200, r.text
    owners = [t["assignee_id"] for t in R.db.all("SELECT assignee_id FROM tasks WHERE source='ceo_base' AND template='preventiva_ti'")]
    assert sorted(owners) == ["usr_tec_bia", "usr_tec_rafa"]                        # carga equilibrada: uma para cada
    g = admin.get("/api/bases/sb_demo/ceo").json()["data"]
    assert "unidade" in g["resumo"] and len(g["dados"]["por_unidade"]) == 2


def test_politica_automatica_aplica_direto(admin):
    assert admin.put("/api/policies/tarefas.atribuir", json={"mode": "automatico"}).status_code == 200
    _run_base(admin)
    assert R.db.one("SELECT status FROM action_requests WHERE action='tarefas.atribuir'")["status"] == "executada"
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE source='ceo_base' AND assignee_id IS NOT NULL") == 2


def test_atraso_maior_que_2_dias_vai_para_central_uma_vez(admin):
    tid = admin.post("/api/tasks", json={"title": "Trocar switch", "partner_id": "prt_aurora", "unit_id": "uni_campinas",
                                         "due_at": iso(now() - timedelta(days=3))}).json()["data"]["id"]
    _run_base(admin); _run_base(admin)
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE related_entity=? AND kind='pendencia'", f"task:{tid}") == 1


def test_pontos_uma_vez_por_servico_nivel_e_regra(admin, marina, joao):
    def concluir(title):
        tid = admin.post("/api/tasks", json={"title": title, "partner_id": "prt_aurora"}).json()["data"]["id"]
        r = admin.patch(f"/api/tasks/{tid}", json={"status": "concluida"})
        assert r.status_code == 200, r.text
        return tid
    base = pontos.summary_by_partner().get("prt_aurora", {"pontos": 0, "servicos": 0})
    t1 = concluir("Serviço 1")
    with R.db.tx() as c:                                                            # repetir o crédito não soma em dobro
        assert pontos.award(c, dict(c.execute("SELECT * FROM tasks WHERE id=?", (t1,)).fetchone())) == 0
    s = pontos.summary_by_partner()["prt_aurora"]
    assert s["pontos"] == base["pontos"] + 10 and s["servicos"] == base["servicos"] + 1
    assert admin.post("/api/cadastros/pontos", json={"por_servico": 25}).json()["data"]["por_servico"] == 25
    concluir("Serviço 2")
    assert pontos.summary_by_partner()["prt_aurora"]["pontos"] == base["pontos"] + 35
    for i in range(8):
        concluir(f"Serviço extra {i}")
    w = {p["id"]: p for p in admin.get("/api/world").json()["data"]["partners"]}
    assert w["prt_aurora"]["fidelidade"]["nivel"] >= 3
    h = marina.get("/api/partners/prt_aurora/pontos").json()["data"]
    assert h["total"] == w["prt_aurora"]["fidelidade"]["pontos"] and h["lancamentos"][0]["points"] == 25
    assert joao.get("/api/partners/prt_aurora/pontos").status_code == 403         # outro parceiro não vê
    assert marina.post("/api/cadastros/pontos", json={"por_servico": 99}).status_code == 403
    pid = admin.post("/api/tasks", json={"title": "Pendência X", "partner_id": "prt_aurora", "kind": "pendencia"}).json()["data"]["id"]
    before = pontos.summary_by_partner()["prt_aurora"]["pontos"]
    admin.patch(f"/api/tasks/{pid}", json={"status": "concluida"})
    assert pontos.summary_by_partner()["prt_aurora"]["pontos"] == before            # pendência não é serviço


def test_niveis():
    assert [pontos.level(n) for n in (0, 2, 3, 7, 8, 19, 20, 49, 50, 500)] == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
