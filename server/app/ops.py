"""Visão operacional: sinalização dos prédios, mundo 3D, prédio do parceiro e central de aplicativos."""
from __future__ import annotations

from datetime import timedelta

from .core import R, NotFound, Principal, money
from .db import iso, loads, now, parse_iso
from . import agents, closings, commerce, tasks

Q_SHIP = "SELECT COUNT(*) FROM orders WHERE status='pago'"
Q_TICKETS = "SELECT COUNT(*) FROM support_tickets WHERE status!='fechado'"
Q_APPR = "SELECT COUNT(*) FROM action_requests WHERE status='aguardando_aprovacao'"
LEVEL_ORDER = {"vermelho": 3, "cinza": 2, "amarelo": 1, "verde": 0}


def building_status(partner_id):
    """Verde só quando HÁ dados recentes e nenhuma pendência crítica. Ausência de dados = cinza, nunca verde."""
    reasons = []
    ts_now = iso()
    last_ev = R.db.one("SELECT received_at, type FROM events WHERE partner_id=? ORDER BY id DESC LIMIT 1", partner_id)
    if not last_ev:
        reasons.append({"level": "cinza", "message": "Nenhum dado operacional recebido para este parceiro.",
                        "source": "eventos", "updated_at": ts_now, "action": {"label": "Ver aplicativos", "floor": "aplicativos"}})
    else:
        age_h = (now() - parse_iso(last_ev["received_at"])).total_seconds() / 3600
        if age_h > R.settings.freshness_hours:
            reasons.append({"level": "cinza", "message": f"Sem dados atualizados há {age_h:.0f} h (último: {last_ev['type']}).",
                            "source": "eventos", "updated_at": last_ev["received_at"],
                            "action": {"label": "Ver aplicativos", "floor": "aplicativos"}})
    for i in R.db.all("SELECT * FROM integrations WHERE partner_id=? OR partner_id IS NULL", partner_id):
        if i["kind"] == "integracao_conectada" and i["status"] == "erro":
            reasons.append({"level": "vermelho" if i["critical"] else "amarelo", "message": f"Integração com falha: {i['name']} — {i['last_error'] or ''}",
                            "source": f"integração {i['code']}", "updated_at": i["last_sync_at"] or ts_now,
                            "action": {"label": "Abrir aplicativos", "floor": "aplicativos"}})
        elif i["kind"] == "integracao_conectada" and i["critical"] and i["status"] == "desconectado":
            reasons.append({"level": "cinza", "message": f"Integração crítica desconectada: {i['name']}", "source": f"integração {i['code']}",
                            "updated_at": i["last_sync_at"] or ts_now, "action": {"label": "Abrir aplicativos", "floor": "aplicativos"}})
    open_t = R.db.all("SELECT * FROM tasks WHERE partner_id=? AND status IN ('a_fazer','em_andamento','aguardando_terceiro','aguardando_aprovacao') "
                      "AND (recurrence_rule IS NULL OR recurrence_parent_id IS NOT NULL)", partner_id)
    for t in open_t:
        od = tasks.is_overdue(t)
        if t["kind"] == "pendencia" and t["priority"] == "critica":
            reasons.append({"level": "vermelho", "message": f"Pendência crítica: {t['title']}", "source": f"tarefa {t['id']}",
                            "updated_at": t["updated_at"], "action": {"label": "Resolver pendência", "floor": "pendencias", "task_id": t["id"]}})
        elif od:
            reasons.append({"level": "vermelho" if t["priority"] == "critica" else "amarelo", "message": f"Tarefa atrasada: {t['title']}",
                            "source": f"tarefa {t['id']}", "updated_at": t["updated_at"],
                            "action": {"label": "Abrir tarefa", "floor": "operacao", "task_id": t["id"]}})
        elif t["kind"] == "pendencia":
            reasons.append({"level": "amarelo", "message": f"Pendência: {t['title']}", "source": f"tarefa {t['id']}",
                            "updated_at": t["updated_at"], "action": {"label": "Ver pendência", "floor": "pendencias", "task_id": t["id"]}})
    for cl in R.db.all("SELECT * FROM closings WHERE partner_id=? AND superseded_by_id IS NULL AND status IN ('preparado','conferido')", partner_id):
        reasons.append({"level": "amarelo", "message": f"Fechamento {cl['kind']} {cl['local_label']} em '{cl['status']}'",
                        "source": f"fechamento {cl['id']}", "updated_at": cl["prepared_at"],
                        "action": {"label": "Abrir financeiro", "floor": "financeiro"}})
    level = max((r["level"] for r in reasons), key=lambda l: LEVEL_ORDER[l], default="verde")
    text = {"verde": "Operação sem pendências críticas identificadas", "amarelo": "Atenção necessária",
            "vermelho": "Bloqueio ou problema crítico", "cinza": "Sem conexão ou sem dados atualizados"}[level]
    reasons.sort(key=lambda r: -LEVEL_ORDER[r["level"]])
    return {"level": level, "text": text, "reasons": reasons, "computed_at": ts_now,
            "last_data_at": last_ev["received_at"] if last_ev else None}


