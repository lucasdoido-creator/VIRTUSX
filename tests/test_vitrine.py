"""Identidade visual, site de alta conversão, captação de leads (consentimento, anti-spam, origem) e funil."""
from app import vitrine
from app.core import R


def _mercado_publicado(admin):
    m = admin.post("/api/mercados", json={"country": "Spain", "brand": "VirtusX", "offer": "Suporte de TI remoto para pequenas empresas",
                                         "email": "hola@virtusx.example", "whatsapp": "+34 600 000 000", "price": "49"}).json()["data"]
    admin.post(f"/api/agents/mkt_{m['id']}/run")
    admin.post("/api/agents/localizacao/run")
    d = admin.get(f"/api/mercados/{m['id']}").json()["data"]
    admin.post(f"/api/approvals/{d['publicacao']['id']}/decide", json={"approve": True})
    return d


def test_marca_aplicada_no_site(admin, ops, public):
    assert ops.post("/api/marca", json={"cor_primaria": "#112233"}).status_code == 403
    assert admin.post("/api/marca", json={"cor_primaria": "verde"}).status_code == 400
    b = admin.post("/api/marca", json={"nome": "VirtusX Global", "slogan": "TI que não para", "cor_primaria": "#112233", "fonte": "serif"}).json()["data"]
    assert b["cor_primaria"] == "#112233"
    d = _mercado_publicado(admin)
    h = public.get(f"/m/{d['slug']}").text
    assert "--p:#112233" in h and "Georgia" in h and "VirtusX Global" in h and "TI que não para" in h
    assert 'property="og:title"' in h and 'name="consentimento"' in h and "wa.me/34600000000" in h and "<script>" not in h


def test_lead_com_consentimento_origem_e_tarefa(admin, public, marina):
    d = _mercado_publicado(admin)
    page = public.get(f"/m/{d['slug']}?utm_source=google&utm_campaign=teste")
    assert 'name="utm_source" value="google"' in page.text
    post = lambda data: public.post(f"/m/{d['slug']}/lead", data=data, follow_redirects=False)
    r = post({"nome": "Ana", "email": "ana@exemplo.es"})                        # sem consentimento
    assert r.status_code == 303 and "erro=1" in r.headers["location"]
    r = post({"nome": "Ana", "email": "ana@exemplo.es", "consentimento": "1", "utm_source": "google", "utm_campaign": "teste", "mensagem": "Preciso de suporte"})
    assert "obrigado=1" in r.headers["location"]
    assert "Recibimos tu solicitud" in public.get(r.headers["location"].split("#")[0]).text
    post({"nome": "Robo", "email": "x@y.z", "consentimento": "1", "website": "http://spam"})   # isca: descartado em silêncio
    leads = admin.get(f"/api/leads?market_id={d['id']}").json()["data"]
    assert [l["name"] for l in leads] == ["Ana"] and leads[0]["utm_source"] == "google" and leads[0]["consent_at"]
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE related_entity=?", f"lead:{leads[0]['id']}") == 1
    ev = R.db.one("SELECT payload_json AS payload FROM events WHERE type='lead.novo'")
    assert "ana@" not in ev["payload"] and "Ana" not in ev["payload"]        # evento sem dados pessoais
    assert marina.get("/api/leads").status_code == 403                        # parceiro não vê leads
    assert admin.patch(f"/api/leads/{leads[0]['id']}", json={"status": "ganho"}).json()["data"]["status"] == "ganho"
    f = admin.get(f"/api/mercados/{d['id']}").json()["data"]["funil"]
    assert f["visitas"] >= 1 and f["leads"] == 1 and f["ganhos"] == 1 and f["conversao_lead_ganho"] == 100.0
    assert f["origens"][0]["origem"] == "google"


def test_anti_spam_por_endereco_e_site_nao_publicado(admin, public):
    d = _mercado_publicado(admin)
    vitrine._rate.clear()
    for i in range(7):
        public.post(f"/m/{d['slug']}/lead", data={"nome": f"P{i}", "email": f"p{i}@x.com", "consentimento": "1"}, follow_redirects=False)
    assert R.db.val("SELECT COUNT(*) FROM leads") == 5                         # máximo 5 por endereço a cada 10 min
    m2 = admin.post("/api/mercados", json={"country": "France", "brand": "V", "offer": "Suporte de TI remoto para empresas"}).json()["data"]
    assert public.post(f"/m/{m2['slug']}/lead", data={"nome": "x", "email": "a@b.c", "consentimento": "1"}).status_code == 404


def test_visitas_nao_contam_previa(admin, public):
    d = _mercado_publicado(admin)
    for _ in range(3):
        public.get(f"/m/{d['slug']}")
    admin.get(f"/m/{d['slug']}")                                                 # equipe logada vendo: conta como prévia? não (publicado conta)
    assert vitrine.funnel(d["id"])["visitas"] >= 3
