"""Percurso completo da Etapa 1 e cenários de falha exigidos."""
import json

from conftest import CUSTOMER, SHIP_BR, make_store, order
from app.core import R
from app import commerce, db as dbmod
from app.providers import DemoPaymentProvider


def inv(pid, wh="whs_cajamar"):
    return R.db.one("SELECT on_hand, reserved FROM inventory WHERE warehouse_id=? AND product_id=?", wh, pid)


def oid(code):
    return R.db.val("SELECT id FROM orders WHERE code=?", code)


def test_percurso_completo(marina, public, ops, admin):
    # 1. parceiro cria a página
    make_store(marina)
    st = public.get("/api/public/stores/casa-aurora").json()["data"]
    assert st["seller"]["seller_of_record"].startswith("Central Brasil")  # vendedor identificado no checkout
    assert st["seller"]["referral_partner"] == "Casa Aurora (DEMO)"
    assert st["environment"] == "demo"
    # 2. cliente faz pedido de teste
    r = order(public, [{"product_id": "prd_cafe", "qty": 2}])
    assert r.status_code == 201, r.text
    d = r.json()["data"]
    code, token = d["order_code"], d["access_token"]
    # 3. estoque reservado
    assert inv("prd_cafe") == {"on_hand": 40, "reserved": 2}
    # 4. pagamento de teste confirmado (webhook assinado do provedor de demonstração)
    r = public.post(f"/api/public/orders/{code}/pay", json={"token": token, "test_card": "4242 4242 4242 4242"})
    assert r.status_code == 200 and r.json()["data"]["simulated"] is True
    o = public.get(f"/api/public/orders/{code}?token={token}").json()["data"]
    assert o["status"] == "pago" and o["payment"]["status"] == "confirmado"
    assert inv("prd_cafe") == {"on_hand": 38, "reserved": 0}
    # 5. envio registrado
    r = ops.post(f"/api/orders/{oid(code)}/ship")
    assert r.status_code == 200, r.text
    ship = r.json()["data"]
    assert ship["simulated"] is True and ship["tracking_code"].startswith("DEMO-")
    # 6. comissão calculada (10% sobre produtos, sem frete): 2 × 49,90 = 99,80 → 9,98
    assert ship["commission"]["amount_minor"] == 998
    assert ship["commission"]["base_minor"] == 9980
    # 7. o centro reflete: eventos correlacionados e prédio atualizado
    det = admin.get(f"/api/orders/{oid(code)}").json()["data"]
    types = [e["type"] for e in det["timeline"]]
    for t in ["pedido.criado", "estoque.reservado", "pagamento.autorizado", "pagamento.confirmado", "estoque.baixado",
              "pedido.pago", "envio.registrado", "pedido.enviado", "comissao.apurada"]:
        assert t in types, t
    assert all(e["received_at"] and e["occurred_at"] for e in det["timeline"])
    world = admin.get("/api/world").json()["data"]
    aurora = next(p for p in world["partners"] if p["id"] == "prt_aurora")
    assert aurora["storefronts"][0]["published"] == 1
    # parceiro vê o pedido com dados pessoais mascarados
    mo = marina.get("/api/orders").json()["data"][0]
    assert mo["customer_email"] == "c***@example.com"
    assert admin.get(f"/api/orders/{oid(code)}").json()["data"]["customer_email"] == CUSTOMER["email"]
    # evento não carrega dados pessoais
    evs = admin.get("/api/events?after=0&limit=1000").json()["data"]["events"]
    assert CUSTOMER["email"] not in json.dumps(evs)


def test_pagamento_recusado_libera_reserva(marina, public):
    make_store(marina)
    d = order(public, [{"product_id": "prd_caneca", "qty": 3}]).json()["data"]
    assert inv("prd_caneca")["reserved"] == 3
    public.post(f"/api/public/orders/{d['order_code']}/pay", json={"token": d["access_token"], "test_card": "4000000000000002"})
    o = public.get(f"/api/public/orders/{d['order_code']}?token={d['access_token']}").json()["data"]
    assert o["status"] == "pagamento_recusado" and o["payment"]["status"] == "recusado"
    assert inv("prd_caneca") == {"on_hand": 25, "reserved": 0}


def test_cartao_que_nao_e_de_teste_e_recusado(marina, public):
    make_store(marina)
    d = order(public, [{"product_id": "prd_caneca", "qty": 1}]).json()["data"]
    r = public.post(f"/api/public/orders/{d['order_code']}/pay", json={"token": d["access_token"], "test_card": "5555555555554444"})
    assert r.status_code == 400 and r.json()["error"] == "cartao_teste_invalido"