def _hq(actor):
    from . import registry
    h = registry.get_hq()
    return {"lat": h["lat"], "lng": h["lng"], "padrao": bool(h.get("padrao")), "geo_source": h.get("geo_source"),
            **({"name": h.get("name"), "city": h.get("city")} if actor.is_central else {})}   # endereço da sede não vai para parceiros


def world(actor: Principal):
    actor.require("world.read")
    scope = actor.partner_scope()
    regions = R.db.all("SELECT code,name,currency,locale,timezone,sales_enabled,sales_status_note,tax_status_note,seller_entity FROM regions")
    partners = R.db.all("SELECT * FROM partners" + (" WHERE id=?" if scope else "") + " ORDER BY region_code, name", *([scope] if scope else []))
    since = iso(now() - timedelta(days=30)); tsn = iso()
    ops_by_unit = {r["unit_id"]: r for r in R.db.all(
        "SELECT unit_id, SUM(status NOT IN ('concluida','cancelada')) abertas,"
        " SUM(status NOT IN ('concluida','cancelada') AND due_at IS NOT NULL AND due_at<?) atrasadas,"
        " SUM(status='concluida' AND completed_at>=?) feitas_30d, MAX(CASE WHEN status='concluida' THEN completed_at END) ultima"
        " FROM tasks WHERE unit_id IS NOT NULL GROUP BY unit_id", tsn, since)}
    for p in partners:
        p["units"] = R.db.all("SELECT * FROM partner_units WHERE partner_id=?", p["id"])
        for u in p["units"]:   # operações realizadas e em aberto por unidade (aparece no mapa)
            o = ops_by_unit.get(u["id"]) or {}
            u["ops"] = {"abertas": o.get("abertas") or 0, "atrasadas": o.get("atrasadas") or 0,
                        "feitas_30d": o.get("feitas_30d") or 0, "ultima": o.get("ultima")}
        p["status"] = building_status(p["id"])
        p["open_orders"] = R.db.val("SELECT COUNT(*) FROM orders WHERE partner_id=? AND status IN ('aguardando_pagamento','pago')", p["id"])
        p["storefronts"] = R.db.all("SELECT id,slug,title,published FROM storefronts WHERE partner_id=?", p["id"])
    whs = R.db.all("SELECT * FROM warehouses")
    for w in whs:
        w["stock"] = R.db.one("SELECT COALESCE(SUM(on_hand),0) on_hand, COALESCE(SUM(reserved),0) reserved FROM inventory WHERE warehouse_id=?", w["id"])
        w["low"] = R.db.val("SELECT COUNT(*) FROM inventory i JOIN product_regions rp ON rp.product_id=i.product_id AND rp.region_code=? "
                            "WHERE i.warehouse_id=? AND i.on_hand - i.reserved < rp.reorder_point", w["region_code"], w["id"])
    sectors = []
    if actor.is_central:
        sectors = [
            {"code": "catalogo", "name": "Catálogo", "metric": f"{R.db.val('SELECT COUNT(*) FROM products')} produtos"},
            {"code": "logistica", "name": "Logística", "metric": f"{R.db.val(Q_SHIP)} pedidos a enviar"},
            {"code": "atendimento", "name": "Atendimento", "metric": f"{R.db.val(Q_TICKETS)} chamados (módulo planejado)"},
            {"code": "financeiro", "name": "Financeiro", "metric": f"{R.db.val(Q_APPR)} aprovações pendentes"},
        ]
    bases = R.db.all("SELECT * FROM service_bases WHERE active=1 ORDER BY name") if actor.is_central else []
    for b in bases:
        b["units"] = R.db.all("SELECT id, partner_id FROM partner_units WHERE base_id=? AND active=1", b["id"])
        b["tecnicos"] = R.db.val("SELECT COUNT(*) FROM users WHERE base_id=? AND role='tecnico' AND active=1", b["id"])
        b["pendentes"] = R.db.val("SELECT COUNT(*) FROM users WHERE base_id=? AND role='tecnico' AND active=0", b["id"])
    from . import geo
    return {
        "hq": _hq(actor),
        "region_country": geo.REGION_COUNTRY, "country_center": geo.COUNTRY_CENTER, "geo": geo.status(),
        "service_bases": bases,
        "environment": R.settings.env, "generated_at": iso(), "view": "central" if actor.is_central else "parceiro",
        "regions": regions, "partners": partners, "warehouses": whs if actor.is_central else
        [w for w in whs if any(p["region_code"] == w["region_code"] for p in partners)],
        "sectors": sectors, "agents": agents.list_agents(actor),
        "agents_paused": R.db.val("SELECT value FROM system_flags WHERE key='agents_paused'") == "1",
        "layout_note": "Posições no ambiente 3D são ilustrativas. Localização física só é exibida como confirmada quando há endereço cadastrado e verificado.",
        "last_event_id": R.db.val("SELECT COALESCE(MAX(id),0) FROM events"),
    }


