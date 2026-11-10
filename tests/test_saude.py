"""Vigia do sistema: erros de tela, verificações, alertas, cópia de segurança, imagens do mapa e supervisor."""
import sqlite3

import pytest

from app import geo, saude
from app.core import R

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 2000
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


@pytest.fixture(autouse=True)
def limpa():
    saude._cache.update(at=0.0, result=None, prev={})
    saude._rate.update(window=0.0, count=0)
    yield
    geo.TRANSPORT["get"] = None


def test_health_mostra_versao_e_saude(public):
    h = public.get("/api/health").json()
    assert h["build"] == saude.BUILD and len(h["build"]) == 12
    assert h["saude"]["level"] in saude.LEVELS


def test_erro_de_tela_registrado_agrupado_e_sem_csrf_recusado(public, admin):
    r = public.post("/api/saude/erro-tela", json={"message": "x is undefined"})
    assert r.status_code == 403                                   # sem cabeçalho anti-CSRF
    hdr = {"X-Requested-With": "plataforma"}
    for _ in range(3):                                            # antes do login também é aceito
        assert public.post("/api/saude/erro-tela", headers=hdr, json={"message": "Cannot read properties of undefined (reading 'lat')",
                           "source": "/static/js/world.js", "line": "140", "where": "3d:inicio", "build": "abc"}).json()["data"]["accepted"]
    d = admin.get("/api/saude").json()["data"]
    e = [x for x in d["client_errors"] if "reading 'lat'" in x["message"]]
    assert len(e) == 1 and e[0]["count"] == 3 and e[0]["onde"] == "3d:inicio"
    assert any(c["code"] == "tela" and c["level"] == "amarelo" for c in admin.get("/api/saude?agora=1").json()["data"]["checks"])
    assert admin.post("/api/saude/erros/resolver").json()["data"]["resolved"] == 1
    assert next(c for c in admin.get("/api/saude?agora=1").json()["data"]["checks"] if c["code"] == "tela")["level"] == "verde"


def test_limite_de_erros_por_minuto(public):
    hdr = {"X-Requested-With": "plataforma"}
    res = [public.post("/api/saude/erro-tela", headers=hdr, json={"message": f"e{i}"}).json()["data"]["accepted"] for i in range(65)]
    assert res[:60] == [True] * 60 and not any(res[60:])


def test_saude_so_para_central(marina, admin):
    assert marina.get("/api/saude").status_code == 403
    d = admin.get("/api/saude?agora=1").json()["data"]
    codes = {c["code"] for c in d["checks"]}
    assert {"banco", "versao", "mapa", "ia_local", "whatsapp", "webhooks", "agentes", "tela", "backup"} <= codes
    assert next(c for c in d["checks"] if c["code"] == "banco")["level"] == "verde"
    assert next(c for c in d["checks"] if c["code"] == "versao")["level"] == "verde"


def test_copia_de_seguranca_valida_e_rotativa(app, tmp_path):
    for _ in range(4):
        p = saude.backup_now(keep=2)
    files = sorted(saude.backup_dir().glob("demo_*.sqlite3"))
    assert 1 <= len(files) <= 2 and p in files
    con = sqlite3.connect(str(p))
    assert con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    assert con.execute("SELECT COUNT(*) FROM partners").fetchone()[0] > 0
    con.close()
    assert saude.backup_if_due() is None                          # já existe uma recente


def test_vermelho_gera_alerta_e_uma_unica_pendencia(app, admin):
    with R.db.tx() as c:
        c.execute("INSERT INTO webhook_inbox(id,provider,external_event_id,event_type,payload,received_at,status,attempts)"
                  " VALUES ('wi_x','teste','ev1','x','{}','2026-01-01T00:00:00Z','dead_letter',5)")
    saude.routine(); saude._cache["at"] = 0; saude.routine()      # duas voltas: não duplica
    n = R.db.val("SELECT COUNT(*) FROM tasks WHERE related_entity='saude:webhooks' AND status NOT IN ('concluida','cancelada')")
    assert n == 1
    assert R.db.val("SELECT COUNT(*) FROM events WHERE type='sistema.alerta'") == 1
    with R.db.tx() as c:
        c.execute("UPDATE webhook_inbox SET status='processado'")
    saude._cache["at"] = 0; saude.routine()
    assert R.db.val("SELECT COUNT(*) FROM events WHERE type='sistema.normalizado'") == 1


def test_imagem_de_satelite_baixada_uma_vez_e_cacheada(app, admin):
    calls = []
    geo.TRANSPORT["get"] = lambda url: (calls.append(url), JPEG)[1]
    r = admin.get("/api/geo/imagem/mundo")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and "gibs.earthdata.nasa.gov" in calls[0]
    admin.get("/api/geo/imagem/mundo")
    assert len(calls) == 1
    r = admin.get("/api/geo/imagem/regiao?bbox=-53,-25,-44,-19.8")
    assert r.status_code == 200 and r.headers["X-Bbox"]
    assert admin.get("/api/geo/imagem/regiao?bbox=-180,-90,180,90").status_code == 400   # grande demais


def test_imagem_indisponivel_sem_internet_nao_quebra(app, admin):
    def falha(url): raise OSError("sem rede")
    geo.TRANSPORT["get"] = falha
    assert admin.get("/api/geo/imagem/mundo").status_code == 204
    assert admin.get("/api/geo/tile/10/379/580").status_code == 204
    geo.TRANSPORT["get"] = lambda url: b"<html>erro</html>"        # resposta que não é imagem
    assert admin.get("/api/geo/imagem/mundo").status_code == 204


def test_tiles_de_ruas_validados_e_cacheados(app, admin, public):
    calls = []
    geo.TRANSPORT["get"] = lambda url: (calls.append(url), PNG)[1]
    assert admin.get("/api/geo/tile/10/379/580").status_code == 200
    admin.get("/api/geo/tile/10/379/580")
    assert len(calls) == 1 and "tile.openstreetmap.org/10/379/580.png" in calls[0]
    assert admin.get("/api/geo/tile/10/5000/1").status_code == 400
    assert admin.get("/api/geo/tile/2/1/1").status_code == 400
    assert public.get("/api/geo/tile/10/379/580").status_code == 401  # só usuário logado


def test_mundo_traz_operacoes_por_unidade(admin):
    w = admin.get("/api/world").json()["data"]
    assert "hq" in w and w["hq"]["lat"] is not None
    us = [u for p in w["partners"] for u in p["units"]]
    assert us and all(set(u["ops"]) == {"abertas", "atrasadas", "feitas_30d", "ultima"} for u in us)


def test_cache_do_navegador_sempre_revalida(public):
    for path in ("/", "/static/js/world.js", "/static/css/app.css"):
        assert public.get(path).headers.get("cache-control") == "no-cache"


def test_supervisor_reinicia_quando_filho_sai_com_atualizacao(monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("rodar", Path(__file__).resolve().parents[1] / "server" / "rodar.py")
    rodar = importlib.util.module_from_spec(spec); spec.loader.exec_module(rodar)
    codes = iter([rodar.EXIT_UPDATE, 1, 0])

    class P:
        def __init__(self, *a, **k): self.c = next(codes)
        def wait(self, timeout=None): return self.c
    monkeypatch.setattr(rodar.subprocess, "Popen", P)
    monkeypatch.setattr(rodar.time, "sleep", lambda s: None)
    monkeypatch.setattr(rodar, "port_busy", lambda h, p: False)
    monkeypatch.setattr(rodar, "log", lambda m: None)
    assert rodar.supervise("127.0.0.1", 1, 1) == 0                # atualização → reinicia; erro → espera e reinicia; 0 → sai
