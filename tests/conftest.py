import sys, uuid, pytest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from starlette.testclient import TestClient
from app import db as dbmod
from app.main import create_app
from app import commerce

@pytest.fixture()
def app(tmp_path):
    dbmod.reset_clock()
    commerce.FAIL_INJECTION["count"] = 0
    a = create_app(data_dir=tmp_path, scheduler_enabled=False, demo_webhook_delay_s=0)
    yield a
    dbmod.reset_clock()

def _client(app, email):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"email": email, "password": "demo1234"})
    assert r.status_code == 200, r.text
    c.headers.update({"X-Requested-With": "plataforma"})
    return c

@pytest.fixture()
def admin(app): return _client(app, "admin@central.demo")
@pytest.fixture()
def ops(app): return _client(app, "ops@central.demo")
@pytest.fixture()
def fin(app): return _client(app, "financeiro@central.demo")
@pytest.fixture()
def marina(app): return _client(app, "marina@aurora.demo")
@pytest.fixture()
def joao(app): return _client(app, "joao@lisboa.demo")
@pytest.fixture()
def public(app): return TestClient(app)

CUSTOMER = {"name": "Cliente Teste", "email": "cliente@example.com"}
SHIP_BR = {"country": "BR", "state": "SP", "city": "São Paulo", "address": "Rua Teste 1", "postal_code": "01000-000"}

def make_store(marina, products=("prd_cafe", "prd_caneca", "prd_cha", "prd_vela")):
    r = marina.post("/api/partners/prt_aurora/storefronts", json={"slug": "casa-aurora", "title": "Casa Aurora",
                    "template": "vitrine-classica", "config": {"headline": "Feito com calma"}, "product_ids": list(products)})
    assert r.status_code == 200, r.text
    sid = r.json()["data"]["id"]
    r = marina.patch(f"/api/storefronts/{sid}", json={"published": True})
    assert r.status_code == 200, r.text
    return sid

def order(public, items, key=None, slug="casa-aurora", ship=SHIP_BR):
    return public.post(f"/api/public/stores/{slug}/checkout", json={"items": items, "customer": CUSTOMER, "shipping": ship},
                       headers={"Idempotency-Key": key or uuid.uuid4().hex})
