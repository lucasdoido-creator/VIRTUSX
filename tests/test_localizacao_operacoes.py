"""Posição exata pelo endereço, sede configurável e operações manuais para agentes."""
import pytest

from app import geo
from app.core import R


@pytest.fixture()
def nominatim():
    calls = []

    def fake(city, state, country):
        calls.append(city)
        if "Rua Longe" in city:
            return (-3.1, -60.0)                    # resultado em Manaus para endereço de Mogi Guaçu → rejeitar
        if "Rua Inexistente" in city:
            return None
        if "Av. Brasil, 100" in city:
            return (-22.3801, -46.9502)             # ~1 km do centro de Mogi Guaçu
        if city.lower().startswith("mogi"):
            return (-22.372222, -46.9425)
        return None
    geo.TRANSPORT["geocode"] = fake
    yield calls
    geo.TRANSPORT["geocode"] = None


def _unit(admin, cid, name, address, city="Mogi Guaçu"):
    r = admin.post("/api/cadastros/unidades", json={"client_id": cid, "name": name, "city": city, "state": "SP", "address": address})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_endereco_exato_aceito_longe_rejeitado_e_cache(app, admin, nominatim):
    cid = admin.post("/api/cadastros/clientes", json={"name": "Sportbar"}).json()["data"]["id"]
    ok = _unit(admin, cid, "Loja 1", "Av. Brasil, 100")
    longe = _unit(admin, cid, "Loja 2", "Rua Longe, 5")
    nada = _unit(admin, cid, "Loja 3", "Rua Inexistente, 9")
    for _ in range(3):
        geo.geocode_missing()
    u = {r["id"]: r for r in R.db.all("SELECT * FROM partner_units WHERE id IN (?,?,?)", ok, longe, nada)}
    assert u[ok]["geo_source"] == geo.ADDR_SRC and round(u[ok]["lat"], 4) == -22.3801
    assert u[longe]["geo_source"].startswith("cidade") and round(u[longe]["lat"], 2) == -22.37   # fica no centro da cidade
    assert u[nada]["geo_source"].startswith("cidade")
    n = len(nominatim)
    geo.geocode_missing(); geo.geocode_missing()
    assert len(nominatim) == n                                 # endereço nunca consultado duas vezes (cache inclusive negativo)
    loc = {x["id"]: x for x in admin.get("/api/cadastros").json()["data"]["units"]}
    assert loc[ok]["geo_source"] == "endereço (OpenStreetMap)"


def test_sem_rede_tenta_depois(app, admin):
    def off(*a): raise OSError("sem rede")
    geo.TRANSPORT["geocode"] = off
    try:
        cid = admin.post("/api/cadastros/clientes", json={"name": "Cliente X"}).json()["data"]["id"]
        uid = _unit(admin, cid, "Matriz", "Rua A, 1", city="Campinas")
        geo.geocode_missing()
        assert R.db.one("SELECT COUNT(*) n FROM research_cache WHERE key LIKE 'geoaddr:%'")["n"] == 0   # não grava "não achou"
        assert R.db.one("SELECT geo_source FROM partner_units WHERE id=?", uid)["geo_source"].startswith("cidade")
    finally:
        geo.TRANSPORT["geocode"] = None


def test_editar_unidade_recalcula_e_manual_tem_prioridade(app, admin, nominatim):
    cid = admin.post("/api/cadastros/clientes", json={"name": "Cliente Y"}).json()["data"]["id"]
    uid = _unit(admin, cid, "Matriz", "Av. Brasil, 100")
    geo.geocode_missing()
    r = admin.put(f"/api/cadastros/unidades/{uid}", json={"client_id": cid, "name": "Matriz", "city": "Mogi Guaçu", "state": "SP",
                                                         "address": "Av. Brasil, 100", "coords": "-22.38, -46.95"})
    assert r.status_code == 200
    geo.geocode_missing()
    u = R.db.one("SELECT * FROM partner_units WHERE id=?", uid)
    assert (u["lat"], u["geo_source"]) == (-22.38, "informada manualmente")   # agendador não sobrescreve o manual


def test_sede_padrao_configuravel_e_privada(app, admin, marina, nominatim):
    w = admin.get("/api/world").json()["data"]
    assert w["hq"]["padrao"] is True
    r = admin.post("/api/cadastros/sede", json={"name": "Sede VIRTUSX", "city": "Mogi Guaçu", "state": "SP", "address": "Av. Brasil, 100"})
    assert r.status_code == 200, r.text
    geo.geocode_missing()
    h = admin.get("/api/world").json()["data"]["hq"]
    assert h["padrao"] is False and h["name"] == "Sede VIRTUSX" and h["geo_source"] == geo.ADDR_SRC and round(h["lat"], 4) == -22.3801
    hp = marina.get("/api/world").json()["data"]["hq"]
    assert "city" not in hp and "name" not in hp                    # parceiro não vê dados da sede
    assert marina.post("/api/cadastros/sede", json={"city": "X"}).status_code == 403
    assert admin.get("/api/cadastros").json()["data"]["sede"]["city"] == "Mogi Guaçu"


def test_operacao_manual_para_agentes(app, admin):
    ags = {a["code"]: a for a in admin.get("/api/agents").json()["data"]}
    t1 = admin.post("/api/tasks", json={"title": "Revisar prazos e pendências", "description": "foco nos atrasos",
                                        "assignee_type": "agente", "assignee_id": ags["coordenador"]["id"], "source": "manual"}).json()["data"]
    t2 = admin.post("/api/tasks", json={"title": "Analisar conta do cliente", "partner_id": "prt_aurora",
                                        "assignee_type": "agente", "assignee_id": ags["analise"]["id"], "source": "manual"}).json()["data"]
    assert admin.post("/api/agents/coordenador/run").status_code == 200
    assert admin.post("/api/agents/analise/run").status_code == 200
    for t in (t1, t2):
        d = admin.get(f"/api/tasks/{t['id']}").json()["data"]
        assert d["status"] == "concluida", d["status"]
        assert any("Revisão de prazos" in c["body"] or "Análise" in c["body"] for c in d["comments"]), d["comments"]
    mine = admin.get(f"/api/tasks?assignee_id={ags['coordenador']['id']}").json()["data"]
    assert [x["id"] for x in mine] == [t1["id"]]