def test_pedido_duplicado_idempotente(marina, public):
    make_store(marina)
    r1 = order(public, [{"product_id": "prd_cafe", "qty": 1}], key="chave-fixa-123")
    r2 = order(public, [{"product_id": "prd_cafe", "qty": 1}], key="chave-fixa-123")
    assert r1.status_code == 201 and r2.status_code == 200
    assert r1.json()["data"]["order_code"] == r2.json()["data"]["order_code"] and r2.json()["data"]["duplicate"]
    assert R.db.val("SELECT COUNT(*) FROM orders") == 1
    assert inv("prd_cafe")["reserved"] == 1
    r3 = order(public, [{"product_id": "prd_cafe", "qty": 5}], key="chave-fixa-123")
    assert r3.status_code == 409 and r3.json()["error"] == "idempotencia_conflito"
    r4 = public.post("/api/public/stores/casa-aurora/checkout", json={"items": [{"product_id": "prd_cafe", "qty": 1}],
                                                                       "customer": CUSTOMER, "shipping": SHIP_BR})
    assert r4.status_code == 400 and r4.json()["error"] == "idempotencia_obrigatoria"


def _paid(public):
    d = order(public, [{"product_id": "prd_cafe", "qty": 1}]).json()["data"]
    public.post(f"/api/public/orders/{d['order_code']}/pay", json={"token": d["access_token"], "test_card": "4242424242424242"})
    return d


def test_webhook_duplicado_nao_reprocessa(marina, public, admin, ops):
    make_store(marina)
    d = _paid(public)
    ops.post(f"/api/orders/{oid(d['order_code'])}/ship")
    captured = R.db.one("SELECT id FROM webhook_inbox WHERE event_type='payment.captured'")["id"]
    r = admin.post("/api/demo/replay-webhook", json={"inbox_id": captured})
    assert r.json()["data"]["status"] == "duplicado"
    assert inv("prd_cafe") == {"on_hand": 39, "reserved": 0}
    assert R.db.val("SELECT COUNT(*) FROM commission_entries") == 1


def test_eventos_fora_de_ordem(marina, public):
    make_store(marina)
    d = order(public, [{"product_id": "prd_cafe", "qty": 1}]).json()["data"]
    p = R.db.one("SELECT * FROM payments")
    data = {"provider_ref": p["provider_ref"], "amount_minor": p["amount_minor"], "currency": "BRL", "fee_minor": 10}
    b1, h1 = DemoPaymentProvider.build_webhook("payment.captured", data, occurred_at="2026-11-09T10:00:02.000Z")
    b2, h2 = DemoPaymentProvider.build_webhook("payment.authorized", data, occurred_at="2026-11-09T10:00:01.000Z")
    assert commerce.receive_webhook("demo", b1, h1)["status"] == "processado"
    assert commerce.receive_webhook("demo", b2, h2)["status"] == "ignorado_fora_ordem"
    assert R.db.val("SELECT status FROM payments") == "confirmado"
    assert R.db.val("SELECT status FROM orders") == "pago"
    # 'falhou' depois de 'confirmado' não regride
    b3, h3 = DemoPaymentProvider.build_webhook("payment.failed", {"provider_ref": p["provider_ref"]})
    assert commerce.receive_webhook("demo", b3, h3)["status"] == "ignorado_fora_ordem"
    assert inv("prd_cafe") == {"on_hand": 39, "reserved": 0}


def test_falta_de_estoque_e_atomica(marina, public):
    make_store(marina)
    before = inv("prd_cafe")
    r = order(public, [{"product_id": "prd_cafe", "qty": 1}, {"product_id": "prd_cha", "qty": 1}])
    assert r.status_code == 409 and r.json()["error"] == "estoque_insuficiente"
    assert R.db.val("SELECT COUNT(*) FROM orders") == 0
    assert inv("prd_cafe") == before  # nada ficou reservado
    assert R.db.val("SELECT COUNT(*) FROM events WHERE type='checkout.sem_estoque'") == 1
    r = order(public, [{"product_id": "prd_vela", "qty": 4}])
    assert r.status_code == 409


