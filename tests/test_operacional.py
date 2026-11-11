"""Financeiro manual e estoque de todas as operações (sede, bases, clientes, países)."""
from app.core import R


def test_financeiro_manual_lancar_totais_cancelar(fin, ops, marina):
    sc = {s["id"]: s for s in fin.get("/api/operacoes").json()["data"]}
    assert "central" in sc and "sb_demo" in sc and "prt_aurora" in sc
    add = lambda **k: fin.post("/api/financeiro-manual", json={"scope_type": "base", "scope_id": "sb_demo", "date": "2026-11-05", **k})
    r = add(kind="receita", category="serviços", description="Contrato manutenção", amount="1.250,90")
    assert r.status_code == 200 and r.json()["data"]["amount_minor"] == 125090
    add(kind="despesa", category="materiais", description="Cabos e conectores", amount="200")
    p = add(kind="receita", category="mensalidade de parceiro", description="Mensalidade parceiro X", amount="99,90", status="previsto").json()["data"]
    assert add(kind="receita", description="x", amount="-5").status_code == 400
    assert add(kind="lucro", description="x", amount="5").status_code == 400
    assert ops.post("/api/financeiro-manual", json={"scope_type": "central", "kind": "receita", "description": "x", "amount": "1"}).status_code == 403
    assert marina.get("/api/financeiro-manual").status_code == 403
    t = fin.get("/api/financeiro-manual?month=2026-11").json()["data"]["totais"][0]
    assert (t["receitas"], t["despesas"], t["resultado"], t["previsto_receitas"]) == (125090, 20000, 105090, 9990)
    assert fin.post(f"/api/financeiro-manual/{p['id']}/realizar").status_code == 200
    assert fin.get("/api/financeiro-manual?month=2026-11").json()["data"]["totais"][0]["receitas"] == 135080
    assert fin.post(f"/api/financeiro-manual/{p['id']}/cancelar", json={}).status_code == 400      # motivo obrigatório
    assert fin.post(f"/api/financeiro-manual/{p['id']}/cancelar", json={"reason": "lançado em duplicidade"}).status_code == 200
    d = fin.get("/api/financeiro-manual?month=2026-11").json()["data"]
    assert d["totais"][0]["receitas"] == 125090 and len(d["lancamentos"]) == 3                          # nada é apagado
    assert R.db.val("SELECT COUNT(*) FROM audit_log WHERE action LIKE 'financeiro.%'") >= 4


def test_estoque_por_operacao_minimo_e_saldo(ops, fin, marina):
    it = ops.post("/api/estoque-operacoes", json={"scope_type": "base", "scope_id": "sb_demo", "name": "Cabo de rede Cat6", "sku": "CAB-6",
                                                 "unit": "m", "min_qty": "50", "cost": "1,20"}).json()["data"]
    pend = lambda: R.db.val("SELECT COUNT(*) FROM tasks WHERE related_entity=? AND status NOT IN ('concluida','cancelada')", f"stock:{it['id']}")
    assert pend() == 1                                                     # começa em 0 < 50: pendência
    mv = lambda kind, q, why="teste": ops.post(f"/api/estoque-operacoes/{it['id']}/movimento", json={"kind": kind, "qty": q, "reason": why})
    assert mv("entrada", "100").json()["data"]["qty"] == 100 and pend() == 0
    assert mv("saida", "60").json()["data"]["qty"] == 40 and pend() == 1
    assert mv("saida", "41").status_code == 409                            # nunca negativo
    assert mv("entrada", "5", "").status_code == 400                       # motivo obrigatório
    assert mv("ajuste", "70").json()["data"]["qty"] == 70 and pend() == 0
    lst = fin.get("/api/estoque-operacoes?scope_id=sb_demo").json()["data"]
    assert lst[0]["name"] == "Cabo de rede Cat6" and len(lst[0]["movimentos"]) == 3 and not lst[0]["baixo"]
    assert fin.post("/api/estoque-operacoes", json={"scope_type": "central", "name": "x"}).status_code == 403
    assert marina.get("/api/estoque-operacoes").status_code == 403