def partner_building(actor: Principal, partner_id):
    actor.require("partners.read", partner_id)
    p = R.db.one("SELECT p.*, r.name region_name, r.currency, r.timezone, r.seller_entity FROM partners p JOIN regions r ON r.code=p.region_code WHERE p.id=?", partner_id)
    if not p:
        raise NotFound("Parceiro não encontrado")
    units = R.db.all("SELECT u.*, b.name base_name FROM partner_units u LEFT JOIN service_bases b ON b.id=u.base_id WHERE u.partner_id=?", partner_id)
    team = R.db.all("SELECT id,name,role,email FROM users WHERE partner_id=? AND active=1", partner_id)
    central_people = R.db.all("SELECT id,name,role FROM users WHERE partner_id IS NULL AND active=1") if actor.is_central else []
    t_all = tasks.list_tasks(actor, partner_id=partner_id)
    orders = commerce.list_orders(actor, partner_id=partner_id, limit=50)
    model = p["commercial_model"]
    fin = {"model": model}
    if model == "cliente_servico":
        fin["contract_minor"] = p["contract_minor"]
        fin["note"] = ("Cliente de serviços de TI: o fechamento mensal de serviços lista o que foi feito por unidade, com evidências, "
                       "e o valor do contrato a receber. Aprovar não significa que o cliente pagou: registre o recebimento à parte.")
    elif model == "central_commission":
        rows = R.db.all("SELECT kind,status,SUM(amount_minor) total FROM commission_entries WHERE partner_id=? GROUP BY kind,status", partner_id)
        fin["commissions"] = rows
        fin["a_receber_minor"] = sum(r["total"] for r in rows if r["status"] in ("em_carencia", "liberada") or r["kind"] in ("estorno", "ajuste") and r["status"] != "paga")
        fin["pago_minor"] = sum(r["total"] for r in rows if r["status"] == "paga")
        fin["vendas_minor"] = R.db.val("SELECT COALESCE(SUM(o.total_minor),0) FROM orders o JOIN payments pa ON pa.order_id=o.id "
                                       "WHERE o.partner_id=? AND pa.status IN ('confirmado','reembolsado')", partner_id)
        fin["taxas_minor"] = R.db.val("SELECT COALESCE(SUM(pa.fee_minor),0) FROM orders o JOIN payments pa ON pa.order_id=o.id "
                                      "WHERE o.partner_id=? AND pa.status IN ('confirmado','reembolsado')", partner_id)
        fin["note"] = "Venda central: a central fatura o cliente; o parceiro recebe comissão. Taxas de pagamento são custo da central."
    else:
        fin["note"] = "Revenda B2B: modalidade preparada no modelo de dados, ainda não operacional (Etapa 5)."
    authorized = R.db.all("SELECT pr.id, pr.sku, pr.name, rp.price_minor FROM partner_products pp JOIN products pr ON pr.id=pp.product_id "
                          "JOIN product_regions rp ON rp.product_id=pr.id AND rp.region_code=? WHERE pp.partner_id=? AND pp.authorized=1",
                          p["region_code"], partner_id)
    for a in authorized:
        a["available"] = commerce.available_qty(a["id"], p["region_code"])
    storefronts = R.db.all("SELECT * FROM storefronts WHERE partner_id=?", partner_id)
    for s in storefronts:
        s["config"] = loads(s.pop("config_json"))
        s["product_ids"] = [r["product_id"] for r in R.db.all("SELECT product_id FROM storefront_products WHERE storefront_id=? ORDER BY position", s["id"])]
    ag = agents.list_agents(actor)
    linked = [a for a in ag if a["current_partner_id"] == partner_id or any(t["assignee_id"] == a["id"] for t in t_all)]
    apps = list_apps(actor, partner_id)
    return {
        "partner": p, "status": building_status(partner_id), "environment": R.settings.env,
        "floors": {
            "recepcao": {"units": units, "responsaveis": team, "avisos": [r["message"] for r in building_status(partner_id)["reasons"][:5]]},
            "comercial": {"storefronts": storefronts, "authorized_products": authorized, "orders": orders,
                          "metas": {"status": "planejado", "note": "Metas comerciais aguardam definição (decisão pendente)."}},
            "operacao": {"tasks": [t for t in t_all if t["kind"] == "tarefa"], "series": [s for s in tasks.list_series(actor) if s["partner_id"] == partner_id]},
            "financeiro": {**fin, "closings": closings.list_closings(actor, partner_id)},
            "pendencias": {"items": [t for t in t_all if t["kind"] == "pendencia" or t["overdue"] or t["status"] == "aguardando_aprovacao"],
                           "integration_failures": [a for a in apps if a["status"] == "erro"]},
            "aplicativos": apps,
            "equipe": {"pessoas": team, "central": central_people, "agentes": linked or ag[:0], "todos_agentes": ag},
        },
    }