def test_cancelamento_libera_reserva_e_pagamento_tardio(marina, public, ops):
    make_store(marina)
    d = order(public, [{"product_id": "prd_caneca", "qty": 2}]).json()["data"]
    assert inv("prd_caneca")["reserved"] == 2
    r = ops.post(f"/api/orders/{oid(d['order_code'])}/cancel", json={"reason": "cliente desistiu"})
    assert r.status_code == 200
    assert r.json()["data"]["reservas_liberadas"] == 1
    assert inv("prd_caneca") == {"on_hand": 25, "reserved": 0}
    assert R.db.val("SELECT status FROM reservations") == "liberada"
    # o provedor confirma depois do cancelamento: pedido NÃO reativa; reembolso é preparado para revisão
    p = R.db.one("SELECT * FROM payments")
    b, h = DemoPaymentProvider.build_webhook("payment.captured", {"provider_ref": p["provider_ref"], "amount_minor": p["amount_minor"],
                                                                  "currency": "BRL", "fee_minor": 5})
    commerce.receive_webhook("demo", b, h)
    assert R.db.val("SELECT status FROM orders") == "cancelado"
    assert inv("prd_caneca") == {"on_hand": 25, "reserved": 0}
    assert R.db.val("SELECT status FROM action_requests WHERE action='reembolso.executar'") == "aguardando_aprovacao"


def test_cancelamento_de_pedido_pago_exige_aprovacao_do_reembolso(marina, public, ops, fin, admin):
    make_store(marina)
    d = _paid(public)
    o = oid(d["order_code"])
    eff = ops.post(f"/api/orders/{o}/cancel", json={"reason": "erro de endereço"}).json()["data"]
    assert eff["estoque_estornado"] == 1 and eff["reembolso"]["modo"] == "revisao"
    assert any("não é desfeito" in x for x in eff["efeitos_externos"])
    assert inv("prd_cafe") == {"on_hand": 40, "reserved": 0}
    aid = eff["reembolso"]["action_request_id"]
    assert ops.post(f"/api/approvals/{aid}/decide", json={"approve": True}).status_code == 403  # sem permissão
    r = fin.post(f"/api/approvals/{aid}/decide", json={"approve": True})
    assert r.status_code == 200, r.text
    assert R.db.val("SELECT status FROM payments") == "reembolsado"
    assert R.db.val("SELECT status FROM refunds") == "executado"
    # cancelar uma ação já executada não a desfaz: informa o que ocorreu e as compensações
    res = fin.post(f"/api/approvals/{aid}/cancel").json()["data"]
    assert res["cancelada"] is False and res["ja_executado"]["executado_externamente"] is True and res["compensacoes_possiveis"]


def test_devolucao_estorna_comissao(marina, public, ops, fin):
    make_store(marina)
    d = _paid(public)
    o = oid(d["order_code"])
    ops.post(f"/api/orders/{o}/ship")
    rid = ops.post(f"/api/orders/{o}/return", json={"reason": "arrependimento"}).json()["data"]["return_id"]
    ref = ops.post(f"/api/returns/{rid}/receive").json()["data"]
    fin.post(f"/api/approvals/{ref['action_request_id']}/decide", json={"approve": True})
    rows = R.db.all("SELECT kind, amount_minor, status FROM commission_entries ORDER BY created_at")
    assert sum(r["amount_minor"] for r in rows) == 0
    assert {r["kind"] for r in rows} == {"apuracao", "estorno"}
    assert R.db.val("SELECT status FROM orders") == "devolvido"


def test_reserva_expira(marina, public, app):
    make_store(marina)
    order(public, [{"product_id": "prd_cafe", "qty": 3}])
    dbmod.advance_clock(minutes=31)
    app.state.tick()
    assert R.db.val("SELECT status FROM orders") == "expirado"
    assert inv("prd_cafe") == {"on_hand": 40, "reserved": 0}


def test_assinatura_invalida(public):
    r = public.post("/api/webhooks/payments/demo", content=b'{"id":"x","type":"payment.captured"}',
                    headers={"x-demo-signature": "t=1,v1=abc"})
    assert r.status_code == 403
    assert R.db.val("SELECT COUNT(*) FROM webhook_inbox") == 0


