"""Fluxo de venda central com comissão: vitrine → pedido → reserva → pagamento → envio → comissão.

A modalidade de revenda B2B ('b2b_resale') existe no modelo de dados, mas o checkout a recusa
explicitamente nesta etapa: faturamento, comissão e obrigações dos dois modelos não se misturam.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from .core import (R, AppError, Conflict, Forbidden, NotFound, Principal, SYSTEM, audit, emit, hash_token,
                   mask_email, mask_name, new_token, sign_payload)
from .db import dumps, iso, loads, new_id, now, parse_iso
from . import policies, providers

ORDER_STATES = ("aguardando_pagamento", "pago", "enviado", "pagamento_recusado", "cancelado", "expirado", "devolvido")
PAY_RANK = {"pendente": 0, "autorizado": 1, "confirmado": 2, "recusado": 2, "reembolsado": 3, "cancelado": 2}
EVENT_TO_STATUS = {"payment.authorized": "autorizado", "payment.captured": "confirmado",
                   "payment.failed": "recusado", "refund.succeeded": "reembolsado"}
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,48}[a-z0-9])$")
TEMPLATES = {
    "vitrine-classica": {"headline": "Seleção oficial", "accent": "#0f766e", "layout": "grade", "about": ""},
    "vitrine-editorial": {"headline": "Curadoria da casa", "accent": "#7c3aed", "layout": "lista", "about": ""},
}


# ------------------------------------------------------------------ vitrines
def create_storefront(actor: Principal, partner_id, slug, title, template="vitrine-classica", config=None, product_ids=()):
    actor.require("storefronts.write", partner_id)
    slug = (slug or "").strip().lower()
    if not SLUG_RE.match(slug):
        raise AppError("Endereço inválido: use 3–50 caracteres minúsculos, números e hífen.", "slug_invalido")
    if template not in TEMPLATES:
        raise AppError("Template inexistente")
    cfg = {**TEMPLATES[template], **{k: v for k, v in (config or {}).items() if k in ("headline", "accent", "about", "layout")}}
    if not re.match(r"^#[0-9a-fA-F]{6}$", cfg.get("accent", "")):
        raise AppError("Cor de destaque inválida")
    with R.db.tx() as c:
        p = c.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
        if not p:
            raise NotFound("Parceiro não encontrado")
        if c.execute("SELECT 1 FROM storefronts WHERE slug=?", (slug,)).fetchone():
            raise Conflict("Endereço já utilizado", "slug_em_uso")
        sid = new_id("vit")
        ts = iso()
        c.execute("INSERT INTO storefronts(id,partner_id,slug,title,template,config_json,published,created_at,updated_at,is_demo)"
                  " VALUES (?,?,?,?,?,?,0,?,?,?)", (sid, partner_id, slug, title.strip()[:120], template, dumps(cfg), ts, ts,
                                                   1 if R.settings.is_demo else 0))
        _set_products(c, sid, partner_id, product_ids)
        audit(c, actor, "vitrine.criada", "storefront", sid, None, {"slug": slug, "template": template}, partner_id)
        emit(c, "vitrine.criada", "vitrines", "storefront", sid, partner_id, p["region_code"], payload={"slug": slug})
    return sid


def _set_products(c, sid, partner_id, product_ids):
    c.execute("DELETE FROM storefront_products WHERE storefront_id=?", (sid,))
    for i, pid in enumerate(product_ids or ()):
        ok = c.execute("SELECT 1 FROM partner_products WHERE partner_id=? AND product_id=? AND authorized=1",
                       (partner_id, pid)).fetchone()
        if not ok:
            raise Forbidden(f"Produto não autorizado para este parceiro: {pid}", "produto_nao_autorizado")
        c.execute("INSERT INTO storefront_products(storefront_id,product_id,position) VALUES (?,?,?)", (sid, pid, i))


def update_storefront(actor: Principal, sid, title=None, config=None, product_ids=None, published=None):
    with R.db.tx() as c:
        s = c.execute("SELECT * FROM storefronts WHERE id=?", (sid,)).fetchone()
        if not s:
            raise NotFound("Vitrine não encontrada")
        s = dict(s)
        actor.require("storefronts.write", s["partner_id"])
        cfg = loads(s["config_json"])
        if config:
            cfg.update({k: v for k, v in config.items() if k in ("headline", "accent", "about", "layout")})
            if not re.match(r"^#[0-9a-fA-F]{6}$", cfg.get("accent", "")):
                raise AppError("Cor de destaque inválida")
        if product_ids is not None:
            _set_products(c, sid, s["partner_id"], product_ids)
        if published:
            n = c.execute("SELECT COUNT(*) FROM storefront_products WHERE storefront_id=?", (sid,)).fetchone()[0]
            if n == 0:
                raise AppError("Selecione ao menos um produto antes de publicar", "vitrine_vazia")
        c.execute("UPDATE storefronts SET title=COALESCE(?,title), config_json=?, published=COALESCE(?,published), updated_at=? WHERE id=?",
                  (title, dumps(cfg), None if published is None else int(bool(published)), iso(), sid))
        audit(c, actor, "vitrine.atualizada", "storefront", sid, {"published": s["published"]},
              {"published": published, "title": title}, s["partner_id"])
        emit(c, "vitrine.publicada" if published else "vitrine.atualizada", "vitrines", "storefront", sid, s["partner_id"])


def public_storefront(slug):
    s = R.db.one("SELECT s.*, p.name partner_name, p.region_code, p.commercial_model, p.status partner_status "
                 "FROM storefronts s JOIN partners p ON p.id=s.partner_id WHERE s.slug=?", slug)
    if not s or not s["published"]:
        raise NotFound("Loja não encontrada")
    region = R.db.one("SELECT * FROM regions WHERE code=?", s["region_code"])
    prods = R.db.all(
        "SELECT pr.id, pr.sku, COALESCE(t.name, pr.name) name, COALESCE(t.description, pr.description) description, "
        "rp.price_minor, ? currency FROM storefront_products sp JOIN products pr ON pr.id=sp.product_id "
        "JOIN product_regions rp ON rp.product_id=pr.id AND rp.region_code=? AND rp.active=1 "
        "LEFT JOIN product_translations t ON t.product_id=pr.id AND t.locale=? "
        "WHERE sp.storefront_id=? AND pr.status='ativo' ORDER BY sp.position",
        region["currency"], region["code"], region["locale"], s["id"])
    for p in prods:
        p["available"] = available_qty(p["id"], region["code"]) > 0
    cfg = loads(s["config_json"])
    return {
        "slug": s["slug"], "title": s["title"], "config": cfg, "partner_name": s["partner_name"],
        "region": {"code": region["code"], "name": region["name"], "currency": region["currency"], "locale": region["locale"],
                   "sales_enabled": bool(region["sales_enabled"]), "sales_status_note": region["sales_status_note"]},
        # Identificação obrigatória do vendedor no checkout (venda central com comissão)
        "seller": {"seller_of_record": region["seller_entity"], "referral_partner": s["partner_name"],
                   "model": s["commercial_model"]},
        "products": prods, "environment": R.settings.env,
        "shipping_minor": (loads(region["config_json"]) or {}).get("flat_shipping_minor", 0),
        "test_cards": list(providers.TEST_CARDS.items()) if R.settings.is_demo else [],
    }


def available_qty(product_id, region_code):
    return R.db.val("SELECT COALESCE(SUM(i.on_hand - i.reserved),0) FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id "
                    "WHERE i.product_id=? AND w.region_code=?", product_id, region_code) or 0


# ------------------------------------------------------------------ checkout
def checkout(slug, items, customer, shipping, idempotency_key):
    if not idempotency_key or len(idempotency_key) < 8:
        raise AppError("Cabeçalho Idempotency-Key obrigatório", "idempotencia_obrigatoria")
    req_hash = hashlib.sha256(dumps({"slug": slug, "items": items, "customer": customer, "shipping": shipping}).encode()).hexdigest()
    prev = R.db.one("SELECT * FROM idempotency_keys WHERE key=?", f"checkout:{idempotency_key}")
    if prev:
        data = loads(prev["response_json"])
        if data["request_hash"] != req_hash:
            raise Conflict("Mesma chave de idempotência com conteúdo diferente", "idempotencia_conflito")
        o = R.db.one("SELECT code, status FROM orders WHERE id=?", data["order_id"])
        return {"order_code": o["code"], "status": o["status"], "duplicate": True, "access_token": None}

    s = R.db.one("SELECT s.*, p.region_code, p.commercial_model, p.status pstatus, p.name pname FROM storefronts s "
                 "JOIN partners p ON p.id=s.partner_id WHERE s.slug=?", slug)
    if not s or not s["published"]:
        raise NotFound("Loja não encontrada")
    if s["pstatus"] != "ativo":
        raise Conflict("Parceiro inativo", "parceiro_inativo")
    if s["commercial_model"] != "central_commission":
        raise Conflict("Modalidade de revenda B2B ainda não operacional nesta etapa.", "modalidade_nao_operacional")
    region = R.db.one("SELECT * FROM regions WHERE code=?", s["region_code"])
    if not region["sales_enabled"]:
        raise Conflict(f"Vendas não liberadas para {region['name']}: {region['sales_status_note'] or ''}", "regiao_bloqueada")
    if (shipping or {}).get("country") != region["code"]:
        raise Conflict("Entrega disponível apenas no país da loja nesta etapa.", "pais_entrega")
    name = (customer or {}).get("name", "").strip()
    email = (customer or {}).get("email", "").strip()
    if not name or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise AppError("Nome e e-mail válidos são obrigatórios", "cliente_invalido")
    for k in ("address", "city", "postal_code"):
        if not (shipping or {}).get(k):
            raise AppError("Endereço de entrega incompleto", "endereco_incompleto")
    if not items:
        raise AppError("Carrinho vazio")
    merged = {}
    for it in items:
        q = int(it.get("qty", 0))
        if q <= 0 or q > 20:
            raise AppError("Quantidade inválida")
        merged[it["product_id"]] = merged.get(it["product_id"], 0) + q

    provider = providers.payment_provider(region)
    cfg = loads(region["config_json"]) or {}
    oid, code, token = new_id("ped"), "P" + new_id("x")[2:12].upper(), new_token()
    corr = new_id("cor")
    ts = iso()
    try:
        with R.db.tx() as c:
            lines, subtotal = [], 0
            for pid, q in merged.items():
                row = c.execute(
                    "SELECT pr.id, pr.sku, pr.name, rp.price_minor FROM storefront_products sp JOIN products pr ON pr.id=sp.product_id "
                    "JOIN product_regions rp ON rp.product_id=pr.id AND rp.region_code=? AND rp.active=1 "
                    "JOIN partner_products pp ON pp.product_id=pr.id AND pp.partner_id=? AND pp.authorized=1 "
                    "WHERE sp.storefront_id=? AND pr.id=? AND pr.status='ativo'",
                    (region["code"], s["partner_id"], s["id"], pid)).fetchone()
                if not row:
                    raise AppError("Produto indisponível nesta loja", "produto_indisponivel")
                lines.append((row, q))
                subtotal += row["price_minor"] * q
            ship = cfg.get("flat_shipping_minor", 0)
            c.execute(
                "INSERT INTO orders(id,code,access_token_hash,region_code,storefront_id,partner_id,unit_id,sales_model,seller_of_record,"
                "status,currency,subtotal_minor,shipping_minor,total_minor,customer_name,customer_email,shipping_json,idempotency_key,"
                "correlation_id,created_at,updated_at,is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (oid, code, hash_token(token), region["code"], s["id"], s["partner_id"],
                 R.db.val("SELECT id FROM partner_units WHERE partner_id=? ORDER BY name LIMIT 1", s["partner_id"]),
                 "central_commission", region["seller_entity"], "aguardando_pagamento", region["currency"], subtotal, ship,
                 subtotal + ship, name[:120], email[:200], dumps(shipping), idempotency_key, corr, ts, ts,
                 1 if R.settings.is_demo else 0))
            expires = iso(now() + timedelta(minutes=R.settings.reservation_ttl_min))
            for row, q in lines:
                c.execute("INSERT INTO order_items(id,order_id,product_id,sku,name,qty,unit_price_minor,line_total_minor) VALUES (?,?,?,?,?,?,?,?)",
                          (new_id("itm"), oid, row["id"], row["sku"], row["name"], q, row["price_minor"], row["price_minor"] * q))
                _reserve(c, oid, row["id"], q, region["code"], expires, corr, s["partner_id"])
            ref = provider.create_intent(oid, subtotal + ship, region["currency"])
            c.execute("INSERT INTO payments(id,order_id,provider,provider_ref,status,amount_minor,currency,is_simulated,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?)", (new_id("pag"), oid, provider.code, ref, "pendente", subtotal + ship,
                                                        region["currency"], 1 if provider.simulated else 0, ts, ts))
            c.execute("INSERT INTO idempotency_keys(key,scope,response_json,created_at) VALUES (?,?,?,?)",
                      (f"checkout:{idempotency_key}", "checkout", dumps({"order_id": oid, "request_hash": req_hash}), ts))
            audit(c, Principal("public", "checkout"), "pedido.criado", "order", oid, None,
                  {"code": code, "total_minor": subtotal + ship, "currency": region["currency"]}, s["partner_id"], corr)
            emit(c, "pedido.criado", "checkout", "order", oid, s["partner_id"], region["code"], corr, "aguardando_pagamento",
                 payload={"code": code, "total_minor": subtotal + ship, "currency": region["currency"], "items": len(lines)})
    except Conflict as e:
        if e.code == "estoque_insuficiente":
            with R.db.tx() as c:
                emit(c, "checkout.sem_estoque", "checkout", "storefront", s["id"], s["partner_id"], region["code"], corr,
                     "recusado", payload=e.extra)
        raise
    return {"order_code": code, "status": "aguardando_pagamento", "access_token": token, "duplicate": False}


def _reserve(c, order_id, product_id, qty, region_code, expires, corr, partner_id):
    whs = c.execute("SELECT i.warehouse_id FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id "
                    "WHERE w.region_code=? AND i.product_id=? AND i.on_hand - i.reserved >= ? ORDER BY (i.on_hand-i.reserved) DESC",
                    (region_code, product_id, qty)).fetchall()
    for (wid,) in whs:
        cur = c.execute("UPDATE inventory SET reserved = reserved + ?, updated_at=? WHERE warehouse_id=? AND product_id=? "
                        "AND on_hand - reserved >= ?", (qty, iso(), wid, product_id, qty))
        if cur.rowcount == 1:
            rid = new_id("res")
            c.execute("INSERT INTO reservations(id,order_id,warehouse_id,product_id,qty,status,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?)",
                      (rid, order_id, wid, product_id, qty, "ativa", expires, iso()))
            c.execute("INSERT INTO inventory_movements(id,warehouse_id,product_id,kind,qty,order_id,reservation_id,at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("mov"), wid, product_id, "reserva", qty, order_id, rid, iso(), "checkout"))
            emit(c, "estoque.reservado", "estoque", "reservation", rid, partner_id, region_code, corr, "ativa",
                 payload={"product_id": product_id, "qty": qty, "warehouse_id": wid, "order_id": order_id})
            return rid
    raise Conflict("Estoque insuficiente", "estoque_insuficiente", extra={"product_id": product_id, "qty": qty})


def _release_reservations(c, order_id, reason, corr, partner_id, region):
    rows = c.execute("SELECT * FROM reservations WHERE order_id=? AND status='ativa'", (order_id,)).fetchall()
    for r in rows:
        c.execute("UPDATE inventory SET reserved = reserved - ?, updated_at=? WHERE warehouse_id=? AND product_id=?",
                  (r["qty"], iso(), r["warehouse_id"], r["product_id"]))
        c.execute("UPDATE reservations SET status='liberada', closed_at=?, close_reason=? WHERE id=?", (iso(), reason, r["id"]))
        c.execute("INSERT INTO inventory_movements(id,warehouse_id,product_id,kind,qty,order_id,reservation_id,at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                  (new_id("mov"), r["warehouse_id"], r["product_id"], "liberacao", r["qty"], order_id, r["id"], iso(), reason))
        emit(c, "estoque.liberado", "estoque", "reservation", r["id"], partner_id, region, corr, "liberada",
             payload={"product_id": r["product_id"], "qty": r["qty"], "motivo": reason})
    return len(rows)


def _consume_reservations(c, order_id, corr, partner_id, region):
    rows = c.execute("SELECT * FROM reservations WHERE order_id=? AND status='ativa'", (order_id,)).fetchall()
    for r in rows:
        c.execute("UPDATE inventory SET reserved = reserved - ?, on_hand = on_hand - ?, updated_at=? WHERE warehouse_id=? AND product_id=?",
                  (r["qty"], r["qty"], iso(), r["warehouse_id"], r["product_id"]))
        c.execute("UPDATE reservations SET status='consumida', closed_at=?, close_reason='pagamento_confirmado' WHERE id=?", (iso(), r["id"]))
        c.execute("INSERT INTO inventory_movements(id,warehouse_id,product_id,kind,qty,order_id,reservation_id,at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                  (new_id("mov"), r["warehouse_id"], r["product_id"], "baixa_venda", -r["qty"], order_id, r["id"], iso(), "pagamento"))
        emit(c, "estoque.baixado", "estoque", "reservation", r["id"], partner_id, region, corr, "consumida",
             payload={"product_id": r["product_id"], "qty": r["qty"]})
    return len(rows)


def _order_by_code(code, token):
    o = R.db.one("SELECT * FROM orders WHERE code=?", code)
    if not o or not token or hash_token(token) != o["access_token_hash"]:
        raise NotFound("Pedido não encontrado")
    return o


def pay(code, token, test_card):
    o = _order_by_code(code, token)
    if o["status"] != "aguardando_pagamento":
        raise Conflict(f"Pedido em '{o['status']}' não aceita pagamento", "estado_invalido")
    p = R.db.one("SELECT * FROM payments WHERE order_id=? ORDER BY created_at DESC LIMIT 1", o["id"])
    if p["status"] not in ("pendente",):
        raise Conflict("Pagamento já em processamento", "pagamento_em_processamento")
    region = R.db.one("SELECT * FROM regions WHERE code=?", o["region_code"])
    prov = providers.payment_provider(region)
    with R.db.tx() as c:
        emit(c, "pagamento.enviado_ao_provedor", "checkout", "payment", p["id"], o["partner_id"], o["region_code"],
             o["correlation_id"], "pendente", payload={"simulado": bool(p["is_simulated"])})
    outcome = prov.confirm(p["provider_ref"], p["amount_minor"], p["currency"], (test_card or "").replace(" ", ""))
    return {"submitted": True, "awaiting_webhook": True, "simulated": bool(p["is_simulated"]), "expected": outcome}


def public_order(code, token):
    o = _order_by_code(code, token)
    p = R.db.one("SELECT status, is_simulated FROM payments WHERE order_id=? ORDER BY created_at DESC LIMIT 1", o["id"])
    sh = R.db.one("SELECT tracking_code, status, is_simulated FROM shipments WHERE order_id=?", o["id"])
    return {"code": o["code"], "status": o["status"], "total_minor": o["total_minor"], "currency": o["currency"],
            "seller_of_record": o["seller_of_record"], "payment": p, "shipment": sh,
            "items": R.db.all("SELECT name, qty, unit_price_minor, line_total_minor FROM order_items WHERE order_id=?", o["id"]),
            "environment": R.settings.env}


# ------------------------------------------------------------------ webhooks
def receive_webhook(provider_code, body: bytes, headers: dict):
    headers = {k.lower(): v for k, v in headers.items()}
    if provider_code != "demo":
        raise providers.IntegrationPending(provider_code, "Webhook ainda não habilitado.")
    if not R.settings.is_demo:
        raise Forbidden("Webhook de demonstração recusado fora do ambiente demo", "ambiente")
    sig = headers.get("x-demo-signature", "")
    try:
        parts = dict(p.split("=", 1) for p in sig.split(","))
        ts = int(parts["t"])
        ok = sign_payload(R.settings.webhook_secret_demo, ts, body) == parts["v1"]
        import time
        fresh = abs(time.time() - ts) <= R.settings.webhook_tolerance_s
    except Exception:
        ok, fresh = False, False
    if not ok or not fresh:
        with R.db.tx() as c:
            emit(c, "webhook.rejeitado", provider_code, status="assinatura_invalida" if not ok else "expirado")
        raise Forbidden("Assinatura de webhook inválida ou expirada", "webhook_invalido")
    try:
        ev = json.loads(body)
        ev_id, ev_type, occurred = ev["id"], ev["type"], ev.get("occurred_at")
    except Exception:
        raise AppError("Payload inválido", "payload_invalido")
    with R.db.tx() as c:
        exists = c.execute("SELECT id, status FROM webhook_inbox WHERE provider=? AND external_event_id=?",
                           (provider_code, ev_id)).fetchone()
        if exists:
            emit(c, "webhook.duplicado", provider_code, "webhook", exists[0], status="duplicado", occurred_at=occurred,
                 payload={"type": ev_type})
            return {"status": "duplicado", "inbox_id": exists[0]}
        iid = new_id("whk")
        c.execute("INSERT INTO webhook_inbox(id,provider,external_event_id,event_type,payload,occurred_at,received_at,status,attempts)"
                  " VALUES (?,?,?,?,?,?,?,?,0)", (iid, provider_code, ev_id, ev_type, body.decode(), occurred, iso(), "recebido"))
    return {"status": process_inbox(iid), "inbox_id": iid}


def process_inbox(iid):
    item = R.db.one("SELECT * FROM webhook_inbox WHERE id=?", iid)
    if item["status"] in ("processado", "ignorado_fora_ordem", "dead_letter"):
        return item["status"]
    try:
        with R.db.tx() as c:
            result = _apply_payment_event(c, item)
            c.execute("UPDATE webhook_inbox SET status=?, processed_at=?, attempts=attempts+1, last_error=NULL WHERE id=?",
                      (result, iso(), iid))
        return result
    except Exception as e:  # registra falha, agenda retentativa limitada e, ao esgotar, envia para fila de falhas
        attempts = item["attempts"] + 1
        dead = attempts >= R.settings.max_webhook_attempts
        with R.db.tx() as c:
            c.execute("UPDATE webhook_inbox SET status=?, attempts=?, last_error=?, next_attempt_at=? WHERE id=?",
                      ("dead_letter" if dead else "falhou", attempts, str(e)[:500],
                       None if dead else iso(now() + timedelta(seconds=min(2 ** attempts * 5, 900))), iid))
            emit(c, "webhook.falhou" if not dead else "webhook.fila_falhas", item["provider"], "webhook", iid,
                 status="dead_letter" if dead else "falhou", payload={"type": item["event_type"], "tentativas": attempts})
            if dead:
                from . import tasks
                tasks.create_task(c, SYSTEM, {"title": f"Webhook na fila de falhas: {item['event_type']}",
                                              "description": f"Entrada {iid}: {str(e)[:300]}", "kind": "pendencia",
                                              "pendency_type": "falha_integracao", "priority": "critica", "source": "webhooks",
                                              "related_entity": f"webhook:{iid}"})
        return "dead_letter" if dead else "falhou"


def retry_due_webhooks():
    due = R.db.all("SELECT id FROM webhook_inbox WHERE status='falhou' AND next_attempt_at <= ?", iso())
    return [process_inbox(d["id"]) for d in due]


def requeue_dead_letter(actor: Principal, iid):
    actor.require("webhooks.requeue")
    with R.db.tx() as c:
        cur = c.execute("UPDATE webhook_inbox SET status='falhou', attempts=0, next_attempt_at=? WHERE id=? AND status='dead_letter'",
                        (iso(), iid))
        if cur.rowcount != 1:
            raise Conflict("Entrada não está na fila de falhas")
        audit(c, actor, "webhook.reenfileirado", "webhook", iid)
    return process_inbox(iid)


FAIL_INJECTION = {"count": 0}  # usado apenas em testes para exercitar retentativas


def _apply_payment_event(c, item):
    if FAIL_INJECTION["count"] > 0:
        FAIL_INJECTION["count"] -= 1
        raise RuntimeError("falha transitória injetada (teste)")
    ev = json.loads(item["payload"])
    data = ev.get("data", {})
    new_status = EVENT_TO_STATUS.get(item["event_type"])
    if not new_status:
        return "ignorado_tipo_desconhecido"
    p = c.execute("SELECT * FROM payments WHERE provider_ref=?", (data.get("provider_ref"),)).fetchone()
    if not p:
        raise RuntimeError("Pagamento desconhecido para provider_ref")
    p = dict(p)
    o = dict(c.execute("SELECT * FROM orders WHERE id=?", (p["order_id"],)).fetchone())
    corr, pid, reg = o["correlation_id"], o["partner_id"], o["region_code"]
    occurred = item["occurred_at"] or iso()

    if new_status == "reembolsado":
        return _apply_refund(c, p, o, data, occurred)

    cur_rank, new_rank = PAY_RANK[p["status"]], PAY_RANK[new_status]
    if new_rank <= cur_rank and p["status"] != "cancelado":
        emit(c, "pagamento.evento_fora_de_ordem", "demo_pagamentos", "payment", p["id"], pid, reg, corr, "ignorado",
             occurred_at=occurred, payload={"evento": item["event_type"], "status_atual": p["status"]})
        return "ignorado_fora_ordem"

    if new_status == "confirmado" and data.get("amount_minor") != p["amount_minor"]:
        from . import tasks
        tasks.create_task(c, SYSTEM, {"title": f"Divergência de valor no pagamento do pedido {o['code']}", "partner_id": pid,
                                      "kind": "pendencia", "pendency_type": "divergencia_pagamento", "priority": "critica",
                                      "source": "webhooks", "related_entity": f"order:{o['id']}",
                                      "description": f"Esperado {p['amount_minor']}, recebido {data.get('amount_minor')}."})
        emit(c, "pagamento.divergencia", "demo_pagamentos", "payment", p["id"], pid, reg, corr, "divergente", occurred_at=occurred)
        return "processado"

    c.execute("UPDATE payments SET status=?, fee_minor=COALESCE(?,fee_minor), last_event_occurred_at=?, updated_at=? WHERE id=?",
              (new_status, data.get("fee_minor"), occurred, iso(), p["id"]))
    emit(c, f"pagamento.{new_status}", "demo_pagamentos", "payment", p["id"], pid, reg, corr, new_status, occurred_at=occurred,
         payload={"order_code": o["code"], "simulado": bool(p["is_simulated"])})

    if new_status == "confirmado":
        if o["status"] == "aguardando_pagamento":
            n = _consume_reservations(c, o["id"], corr, pid, reg)
            _set_order_status(c, o, "pago", SYSTEM)
            if n == 0:
                raise RuntimeError("Pedido sem reserva ativa ao confirmar pagamento")
        else:
            # Pagamento confirmado para pedido cancelado/expirado/recusado: não reativa o pedido; prepara reembolso.
            from . import tasks
            tasks.create_task(c, SYSTEM, {"title": f"Pagamento confirmado após cancelamento — pedido {o['code']}",
                                          "partner_id": pid, "kind": "pendencia", "pendency_type": "divergencia_pagamento",
                                          "priority": "alta", "source": "webhooks", "related_entity": f"order:{o['id']}"})
            _request_refund(c, o, p, "pagamento_apos_cancelamento", SYSTEM)
    elif new_status == "recusado":
        if o["status"] == "aguardando_pagamento":
            _release_reservations(c, o["id"], "pagamento_recusado", corr, pid, reg)
            _set_order_status(c, o, "pagamento_recusado", SYSTEM)
    return "processado"


def _set_order_status(c, o, status, actor, reason=None):
    c.execute("UPDATE orders SET status=?, cancel_reason=COALESCE(?,cancel_reason), updated_at=? WHERE id=?",
              (status, reason, iso(), o["id"]))
    audit(c, actor, "pedido.status", "order", o["id"], {"status": o["status"]}, {"status": status}, o["partner_id"], o["correlation_id"])
    emit(c, f"pedido.{status}", "pedidos", "order", o["id"], o["partner_id"], o["region_code"], o["correlation_id"], status,
         payload={"code": o["code"], "total_minor": o["total_minor"], "currency": o["currency"]})
    o["status"] = status


# ------------------------------------------------------------------ operação de pedidos
def register_shipment(actor: Principal, order_id):
    actor.require("shipments.create")
    o = R.db.one("SELECT * FROM orders WHERE id=?", order_id)
    if not o:
        raise NotFound("Pedido não encontrado")
    region = R.db.one("SELECT * FROM regions WHERE code=?", o["region_code"])
    prov = providers.logistics_provider(region)
    with R.db.tx() as c:
        o = dict(c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone())
        if o["status"] != "pago":
            raise Conflict(f"Envio exige pedido pago (atual: {o['status']})", "estado_invalido")
        tracking = prov.register_shipment(o)
        sid = new_id("env")
        c.execute("INSERT INTO shipments(id,order_id,provider,tracking_code,status,is_simulated,registered_at,registered_by) VALUES (?,?,?,?,?,?,?,?)",
                  (sid, o["id"], prov.code, tracking, "registrado", 1 if prov.simulated else 0, iso(), actor.label))
        emit(c, "envio.registrado", prov.code, "shipment", sid, o["partner_id"], o["region_code"], o["correlation_id"], "registrado",
             payload={"order_code": o["code"], "simulado": prov.simulated})
        _set_order_status(c, o, "enviado", actor)
        commission = _accrue_commission(c, o, region)
    return {"shipment_id": sid, "tracking_code": tracking, "simulated": prov.simulated, "commission": commission}


def commission_amount(base_minor: int, rate_bp: int) -> int:
    """Cálculo determinístico: arredondamento comercial (meio para cima) em unidades mínimas."""
    if base_minor < 0 or rate_bp < 0:
        raise ValueError("valores negativos")
    return (base_minor * rate_bp + 5000) // 10000


def _accrue_commission(c, o, region):
    p = dict(c.execute("SELECT * FROM partners WHERE id=?", (o["partner_id"],)).fetchone())
    if p["commercial_model"] != "central_commission" or o["sales_model"] != "central_commission":
        return None
    if c.execute("SELECT 1 FROM commission_entries WHERE order_id=? AND kind='apuracao'", (o["id"],)).fetchone():
        return None
    base = o["subtotal_minor"]  # decisão registrada: comissão sobre produtos, sem frete
    amount = commission_amount(base, p["commission_rate_bp"])
    cid = new_id("com")
    release = iso(now() + timedelta(days=region["return_window_days"]))
    c.execute("INSERT INTO commission_entries(id,partner_id,order_id,kind,base_minor,rate_bp,amount_minor,currency,status,release_at,created_at)"
              " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (cid, p["id"], o["id"], "apuracao", base, p["commission_rate_bp"], amount,
                                                 o["currency"], "em_carencia", release, iso()))
    audit(c, SYSTEM, "comissao.apurada", "commission", cid, None, {"amount_minor": amount, "rate_bp": p["commission_rate_bp"]},
          p["id"], o["correlation_id"])
    emit(c, "comissao.apurada", "comissoes", "commission", cid, p["id"], o["region_code"], o["correlation_id"], "em_carencia",
         payload={"order_code": o["code"], "amount_minor": amount, "currency": o["currency"], "rate_bp": p["commission_rate_bp"]})
    return {"id": cid, "amount_minor": amount, "base_minor": base, "rate_bp": p["commission_rate_bp"], "release_at": release}


def release_commissions():
    with R.db.tx() as c:
        rows = c.execute("SELECT * FROM commission_entries WHERE kind='apuracao' AND status='em_carencia' AND release_at <= ?",
                         (iso(),)).fetchall()
        for r in rows:
            c.execute("UPDATE commission_entries SET status='liberada' WHERE id=?", (r["id"],))
            emit(c, "comissao.liberada", "comissoes", "commission", r["id"], r["partner_id"], status="liberada",
                 payload={"amount_minor": r["amount_minor"]})
    return len(rows)


def cancel_order(actor: Principal, order_id, reason):
    actor.require("orders.cancel")
    with R.db.tx() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            raise NotFound("Pedido não encontrado")
        o = dict(o)
        effects = {"reservas_liberadas": 0, "estoque_estornado": 0, "reembolso": None,
                   "efeitos_externos": [], "observacao": "Cancelar o pedido não desfaz ações já realizadas no provedor."}
        if o["status"] == "aguardando_pagamento":
            effects["reservas_liberadas"] = _release_reservations(c, o["id"], "cancelamento", o["correlation_id"], o["partner_id"], o["region_code"])
            _set_order_status(c, o, "cancelado", actor, reason)
            effects["efeitos_externos"].append("Intenção de pagamento pendente: se o provedor confirmar depois, um reembolso será preparado.")
        elif o["status"] == "pago":
            for r in c.execute("SELECT * FROM reservations WHERE order_id=? AND status='consumida'", (o["id"],)).fetchall():
                c.execute("UPDATE inventory SET on_hand = on_hand + ?, updated_at=? WHERE warehouse_id=? AND product_id=?",
                          (r["qty"], iso(), r["warehouse_id"], r["product_id"]))
                c.execute("INSERT INTO inventory_movements(id,warehouse_id,product_id,kind,qty,order_id,reservation_id,at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                          (new_id("mov"), r["warehouse_id"], r["product_id"], "estorno_cancelamento", r["qty"], o["id"], r["id"], iso(), actor.label))
                effects["estoque_estornado"] += r["qty"]
            _set_order_status(c, o, "cancelado", actor, reason)
            p = dict(c.execute("SELECT * FROM payments WHERE order_id=? AND status='confirmado'", (o["id"],)).fetchone())
            effects["efeitos_externos"].append("Pagamento já confirmado no provedor (não é desfeito pelo cancelamento).")
            effects["reembolso"] = _request_refund(c, o, p, reason or "cancelamento", actor)
        else:
            raise Conflict(f"Pedido em '{o['status']}' não pode ser cancelado (use devolução).", "estado_invalido")
    return effects


def _request_refund(c, o, p, reason, actor):
    rid = new_id("rmb")
    c.execute("INSERT INTO refunds(id,order_id,payment_id,amount_minor,currency,reason,status,created_at) VALUES (?,?,?,?,?,?,?,?)",
              (rid, o["id"], p["id"], p["amount_minor"], p["currency"], reason, "preparado", iso()))
    req_actor = actor if actor.kind != "public" else SYSTEM
    aid, mode = policies.request_action(c, "reembolso.executar", {"refund_id": rid, "order_code": o["code"]}, req_actor,
                                        p["amount_minor"], p["currency"], o["partner_id"], o["correlation_id"], reason)
    c.execute("UPDATE refunds SET action_request_id=?, status=CASE WHEN status='preparado' THEN ? ELSE status END WHERE id=?",
              (aid, "aguardando_aprovacao" if mode != "automatico" else "preparado", rid))
    return {"refund_id": rid, "action_request_id": aid, "modo": mode}


@policies.executor("reembolso.executar")
def _exec_refund(c, payload, req, actor):
    r = dict(c.execute("SELECT * FROM refunds WHERE id=?", (payload["refund_id"],)).fetchone())
    p = dict(c.execute("SELECT * FROM payments WHERE id=?", (r["payment_id"],)).fetchone())
    o = dict(c.execute("SELECT * FROM orders WHERE id=?", (r["order_id"],)).fetchone())
    region = dict(c.execute("SELECT * FROM regions WHERE code=?", (o["region_code"],)).fetchone())
    prov = providers.payment_provider(region)
    c.execute("UPDATE refunds SET status='enviado_ao_provedor' WHERE id=?", (r["id"],))
    ref = prov.refund(p["provider_ref"], r["amount_minor"], r["currency"], r["id"])
    c.execute("UPDATE refunds SET provider_ref=? WHERE id=?", (ref, r["id"]))
    return {"executado_externamente": True, "simulado": prov.simulated, "provedor": prov.code, "refund_ref": ref,
            "confirmacao": "aguardando webhook refund.succeeded"}


def _apply_refund(c, p, o, data, occurred):
    r = c.execute("SELECT * FROM refunds WHERE id=?", (data.get("refund_id"),)).fetchone()
    if not r:
        raise RuntimeError("Reembolso desconhecido")
    if r["status"] == "executado":
        return "ignorado_fora_ordem"
    c.execute("UPDATE refunds SET status='executado', executed_at=? WHERE id=?", (occurred, r["id"]))
    c.execute("UPDATE payments SET status='reembolsado', updated_at=? WHERE id=?", (iso(), p["id"]))
    emit(c, "pagamento.reembolsado", "demo_pagamentos", "payment", p["id"], o["partner_id"], o["region_code"], o["correlation_id"],
         "reembolsado", occurred_at=occurred, payload={"order_code": o["code"], "amount_minor": r["amount_minor"]})
    ce = c.execute("SELECT * FROM commission_entries WHERE order_id=? AND kind='apuracao'", (o["id"],)).fetchone()
    if ce and not c.execute("SELECT 1 FROM commission_entries WHERE order_id=? AND kind='estorno'", (o["id"],)).fetchone():
        eid = new_id("com")
        c.execute("INSERT INTO commission_entries(id,partner_id,order_id,kind,base_minor,rate_bp,amount_minor,currency,status,created_at,note)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (eid, ce["partner_id"], o["id"], "estorno", -ce["base_minor"], ce["rate_bp"],
                                                     -ce["amount_minor"], ce["currency"], "estornada", iso(), "Reembolso do pedido"))
        c.execute("UPDATE commission_entries SET status='estornada' WHERE id=?", (ce["id"],))
        emit(c, "comissao.estornada", "comissoes", "commission", eid, o["partner_id"], o["region_code"], o["correlation_id"],
             "estornada", payload={"amount_minor": -ce["amount_minor"]})
    ret = c.execute("SELECT id FROM returns WHERE refund_id=?", (r["id"],)).fetchone()
    if ret:
        c.execute("UPDATE returns SET status='concluida' WHERE id=?", (ret[0],))
        _set_order_status(c, o, "devolvido", SYSTEM)
    return "processado"


def create_return(actor: Principal, order_id, reason):
    actor.require("returns.create")
    with R.db.tx() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o or o["status"] != "enviado":
            raise Conflict("Devolução exige pedido enviado", "estado_invalido")
        if c.execute("SELECT 1 FROM returns WHERE order_id=? AND status!='recusada'", (order_id,)).fetchone():
            raise Conflict("Devolução já aberta", "duplicado")
        rid = new_id("dev")
        c.execute("INSERT INTO returns(id,order_id,reason,status,created_at,created_by) VALUES (?,?,?,?,?,?)",
                  (rid, order_id, reason, "aguardando_recebimento", iso(), actor.label))
        emit(c, "devolucao.aberta", "devolucoes", "return", rid, o["partner_id"], o["region_code"], o["correlation_id"], "aguardando_recebimento")
    return rid


def receive_return(actor: Principal, return_id):
    actor.require("returns.create")
    with R.db.tx() as c:
        rt = c.execute("SELECT * FROM returns WHERE id=?", (return_id,)).fetchone()
        if not rt or rt["status"] != "aguardando_recebimento":
            raise Conflict("Devolução não está aguardando recebimento")
        o = dict(c.execute("SELECT * FROM orders WHERE id=?", (rt["order_id"],)).fetchone())
        for r in c.execute("SELECT * FROM reservations WHERE order_id=? AND status='consumida'", (o["id"],)).fetchall():
            c.execute("UPDATE inventory SET on_hand = on_hand + ?, updated_at=? WHERE warehouse_id=? AND product_id=?",
                      (r["qty"], iso(), r["warehouse_id"], r["product_id"]))
            c.execute("INSERT INTO inventory_movements(id,warehouse_id,product_id,kind,qty,order_id,reservation_id,at,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                      (new_id("mov"), r["warehouse_id"], r["product_id"], "devolucao", r["qty"], o["id"], r["id"], iso(), actor.label))
        p = dict(c.execute("SELECT * FROM payments WHERE order_id=? AND status='confirmado'", (o["id"],)).fetchone())
        ref = _request_refund(c, o, p, "devolucao", actor)
        c.execute("UPDATE returns SET status='recebida_aguardando_reembolso', refund_id=? WHERE id=?", (ref["refund_id"], return_id))
        emit(c, "devolucao.recebida", "devolucoes", "return", return_id, o["partner_id"], o["region_code"], o["correlation_id"], "recebida")
    return ref


def expire_reservations():
    rows = R.db.all("SELECT DISTINCT o.* FROM orders o JOIN reservations r ON r.order_id=o.id WHERE o.status='aguardando_pagamento' "
                    "AND r.status='ativa' AND r.expires_at <= ?", iso())
    for o in rows:
        with R.db.tx() as c:
            cur = dict(c.execute("SELECT * FROM orders WHERE id=?", (o["id"],)).fetchone())
            if cur["status"] != "aguardando_pagamento":
                continue
            _release_reservations(c, o["id"], "reserva_expirada", o["correlation_id"], o["partner_id"], o["region_code"])
            _set_order_status(c, cur, "expirado", SYSTEM, "reserva_expirada")
    return len(rows)


# ------------------------------------------------------------------ consultas internas
def order_view(o, actor: Principal):
    out = dict(o)
    out.pop("access_token_hash", None)
    out.pop("idempotency_key", None)
    if not actor.can("orders.read_pii"):
        out["customer_name"] = mask_name(o["customer_name"])
        out["customer_email"] = mask_email(o["customer_email"])
        sj = loads(o["shipping_json"]) or {}
        out["shipping_json"] = dumps({"city": sj.get("city"), "state": sj.get("state"), "country": sj.get("country")})
    return out


def list_orders(actor: Principal, partner_id=None, status=None, q=None, limit=200):
    actor.require("orders.read")
    sql, args = "SELECT * FROM orders WHERE 1=1", []
    scope = actor.partner_scope()
    if scope:
        sql += " AND partner_id=?"
        args.append(scope)
    if partner_id:
        actor.require_partner(partner_id)
        sql += " AND partner_id=?"
        args.append(partner_id)
    if status:
        sql += " AND status=?"
        args.append(status)
    if q:
        sql += " AND code LIKE ?"
        args.append(f"%{q.upper()}%")
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [order_view(o, actor) for o in R.db.all(sql, *args)]


def get_order(actor: Principal, order_id):
    o = R.db.one("SELECT * FROM orders WHERE id=? OR code=?", order_id, order_id)
    if not o:
        raise NotFound("Pedido não encontrado")
    actor.require("orders.read", o["partner_id"])
    out = order_view(o, actor)
    oid = o["id"]
    out["items"] = R.db.all("SELECT sku,name,qty,unit_price_minor,line_total_minor FROM order_items WHERE order_id=?", oid)
    out["reservations"] = R.db.all("SELECT id,warehouse_id,product_id,qty,status,expires_at,close_reason FROM reservations WHERE order_id=?", oid)
    out["payments"] = R.db.all("SELECT id,provider,status,amount_minor,currency,fee_minor,is_simulated,last_event_occurred_at,updated_at FROM payments WHERE order_id=?", oid)
    out["shipment"] = R.db.one("SELECT * FROM shipments WHERE order_id=?", oid)
    out["commissions"] = R.db.all("SELECT * FROM commission_entries WHERE order_id=? ORDER BY created_at", oid)
    out["refunds"] = R.db.all("SELECT * FROM refunds WHERE order_id=?", oid)
    out["returns"] = R.db.all("SELECT * FROM returns WHERE order_id=?", oid)
    out["timeline"] = R.db.all("SELECT id,type,source,status,occurred_at,received_at FROM events WHERE correlation_id=? ORDER BY id",
                               o["correlation_id"])
    return out