def list_apps(actor: Principal, partner_id):
    actor.require("apps.read", partner_id)
    rows = R.db.all("SELECT * FROM integrations WHERE partner_id=? OR partner_id IS NULL ORDER BY category, name", partner_id)
    labels = {"modulo_interno": "Módulo interno funcional", "integracao_conectada": "Integração conectada",
              "link_externo": "Link para serviço externo", "planejada": "Funcionalidade planejada"}
    for r in rows:
        r["kind_label"] = labels[r["kind"]]
    return rows


def heartbeat_internal_modules():
    """Módulos internos fazem parte deste servidor: o sinal de vida é o próprio servidor respondendo."""
    with R.db.tx() as c:
        c.execute("UPDATE integrations SET last_sync_at=?, status='ativo' WHERE kind='modulo_interno'", (iso(),))


def overview(actor: Principal):
    """Comparativo da central para o modo tabelas."""
    actor.require("world.read")
    scope = actor.partner_scope()
    out = []
    for p in R.db.all("SELECT p.*, r.currency FROM partners p JOIN regions r ON r.code=p.region_code" + (" WHERE p.id=?" if scope else ""),
                      *([scope] if scope else [])):
        st = building_status(p["id"])
        out.append({"id": p["id"], "name": p["name"], "region": p["region_code"], "model": p["commercial_model"], "is_demo": p["is_demo"],
                    "status": st["level"], "status_text": st["text"], "reasons": len(st["reasons"]),
                    "overdue_tasks": sum(1 for t in tasks.list_tasks(actor, partner_id=p["id"]) if t["overdue"]),
                    "pending_closings": R.db.val("SELECT COUNT(*) FROM closings WHERE partner_id=? AND superseded_by_id IS NULL AND status IN ('preparado','conferido')", p["id"]),
                    "failed_integrations": R.db.val("SELECT COUNT(*) FROM integrations WHERE partner_id=? AND status='erro'", p["id"]),
                    "sales": money(R.db.val("SELECT COALESCE(SUM(o.total_minor),0) FROM orders o JOIN payments pa ON pa.order_id=o.id WHERE o.partner_id=? AND pa.status='confirmado'", p["id"]), p["currency"])})
    return out
