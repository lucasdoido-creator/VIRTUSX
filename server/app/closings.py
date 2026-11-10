"""Fechamentos (vendas, comissões) e conciliação de pagamentos.

Fluxo: preparado → conferido → aprovado → encerrado. Liquidação é um estado SEPARADO:
aprovar um fechamento não significa que houve transferência de dinheiro.
Reabertura cria nova revisão; a revisão original e seus totais permanecem intactos.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from .tz import get_tz

from .core import R, AppError, Conflict, Forbidden, NotFound, Principal, audit, emit, money
from .db import dumps, iso, loads, new_id

KINDS = ("vendas", "comissoes", "conciliacao_pagamentos", "servicos")
FLOW = {"preparado": ("conferido", "closings.review"), "conferido": ("aprovado", "closings.approve"),
        "aprovado": ("encerrado", "closings.close")}


def period_bounds(period_type, local_day: str, tz: str):
    d = date.fromisoformat(local_day)
    if period_type == "diario":
        start, end = d, d + timedelta(days=1)
        label = d.strftime("%d/%m/%Y")
    elif period_type == "semanal":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=7)
        label = f"Semana {start.strftime('%d/%m')}–{(end - timedelta(days=1)).strftime('%d/%m/%Y')}"
    elif period_type == "mensal":
        start = d.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1)
        label = start.strftime("%m/%Y")
    else:
        raise AppError("Período inválido")
    z = get_tz(tz)
    s_utc = datetime.combine(start, time(0), z)
    e_utc = datetime.combine(end, time(0), z)
    return iso(s_utc), iso(e_utc), label


def compute(kind, partner_id, unit_id, start, end):
    unit_sql = " AND o.unit_id=?" if unit_id else ""
    ua = [unit_id] if unit_id else []
    div = []
    if kind == "vendas":
        orders = R.db.all(f"SELECT o.* FROM orders o WHERE o.partner_id=? AND o.created_at>=? AND o.created_at<?{unit_sql}",
                          partner_id, start, end, *ua)
        t = {"pedidos": len(orders), "vendas_confirmadas_minor": 0, "pedidos_confirmados": 0, "cancelamentos": 0,
             "cancelamentos_minor": 0, "devolucoes": 0, "reembolsos_minor": 0, "taxas_minor": 0, "recusados": 0,
             "expirados": 0, "aguardando_pagamento": 0}
        for o in orders:
            pays = R.db.all("SELECT * FROM payments WHERE order_id=?", o["id"])
            confirmed = [p for p in pays if p["status"] in ("confirmado", "reembolsado")]
            if confirmed:
                t["vendas_confirmadas_minor"] += o["total_minor"]
                t["pedidos_confirmados"] += 1
                t["taxas_minor"] += sum(p["fee_minor"] for p in confirmed)
            if o["status"] == "cancelado":
                t["cancelamentos"] += 1
                if confirmed:
                    t["cancelamentos_minor"] += o["total_minor"]
            if o["status"] == "devolvido":
                t["devolucoes"] += 1
            t["recusados"] += o["status"] == "pagamento_recusado"
            t["expirados"] += o["status"] == "expirado"
            t["aguardando_pagamento"] += o["status"] == "aguardando_pagamento"
            t["reembolsos_minor"] += R.db.val("SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE order_id=? AND status='executado'", o["id"])
        t["liquido_minor"] = t["vendas_confirmadas_minor"] - t["reembolsos_minor"] - t["taxas_minor"]
        if t["aguardando_pagamento"]:
            div.append({"tipo": "pedidos_em_aberto", "detalhe": f"{t['aguardando_pagamento']} pedido(s) ainda aguardando pagamento no período"})
        return t, div
    if kind == "servicos":
        tsql = " AND t.unit_id=?" if unit_id else ""
        rows = R.db.all(f"SELECT t.* FROM tasks t WHERE t.partner_id=? AND (t.recurrence_rule IS NULL OR t.recurrence_parent_id IS NOT NULL) "
                        f"AND ((t.completed_at>=? AND t.completed_at<?) OR (t.status NOT IN ('concluida','cancelada') AND t.due_at<?)){tsql}",
                        partner_id, start, end, end, *ua)
        done = [t for t in rows if t["status"] == "concluida" and t["completed_at"] and start <= t["completed_at"] < end]
        late = [t for t in rows if t["status"] not in ("concluida", "cancelada")]
        ev = {t["id"]: R.db.val("SELECT COUNT(*) FROM attachments WHERE entity_type='task' AND entity_id=? AND is_evidence=1", t["id"]) for t in done}
        by_tpl = {}
        for t in done:
            by_tpl[t["template"] or "outros"] = by_tpl.get(t["template"] or "outros", 0) + 1
        units = {}
        for t in done:
            units[t["unit_id"] or "sem_unidade"] = units.get(t["unit_id"] or "sem_unidade", 0) + 1
        contract = R.db.val("SELECT contract_minor FROM partners WHERE id=?", partner_id) or 0
        t = {"servicos_concluidos": len(done), "preventivas": by_tpl.get("preventiva_ti", 0), "chamados": by_tpl.get("chamado_ti", 0),
             "outros_servicos": len(done) - by_tpl.get("preventiva_ti", 0) - by_tpl.get("chamado_ti", 0),
             "atrasados_em_aberto": len(late), "com_evidencia": sum(1 for v in ev.values() if v),
             "unidades_atendidas": len([u for u in units if u != "sem_unidade"]),
             "valor_contrato_minor": contract if period_type_of(start, end) == "mensal" else 0}
        t["a_receber_minor"] = t["valor_contrato_minor"]
        t["pago_minor"] = 0
        for x in done:
            if not ev[x["id"]]:
                div.append({"tipo": "sem_evidencia", "detalhe": f"Serviço concluído sem evidência anexada: {x['title'][:80]}"})
        for x in late:
            div.append({"tipo": "atrasado", "detalhe": f"Serviço atrasado em aberto: {x['title'][:80]}"})
        if not contract:
            div.append({"tipo": "sem_contrato", "detalhe": "Cliente sem valor de contrato mensal cadastrado."})
        return t, div
    if kind == "comissoes":
        rows = R.db.all(f"SELECT c.* FROM commission_entries c JOIN orders o ON o.id=c.order_id WHERE c.partner_id=? "
                        f"AND c.created_at>=? AND c.created_at<?{unit_sql}", partner_id, start, end, *ua)
        t = {"lancamentos": len(rows),
             "apurado_minor": sum(r["amount_minor"] for r in rows if r["kind"] == "apuracao"),
             "estornos_minor": sum(r["amount_minor"] for r in rows if r["kind"] == "estorno"),
             "ajustes_minor": sum(r["amount_minor"] for r in rows if r["kind"] == "ajuste"),
             "em_carencia_minor": sum(r["amount_minor"] for r in rows if r["kind"] == "apuracao" and r["status"] == "em_carencia"),
             "liberado_minor": sum(r["amount_minor"] for r in rows if r["kind"] == "apuracao" and r["status"] == "liberada")}
        t["liquido_minor"] = t["apurado_minor"] + t["estornos_minor"] + t["ajustes_minor"]
        t["a_receber_minor"] = t["liquido_minor"]
        t["pago_minor"] = 0
        if t["em_carencia_minor"]:
            div.append({"tipo": "carencia", "detalhe": "Há comissões ainda no prazo de devolução (podem ser estornadas)."})
        return t, div
    # conciliação de pagamentos
    orders = R.db.all(f"SELECT o.* FROM orders o WHERE o.partner_id=? AND o.created_at>=? AND o.created_at<?{unit_sql}",
                      partner_id, start, end, *ua)
    t = {"pagamentos_confirmados": 0, "valor_confirmado_minor": 0, "taxas_minor": 0, "valor_liquido_provedor_minor": 0,
         "reembolsos_minor": 0, "divergencias": 0, "simulados": 0}
    for o in orders:
        pays = R.db.all("SELECT * FROM payments WHERE order_id=?", o["id"])
        conf = [p for p in pays if p["status"] in ("confirmado", "reembolsado")]
        t["simulados"] += sum(p["is_simulated"] for p in conf)
        for p in conf:
            t["pagamentos_confirmados"] += 1
            t["valor_confirmado_minor"] += p["amount_minor"]
            t["taxas_minor"] += p["fee_minor"]
            if p["amount_minor"] != o["total_minor"]:
                div.append({"tipo": "valor_divergente", "pedido": o["code"], "detalhe": "Valor do pagamento difere do pedido"})
        refunded = R.db.val("SELECT COALESCE(SUM(amount_minor),0) FROM refunds WHERE order_id=? AND status='executado'", o["id"])
        t["reembolsos_minor"] += refunded
        if o["status"] in ("pago", "enviado") and not conf:
            div.append({"tipo": "pedido_sem_pagamento", "pedido": o["code"], "detalhe": "Pedido pago/enviado sem pagamento confirmado"})
        if o["status"] in ("cancelado", "expirado", "pagamento_recusado") and any(p["status"] == "confirmado" for p in conf):
            pend = R.db.val("SELECT COUNT(*) FROM refunds WHERE order_id=? AND status!='executado'", o["id"])
            div.append({"tipo": "cancelado_com_pagamento", "pedido": o["code"],
                        "detalhe": "Pedido cancelado com pagamento confirmado" + (" — reembolso pendente" if pend else "")})
    t["valor_liquido_provedor_minor"] = t["valor_confirmado_minor"] - t["taxas_minor"] - t["reembolsos_minor"]
    t["divergencias"] = len(div)
    return t, div


def period_type_of(start, end):
    from .db import parse_iso
    return "mensal" if (parse_iso(end) - parse_iso(start)).days >= 28 else "outro"


def prepare(actor: Principal, kind, partner_id, period_type, local_day, unit_id=None):
    actor.require("closings.prepare", partner_id)
    if kind not in KINDS:
        raise AppError("Tipo de fechamento inválido")
    p = R.db.one("SELECT p.*, r.timezone, r.currency FROM partners p JOIN regions r ON r.code=p.region_code WHERE p.id=?", partner_id)
    if not p:
        raise NotFound("Parceiro não encontrado")
    start, end, label = period_bounds(period_type, local_day, p["timezone"])
    with R.db.tx() as c:
        ex = c.execute("SELECT id FROM closings WHERE kind=? AND partner_id=? AND COALESCE(unit_id,'')=? AND period_type=? AND period_start=? "
                       "AND superseded_by_id IS NULL", (kind, partner_id, unit_id or "", period_type, start)).fetchone()
        if ex:
            raise Conflict("Já existe fechamento ativo para este período", "fechamento_existente", extra={"id": ex[0]})
        totals, div = compute(kind, partner_id, unit_id, start, end)
        cid = new_id("fec")
        c.execute("INSERT INTO closings(id,kind,partner_id,unit_id,period_type,period_start,period_end,timezone,local_label,revision,status,"
                  "settlement_status,totals_json,divergences_json,currency,prepared_by,prepared_at,is_demo) VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?,?,?,?,?)",
                  (cid, kind, partner_id, unit_id, period_type, start, end, p["timezone"], label, "preparado",
                   "pendente" if kind in ("comissoes", "servicos") else "nao_aplicavel", dumps(totals), dumps(div), p["currency"],
                   actor.label, iso(), 1 if R.settings.is_demo else 0))
        audit(c, actor, "fechamento.preparado", "closing", cid, None, totals, partner_id)
        emit(c, "fechamento.preparado", "fechamentos", "closing", cid, partner_id, p["region_code"], status="preparado",
             payload={"kind": kind, "periodo": label, "divergencias": len(div)})
    return cid


def advance(actor: Principal, closing_id, note=""):
    with R.db.tx() as c:
        cl = c.execute("SELECT * FROM closings WHERE id=?", (closing_id,)).fetchone()
        if not cl:
            raise NotFound("Fechamento não encontrado")
        cl = dict(cl)
        if cl["status"] not in FLOW:
            raise Conflict(f"Fechamento em '{cl['status']}' não avança", "estado_invalido")
        nxt, perm = FLOW[cl["status"]]
        actor.require(perm, cl["partner_id"])
        if nxt == "aprovado" and not actor.is_central:
            raise Forbidden("Aprovação é feita pela central", "aprovacao_central")
        # segregação de funções
        if nxt == "conferido" and cl["prepared_by"] == actor.label:
            raise Forbidden("Quem preparou não pode conferir", "segregacao_funcoes")
        if nxt == "aprovado" and actor.label in (cl["prepared_by"], cl["reviewed_by"]):
            raise Forbidden("Aprovação exige pessoa diferente de quem preparou/conferiu", "segregacao_funcoes")
        if nxt == "aprovado" and loads(cl["divergences_json"]) and not note.strip():
            raise AppError("Há divergências: registre uma justificativa para aprovar", "justificativa_obrigatoria")
        col = {"conferido": "reviewed", "aprovado": "approved", "encerrado": "closed"}[nxt]
        c.execute(f"UPDATE closings SET status=?, {col}_by=?, {col}_at=? WHERE id=?", (nxt, actor.label, iso(), closing_id))
        audit(c, actor, f"fechamento.{nxt}", "closing", closing_id, {"status": cl["status"]}, {"status": nxt, "nota": note}, cl["partner_id"])
        emit(c, f"fechamento.{nxt}", "fechamentos", "closing", closing_id, cl["partner_id"], status=nxt,
             payload={"kind": cl["kind"], "periodo": cl["local_label"]})
    return nxt


def reopen(actor: Principal, closing_id, reason):
    actor.require("closings.reopen")
    if not (reason or "").strip():
        raise AppError("Motivo obrigatório para reabrir")
    with R.db.tx() as c:
        cl = c.execute("SELECT * FROM closings WHERE id=?", (closing_id,)).fetchone()
        if not cl:
            raise NotFound("Fechamento não encontrado")
        cl = dict(cl)
        if cl["superseded_by_id"]:
            raise Conflict("Revisão já substituída")
        if cl["status"] not in ("aprovado", "encerrado"):
            raise Conflict("Só fechamentos aprovados/encerrados são reabertos")
        if cl["settlement_status"] == "liquidado":
            raise Conflict("Período já liquidado: registre um ajuste em vez de reabrir", "liquidado")
        totals, div = compute(cl["kind"], cl["partner_id"], cl["unit_id"], cl["period_start"], cl["period_end"])
        nid = new_id("fec")
        c.execute("INSERT INTO closings(id,kind,partner_id,unit_id,period_type,period_start,period_end,timezone,local_label,revision,status,"
                  "settlement_status,totals_json,divergences_json,currency,prepared_by,prepared_at,supersedes_id,reopen_reason,is_demo) "
                  "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (nid, cl["kind"], cl["partner_id"], cl["unit_id"], cl["period_type"], cl["period_start"], cl["period_end"],
                   cl["timezone"], cl["local_label"], cl["revision"] + 1, "preparado", cl["settlement_status"], dumps(totals),
                   dumps(div), cl["currency"], actor.label, iso(), closing_id, reason, cl["is_demo"]))
        c.execute("UPDATE closings SET superseded_by_id=? WHERE id=?", (nid, closing_id))
        audit(c, actor, "fechamento.reaberto", "closing", closing_id, {"revision": cl["revision"]},
              {"nova_revisao": nid, "motivo": reason}, cl["partner_id"])
        emit(c, "fechamento.reaberto", "fechamentos", "closing", nid, cl["partner_id"], status="preparado",
             payload={"revisao": cl["revision"] + 1})
    return nid


def settle(actor: Principal, closing_id, reference):
    actor.require("closings.settle")
    with R.db.tx() as c:
        cl = c.execute("SELECT * FROM closings WHERE id=?", (closing_id,)).fetchone()
        if not cl:
            raise NotFound("Fechamento não encontrado")
        cl = dict(cl)
        if cl["kind"] not in ("comissoes", "servicos"):
            raise Conflict("Liquidação aplica-se a fechamentos de comissões ou de serviços (recebimento da mensalidade)")
        if cl["status"] not in ("aprovado", "encerrado") or cl["superseded_by_id"]:
            raise Conflict("Liquidação exige fechamento aprovado e vigente")
        if cl["settlement_status"] == "liquidado":
            raise Conflict("Já liquidado")
        totals = loads(cl["totals_json"])
        simulated = R.settings.is_demo
        if not simulated and not (reference or "").strip():
            raise AppError("Informe a referência da transferência bancária")
        pid = new_id("rep")
        c.execute("INSERT INTO payouts(id,closing_id,partner_id,amount_minor,currency,reference,is_simulated,settled_at,registered_by)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (pid, closing_id, cl["partner_id"], totals["a_receber_minor"], cl["currency"],
                                                 reference or "DEMO-SEM-TRANSFERENCIA", 1 if simulated else 0, iso(), actor.label))
        c.execute("UPDATE closings SET settlement_status='liquidado' WHERE id=?", (closing_id,))
        if cl["kind"] == "comissoes":
            c.execute("UPDATE commission_entries SET status='paga' WHERE partner_id=? AND kind='apuracao' AND status='liberada' "
                      "AND created_at>=? AND created_at<?", (cl["partner_id"], cl["period_start"], cl["period_end"]))
        audit(c, actor, "fechamento.liquidado", "closing", closing_id, None, {"payout": pid, "simulado": simulated}, cl["partner_id"])
        emit(c, "fechamento.liquidado", "fechamentos", "closing", closing_id, cl["partner_id"], status="liquidado",
             payload={"simulado": simulated})
    return pid


def add_adjustment(actor: Principal, partner_id, order_id, amount_minor, note):
    actor.require("closings.reopen")
    if not note:
        raise AppError("Justificativa obrigatória")
    with R.db.tx() as c:
        o = c.execute("SELECT * FROM orders WHERE id=? AND partner_id=?", (order_id, partner_id)).fetchone()
        if not o:
            raise NotFound("Pedido do parceiro não encontrado")
        aid = new_id("com")
        c.execute("INSERT INTO commission_entries(id,partner_id,order_id,kind,base_minor,rate_bp,amount_minor,currency,status,created_at,note)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?)", (aid, partner_id, order_id, "ajuste", 0, 0, int(amount_minor), o["currency"],
                                                     "liberada", iso(), note))
        audit(c, actor, "comissao.ajuste", "commission", aid, None, {"amount_minor": amount_minor, "note": note}, partner_id)
        emit(c, "comissao.ajuste", "comissoes", "commission", aid, partner_id, status="liberada")
    return aid


def list_closings(actor: Principal, partner_id=None):
    actor.require("closings.read")
    scope = actor.partner_scope() or partner_id
    if partner_id:
        actor.require_partner(partner_id)
    rows = R.db.all("SELECT * FROM closings" + (" WHERE partner_id=?" if scope else "") + " ORDER BY period_start DESC, revision DESC",
                    *([scope] if scope else []))
    for r in rows:
        r["totals"] = loads(r.pop("totals_json"))
        r["divergences"] = loads(r.pop("divergences_json"))
        r["payouts"] = R.db.all("SELECT * FROM payouts WHERE closing_id=?", r["id"])
        r["money_hint"] = money(r["totals"].get("liquido_minor", r["totals"].get("valor_liquido_provedor_minor", 0)), r["currency"])
    return rows
