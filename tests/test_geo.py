"""Mapa real: decodificação TopoJSON, estados por país, geocodificação (offline e online simulada)."""
import json

import pytest

from app import geo
from app.core import R

TOPO = {"type": "Topology", "transform": {"scale": [0.01, 0.01], "translate": [-50, -30]},
        "objects": {"countries": {"type": "GeometryCollection", "geometries": [
            {"type": "Polygon", "arcs": [[0]], "id": "076", "properties": {"name": "Brazil"}},
            {"type": "MultiPolygon", "arcs": [[[1]], [[2]]], "id": "620", "properties": {"name": "Portugal"}}]}},
        "arcs": [[[0, 0], [1000, 0], [0, 1000], [-1000, 0], [0, -1000]],
                 [[4000, 6000], [100, 0], [0, 100], [-100, 0], [0, -100]],
                 [[4200, 6000], [50, 0], [0, 50], [-50, 0], [0, -50]]]}
ADMIN1 = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"admin": "Brazil", "name": "São Paulo", "postal": "SP", "type_en": "State"},
     "geometry": {"type": "Polygon", "coordinates": [[[-53, -25], [-44, -25], [-44, -19.8], [-53, -19.8], [-53, -25]]]}},
    {"type": "Feature", "properties": {"admin": "Brazil", "name": "Minas Gerais", "postal": "MG", "type_en": "State"},
     "geometry": {"type": "Polygon", "coordinates": [[[-51, -22], [-40, -22], [-40, -14], [-51, -14], [-51, -22]]]}},
    {"type": "Feature", "properties": {"admin": "Portugal", "name": "Lisboa", "postal": "LI", "type_en": "District"},
     "geometry": {"type": "MultiPolygon", "coordinates": [[[[-9.5, 38.6], [-8.8, 38.6], [-8.8, 39.3], [-9.5, 38.6]]]]}}]}


@pytest.fixture()
def rede(app):
    calls = []

    def get(url):
        calls.append(url)
        return json.dumps(TOPO if "world-atlas" in url else ADMIN1).encode()
    geo.TRANSPORT["get"] = get
    geo._state.update(admin1="idle", admin1_error=None)
    yield calls
    geo.TRANSPORT["get"] = None
    geo.TRANSPORT["geocode"] = None


def test_paises_decodificados_e_cacheados(rede, admin):
    d = admin.get("/api/geo/countries").json()["data"]
    assert d["available"] and {c["name"] for c in d["countries"]} == {"Brazil", "Portugal"}
    br = next(c for c in d["countries"] if c["name"] == "Brazil")
    assert br["name_pt"] == "Brasil" and br["bbox"] == [-50, -30, -40, -20]
    assert len(next(c for c in d["countries"] if c["name"] == "Portugal")["rings"]) == 2  # multipolígono
    admin.get("/api/geo/countries")
    assert len([u for u in rede if "world-atlas" in u]) == 1  # baixa uma vez só


def test_estados_por_pais(rede, admin):
    d = admin.get("/api/geo/states?country=Brazil").json()["data"]
    assert d["status"] == "pronto" and {s["code"] for s in d["states"]} == {"SP", "MG"}
    assert admin.get("/api/geo/states?country=Portugal").json()["data"]["states"][0]["name"] == "Lisboa"
    assert admin.get("/api/geo/states?country=Atlantida").json()["data"]["states"] == []


def test_sem_internet_nao_quebra(app, admin):
    geo.TRANSPORT["get"] = lambda url: (_ for _ in ()).throw(OSError("sem rede"))
    try:
        d = admin.get("/api/geo/countries").json()["data"]
        assert d["available"] is False and d["countries"] == []
    finally:
        geo.TRANSPORT["get"] = None


def test_geocodificacao_offline_online_e_cache(app):
    assert geo.geocode("São Paulo", "SP")[:2] == (-23.5505, -46.6333)
    assert geo.geocode("SAO JOSE DOS CAMPOS", "sp")[2] == "cidade (tabela embutida)"
    calls = []
    geo.TRANSPORT["geocode"] = lambda c, s, k: calls.append(c) or (-22.0, -48.0)
    try:
        assert geo.geocode("Cidade Nova Fictícia", "SP")[:2] == (-22.0, -48.0)
        geo.geocode("Cidade Nova Fictícia", "SP")
        assert calls == ["Cidade Nova Fictícia"]  # segunda vez vem do cache
    finally:
        geo.TRANSPORT["geocode"] = None


def test_agendador_preenche_coordenadas_e_cadastro_aceita_manual(app, admin):
    with R.db.tx() as c:
        c.execute("UPDATE partner_units SET lat=NULL, lng=NULL")
    n = geo.geocode_missing(limit=50)
    assert n >= 5
    u = R.db.one("SELECT * FROM partner_units WHERE id='uni_pinheiros'")
    assert round(u["lat"], 2) == -23.55 and u["geo_source"].startswith("cidade")
    cid = admin.post("/api/cadastros/clientes", json={"name": "Cliente Mapa"}).json()["data"]["id"]
    r = admin.post("/api/cadastros/unidades", json={"client_id": cid, "name": "Sítio", "city": "Lugar Sem Nome", "state": "SP",
                                                    "coords": "-22.5, -47.1"})
    u = R.db.one("SELECT * FROM partner_units WHERE id=?", r.json()["data"]["id"])
    assert (u["lat"], u["lng"], u["geo_source"]) == (-22.5, -47.1, "informada manualmente")
    bad = admin.post("/api/cadastros/unidades", json={"client_id": cid, "name": "X", "city": "Y", "state": "SP", "coords": "999,1"})
    assert bad.status_code == 400


def test_simplificacao_mantem_forma():
    pts = [(0, 0), (1, 0.001), (2, 0), (2, 2), (0, 2), (0, 0)]
    s = geo._simplify(pts, 0.01)
    assert (1, 0.001) not in s and s[0] == (0, 0) and len(s) == 5
