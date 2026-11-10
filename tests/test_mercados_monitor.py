"""Expansão internacional (mercado por país, CEO, agentes, site com hreflang) e Sentinela (monitor de segurança)."""
import json

import pytest

from app import brain, monitor, paises
from app.core import R


def test_catalogo_de_paises():
    for k in ("Germany", "Alemanha", "DE", "alemanha"):
        assert paises.lookup(k)["currency"] == "EUR"
    assert paises.lookup("United States of America")["lang"] == "en-US"
    assert paises.lookup("Atlântida") is None


def _criar(admin, **kw):
    data = {"country": "Germany", "brand": "VirtusX IT", "offer": "Suporte de TI remoto para pequenas empresas", "email": "contato@virtusx.example",
            "price": "49"} | kw
    return admin.post("/api/mercados", json=data)


def test_criar_mercado_permissoes_e_duplicado(admin, ops, marina):
    assert _criar(ops).status_code == 403                                       # operações vê, não cria
    r = _criar(admin)
    assert r.status_code == 200, r.text
    m = r.json()["data"]
    assert (m["lang"], m["currency"], m["presence"]) == ("de-DE", "EUR", "remota") and m["status"] in ("planejando", "em_execucao")
    assert ops.get(f"/api/mercados/{m['id']}").status_code == 200
    assert marina.get("/api/mercados").status_code == 403                        # parceiro não vê expansão
    assert _criar(admin).status_code == 409                                     # um mercado ativo por país
    assert _criar(admin, country="Atlântida", lang="x1", currency="EU").status_code == 400
    r = _criar(admin, country="Atlântida", iso2="AT", lang="de", currency="EUR", brand="X", offer="Teste de país fora do catálogo")
    assert r.status_code in (200, 409)
    w = admin.get("/api/world").json()["data"]
    assert any(x["id"] == m["id"] and x["lat"] for x in w["markets"])


def test_ceo_divide_em_agentes_e_site_publicado_so_com_aprovacao(admin, public):
    m = _criar(admin).json()["data"]
    assert admin.post(f"/api/agents/mkt_{m['id']}/run").json()["data"]["status"] == "concluido"
    d = admin.get(f"/api/mercados/{m['id']}").json()["data"]
    assert set(d["progresso"]["setores"]) == {"juridico", "localizacao", "marketing", "vendas", "financeiro"}
    assert any(i["kind"] == "estrategia" for i in d["itens"])
    admin.post(f"/api/agents/mkt_{m['id']}/run")                                   # segunda volta não duplica tarefas
    assert admin.get(f"/api/mercados/{m['id']}").json()["data"]["progresso"]["total"] == 5
    for a in ("juridico", "localizacao", "marketing", "vendas", "financeiro"):
        assert admin.post(f"/api/agents/{a}/run").json()["data"]["status"] == "concluido", a
    d = admin.get(f"/api/mercados/{m['id']}").json()["data"]
    kinds = {i["kind"] for i in d["itens"]}
    assert {"juridico", "localizacao", "marketing", "vendas", "financeiro"} <= kinds
    setores = [t for t in d["tarefas"] if not t["related_entity"].endswith("revisao_juridica")]
    assert len(setores) == 5 and all(t["status"] == "concluida" for t in setores)
    assert any(t["related_entity"].endswith("revisao_juridica") and t["status"] == "a_fazer" for t in d["tarefas"])   # advogado: pessoa
    legal = next(i for i in d["itens"] if i["kind"] == "juridico")["body"]
    assert "NÃO é parecer jurídico" in legal["aviso"] and any(x["tema"] == "Impressum" for x in legal["itens"])
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE related_entity=?", f"market:{m['id']}:revisao_juridica") == 1
    ads = next(i for i in d["itens"] if i["kind"] == "marketing")["body"]
    assert all(len(t) <= 30 for t in ads["google"]["titulos"]) and "aprovação" in ads["verba"]
    # site: só prévia (noindex) até aprovar
    assert public.get(f"/m/{d['slug']}").status_code == 404
    pv = admin.get(f"/m/{d['slug']}")
    assert pv.status_code == 200 and "PRÉVIA" in pv.text and 'name="robots" content="noindex"' in pv.text
    assert d["publicacao"]["status"] == "aguardando_aprovacao"
    assert admin.post(f"/api/approvals/{d['publicacao']['id']}/decide", json={"approve": True}).status_code == 200
    pg = public.get(f"/m/{d['slug']}")
    assert pg.status_code == 200 and '<html lang="de-DE">' in pg.text and 'hreflang="de-DE"' in pg.text and "EUR" in pg.text
    assert "Remote-Service in Germany" in pg.text and "noindex" not in pg.text
    ld = json.loads(pg.text.split('application/ld+json">')[1].split("</script>")[0])
    assert "address" not in ld and ld["areaServed"] == "Alemanha"                # nada de endereço inventado
    assert f"/m/{d['slug']}" in public.get("/sitemap.xml").text
    assert "Sitemap" in public.get("/robots.txt").text
    assert admin.get(f"/api/mercados/{m['id']}").json()["data"]["status"] == "ativo"