def test_retentativa_e_fila_de_falhas(marina, public, admin, app):
    make_store(marina)
    commerce.FAIL_INJECTION["count"] = 1
    d = order(public, [{"product_id": "prd_cafe", "qty": 1}]).json()["data"]
    public.post(f"/api/public/orders/{d['order_code']}/pay", json={"token": d["access_token"], "test_card": "4242424242424242"})
    first = R.db.one("SELECT * FROM webhook_inbox WHERE event_type='payment.authorized'")
    assert first["status"] == "falhou" and first["attempts"] == 1
    dbmod.advance_clock(minutes=1)
    commerce.retry_due_webhooks()
    assert R.db.val("SELECT status FROM webhook_inbox WHERE id=?", first["id"]) == "ignorado_fora_ordem"  # captured já chegou
    assert R.db.val("SELECT status FROM orders") == "pago"
    # falha persistente → fila de falhas + pendência crítica
    d2 = order(public, [{"product_id": "prd_caneca", "qty": 1}]).json()["data"]
    commerce.FAIL_INJECTION["count"] = 100
    public.post(f"/api/public/orders/{d2['order_code']}/pay", json={"token": d2["access_token"], "test_card": "4000000000000002"})
    for _ in range(6):
        dbmod.advance_clock(minutes=20)
        commerce.retry_due_webhooks()
    item = R.db.one("SELECT * FROM webhook_inbox WHERE event_type='payment.failed'")
    assert item["status"] == "dead_letter" and item["attempts"] == R.settings.max_webhook_attempts
    assert R.db.val("SELECT COUNT(*) FROM tasks WHERE pendency_type='falha_integracao'") == 1
    commerce.FAIL_INJECTION["count"] = 0
    r = admin.post(f"/api/webhooks/inbox/{item['id']}/requeue")
    assert r.json()["data"]["status"] == "processado"
    assert R.db.val("SELECT status FROM orders WHERE code=?", d2["order_code"]) == "pagamento_recusado"


def test_recuperacao_apos_desconexao(marina, public, admin):
    make_store(marina)
    last = admin.get("/api/events?after=0&limit=1000").json()["data"]["last_id"]
    _paid(public)  # acontece enquanto o painel está "desconectado"
    missed = admin.get(f"/api/events?after={last}").json()["data"]["events"]
    ids = [e["id"] for e in missed]
    assert ids == sorted(ids) and len(ids) >= 5
    assert {"pedido.criado", "pedido.pago"} <= {e["type"] for e in missed}


def test_regiao_bloqueada_e_b2b(admin, public):
    # EUA: vendas não liberadas automaticamente
    R.db.connect().execute("INSERT INTO partner_products VALUES ('prt_sul','prd_cafe',1) ON CONFLICT DO NOTHING")
    r = admin.post("/api/partners/prt_austin/storefronts", json={"slug": "austin-goods", "title": "Austin", "product_ids": ["prd_cafe"]})
    admin.patch(f"/api/storefronts/{r.json()['data']['id']}", json={"published": True})
    r = order(public, [{"product_id": "prd_cafe", "qty": 1}], slug="austin-goods",
              ship={"country": "US", "city": "Austin", "address": "1 Main", "postal_code": "78701"})
    assert r.status_code == 409 and r.json()["error"] == "regiao_bloqueada"
    # revenda B2B: preparada no modelo, não operacional
    r = admin.post("/api/partners/prt_sul/storefronts", json={"slug": "revenda-sul", "title": "Sul", "product_ids": ["prd_cafe"]})
    admin.patch(f"/api/storefronts/{r.json()['data']['id']}", json={"published": True})
    r = order(public, [{"product_id": "prd_cafe", "qty": 1}], slug="revenda-sul")
    assert r.status_code == 409 and r.json()["error"] == "modalidade_nao_operacional"


def test_produto_nao_autorizado(marina):
    r = marina.post("/api/partners/prt_aurora/storefronts", json={"slug": "teste-x", "title": "X", "product_ids": ["prd_inexistente"]})
    assert r.status_code == 403


def test_provedor_demo_recusado_fora_do_demo(app):
    from app.providers import IntegrationPending, payment_provider
    old = R.settings.env
    try:
        R.settings.env = "production"
        try:
            payment_provider({"code": "BR", "payment_provider": "stripe"})
            assert False
        except IntegrationPending as e:
            assert "Stripe" in e.message
    finally:
        R.settings.env = old


def test_comissao_deterministica():
    assert commerce.commission_amount(9980, 1000) == 998
    assert commerce.commission_amount(1005, 1000) == 101  # 100,5 → 101 (meio para cima)
    assert commerce.commission_amount(1004, 1000) == 100
    assert commerce.commission_amount(0, 1200) == 0