@pytest.fixture()
def sentinela(app, tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("starlette>=0.37\nhttpx\n")
    (tmp_path / ".gitignore").write_text("config.local.env\ndata/\n")
    (tmp_path / "config.local.env.exemplo").write_text("ANTHROPIC_API_KEY=\n")
    monkeypatch.setattr(monitor, "ROOT", tmp_path)
    issues = []
    monitor.TRANSPORT.update(osv=lambda pk: {n: (["GHSA-test-0001"] if n == "starlette" else []) for n in pk},
                             pypi=lambda n: "999.0", github=lambda t, b: (issues.append((t, b)), "https://github.com/x/y/issues/1")[1])
    yield tmp_path, issues
    monitor.TRANSPORT.update(osv=None, pypi=None, github=None)


def test_sentinela_propoe_e_so_age_com_aprovacao(sentinela, admin):
    root, issues = sentinela
    assert admin.post("/api/agents/monitor/run").json()["data"]["status"] == "concluido"
    ov = admin.get("/api/monitor").json()["data"]
    codes = {a["code"]: a for a in ov["achados"]}
    assert codes["vuln:starlette"]["severity"] == "alta" and "versao:starlette" in codes
    reqs = R.db.all("SELECT * FROM action_requests WHERE action='sistema.corrigir'")
    assert len(reqs) == 1 and reqs[0]["status"] == "aguardando_aprovacao" and not issues   # nada aplicado sozinho
    admin.post("/api/agents/monitor/run")
    assert R.db.val("SELECT COUNT(*) FROM action_requests WHERE action='sistema.corrigir'") == 1
    assert admin.post(f"/api/approvals/{reqs[0]['id']}/decide", json={"approve": True}).status_code == 200
    assert len(issues) == 1 and "@claude" in issues[0][1]
    # segredo no modelo = crítico
    (root / "config.local.env.exemplo").write_text("ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnop\n")
    admin.post("/api/agents/monitor/run")
    a = {x["code"]: x for x in admin.get("/api/monitor").json()["data"]["achados"]}
    assert a["segredo:modelo"]["severity"] == "critica" and "sk-ant-api03" not in json.dumps(a)
    # corrigido → achado resolvido sozinho
    (root / "config.local.env.exemplo").write_text("ANTHROPIC_API_KEY=\n")
    admin.post("/api/agents/monitor/run")
    assert "segredo:modelo" not in {x["code"] for x in admin.get("/api/monitor").json()["data"]["achados"]}


def test_gasto_do_monitor_separado(app, admin):
    old = (R.settings.monitor_api_key, R.settings.anthropic_api_key)
    R.settings.monitor_api_key, R.settings.anthropic_api_key = "sk-ant-monitor-" + "x" * 50, ""
    brain.TRANSPORT["claude"] = lambda p: {"usage": {"input_tokens": 1000, "output_tokens": 500}, "content": [{"text": '{"ok": 1}'}]}
    try:
        out = brain.think("s", "u", tier="decisao", purpose="analise", profile="monitor")
        assert out["provider"] == "claude"
        assert brain.month_spend_usd("monitor") > 0 and brain.month_spend_usd() == 0
        R.settings.monitor_monthly_cap_usd = 0.0001
        with pytest.raises(brain.BrainUnavailable):                              # teto próprio
            brain.think("s", "u", tier="decisao", purpose="analise", profile="monitor")
    finally:
        brain.TRANSPORT["claude"] = None
        R.settings.monitor_api_key, R.settings.anthropic_api_key = old
        R.settings.monitor_monthly_cap_usd = 5.0


def test_bloqueio_de_forca_bruta(public):
    codes = [public.post("/api/auth/login", json={"email": "admin@central.demo", "password": "errada"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429
    assert R.db.val("SELECT COUNT(*) FROM events WHERE type='sessao.falha_login'") == 10
