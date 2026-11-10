"""Políticas de autonomia por ação e solicitações de aprovação.

Regra central: sem política cadastrada, a ação é PREPARADA para revisão, nunca executada.
"""
from __future__ import annotations

from .core import R, AppError, Conflict, Forbidden, NotFound, Principal, audit, emit, get_flag
from .db import dumps, iso, loads, new_id

EXECUTORS = {}

# Ações conhecidas (as políticas são configuráveis; esta lista só descreve as ações existentes)
KNOWN_ACTIONS = {
    "reembolso.executar": "Executar reembolso no provedor de pagamento",
    "estoque.compra": "Emitir pedido de compra de estoque ao fornecedor",
    "campanha.paga": "Iniciar campanha paga",
    "comercial.alterar_preco": "Alterar preço regional",
    "comercial.alterar_comissao": "Alterar taxa de comissão de parceiro",
    "atendimento.responder": "Enviar resposta a cliente",
    "pagamento.repasse": "Transferir valores a parceiro",
}


def executor(action):
    def deco(fn):
        EXECUTORS[action] = fn
        return fn
    return deco


def decide(action: str, amount_minor: int | None, currency: str | None):
    p = R.db.one("SELECT * FROM policies WHERE action=?", action)
    if not p:
        return "revisao", "Sem política cadastrada: preparado para revisão."
    if p["mode"] == "proibido":
        return "proibido", "Política proíbe execução automática e manual por agentes."
    if p["mode"] == "revisao":
        return "revisao", "Política exige revisão humana."
    if p["limit_minor"] is not None and amount_minor is not None:
        if p["currency"] and currency and p["currency"] != currency:
            return "revisao", "Moeda diferente da política: revisão."
        if amount_minor > p["limit_minor"]:
            return "revisao", "Valor acima do limite automático."
    return "automatico", "Dentro do limite aprovado."


def request_action(c, action, payload, actor: Principal, amount_minor=None, currency=None, partner_id=None,
                   correlation_id=None, reason=""):
    if actor.kind == "agent" and get_flag("agents_paused", "0") == "1":
        raise Conflict("Agentes pausados globalmente.", "agentes_pausados")
    mode, why = decide(action, amount_minor, currency)
    if mode == "proibido" and actor.kind == "agent":
        raise Forbidden(why, "politica_proibe")
    rid = new_id("act")
    status = "aguardando_aprovacao" if mode != "automatico" else "aprovada"
    c.execute(
        "INSERT INTO action_requests(id,action,partner_id,payload_json,amount_minor,currency,requested_by_type,requested_by,"
        "status,reason,correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (rid, action, partner_id, dumps(payload), amount_minor, currency, actor.kind, actor.id, status,
         f"{reason} | {why}".strip(" |"), correlation_id, iso()))
    audit(c, actor, "acao.solicitada", "action_request", rid, None, {"action": action, "mode": mode}, partner_id, correlation_id)
    emit(c, "acao.solicitada", "politicas", "action_request", rid, partner_id, None, correlation_id,
         "aguardando_aprovacao" if mode != "automatico" else "aprovada", payload={"action": action, "mode": mode})
    if mode == "automatico":
        _execute(c, rid, actor)
    else:
        from . import tasks
        tasks.create_task(c, Principal("system", "politicas"), {
            "title": f"Aprovar: {KNOWN_ACTIONS.get(action, action)}",
            "description": f"Solicitação {rid}. {why}", "partner_id": partner_id, "kind": "pendencia",
            "pendency_type": "decisao_aguardando_aprovacao", "status": "aguardando_aprovacao", "priority": "alta",
            "related_entity": f"action_request:{rid}", "source": "politicas"})
    return rid, mode


def _execute(c, rid, actor):
    req = dict(c.execute("SELECT * FROM action_requests WHERE id=?", (rid,)).fetchone())
    fn = EXECUTORS.get(req["action"])
    if not fn:
        c.execute("UPDATE action_requests SET status='aguardando_integracao', external_effects_json=? WHERE id=?",
                  (dumps({"executado_externamente": False, "motivo": "Sem executor/integração para esta ação."}), rid))
        emit(c, "acao.aguardando_integracao", "politicas", "action_request", rid, req["partner_id"], None,
             req["correlation_id"], "aguardando_integracao", payload={"action": req["action"]})
        return
    try:
        effects = fn(c, loads(req["payload_json"]), req, actor)
    except AppError as e:
        st = "aguardando_integracao" if e.code == "integracao_pendente" else "falhou"
        c.execute("UPDATE action_requests SET status=?, external_effects_json=? WHERE id=?",
                  (st, dumps({"executado_externamente": False, "erro": e.message}), rid))
        emit(c, "acao." + st, "politicas", "action_request", rid, req["partner_id"], None, req["correlation_id"], st,
             payload={"action": req["action"]})
        return
    c.execute("UPDATE action_requests SET status='executada', executed_at=?, external_effects_json=? WHERE id=?",
              (iso(), dumps(effects), rid))
    audit(c, actor, "acao.executada", "action_request", rid, None, effects, req["partner_id"], req["correlation_id"])
    emit(c, "acao.executada", "politicas", "action_request", rid, req["partner_id"], None, req["correlation_id"],
         "executada", payload={"action": req["action"]})
    _close_related_pendency(c, rid, actor, "Ação executada")


def _close_related_pendency(c, rid, actor, note):
    rows = c.execute("SELECT id FROM tasks WHERE related_entity=? AND status NOT IN ('concluida','cancelada')",
                     (f"action_request:{rid}",)).fetchall()
    from . import tasks
    for r in rows:
        tasks.update_task(c, actor, r[0], {"status": "concluida"}, note=note, force=True)


def decide_request(rid, approve: bool, actor: Principal, note=""):
    actor.require("approvals.decide")
    with R.db.tx() as c:
        req = c.execute("SELECT * FROM action_requests WHERE id=?", (rid,)).fetchone()
        if not req:
            raise NotFound("Solicitação não encontrada")
        req = dict(req)
        if req["status"] != "aguardando_aprovacao":
            raise Conflict(f"Solicitação está em '{req['status']}'.", "estado_invalido")
        if req["requested_by"] == actor.id:
            raise Forbidden("Quem solicitou não pode aprovar a própria solicitação.", "segregacao_funcoes")
        pol = c.execute("SELECT approver_role FROM policies WHERE action=?", (req["action"],)).fetchone()
        if pol and pol[0] and actor.role not in (pol[0], "admin_central"):
            raise Forbidden(f"Aprovação exige papel {pol[0]}.", "papel_aprovador")
        c.execute("UPDATE action_requests SET status=?, decided_by=?, decided_at=?, reason=COALESCE(reason,'')||? WHERE id=?",
                  ("aprovada" if approve else "rejeitada", actor.id, iso(), f" | decisão: {note}" if note else "", rid))
        audit(c, actor, "acao.aprovada" if approve else "acao.rejeitada", "action_request", rid,
              {"status": req["status"]}, {"note": note}, req["partner_id"], req["correlation_id"])
        if approve:
            _execute(c, rid, actor)
        else:
            emit(c, "acao.rejeitada", "politicas", "action_request", rid, req["partner_id"], None, req["correlation_id"], "rejeitada")
            _close_related_pendency(c, rid, actor, "Solicitação rejeitada")
    return R.db.one("SELECT * FROM action_requests WHERE id=?", rid)


def cancel_request(rid, actor: Principal):
    """Cancelar NÃO desfaz efeitos externos: devolve o que já aconteceu e as compensações possíveis."""
    with R.db.tx() as c:
        req = c.execute("SELECT * FROM action_requests WHERE id=?", (rid,)).fetchone()
        if not req:
            raise NotFound("Solicitação não encontrada")
        req = dict(req)
        if req["status"] == "executada":
            effects = loads(req["external_effects_json"]) or {}
            comp = {"reembolso.executar": "Reembolso executado não é revertido automaticamente; a compensação é "
                                          "uma nova cobrança autorizada pelo cliente.",
                    }.get(req["action"], "Avaliar ação compensatória manual.")
            c.execute("UPDATE action_requests SET compensation_options=? WHERE id=?", (comp, rid))
            audit(c, actor, "acao.cancelamento_sem_efeito", "action_request", rid)
            return {"cancelada": False, "ja_executado": effects, "compensacoes_possiveis": comp}
        c.execute("UPDATE action_requests SET status='cancelada', decided_by=?, decided_at=? WHERE id=?", (actor.id, iso(), rid))
        audit(c, actor, "acao.cancelada", "action_request", rid)
        _close_related_pendency(c, rid, actor, "Solicitação cancelada")
        return {"cancelada": True, "ja_executado": None, "compensacoes_possiveis": None}


def upsert_policy(actor: Principal, action, mode, limit_minor=None, currency=None, approver_role=None, description=None):
    actor.require("policies.write")
    if mode not in ("automatico", "revisao", "proibido"):
        raise AppError("Modo inválido")
    with R.db.tx() as c:
        before = c.execute("SELECT * FROM policies WHERE action=?", (action,)).fetchone()
        c.execute("INSERT INTO policies(action,mode,limit_minor,currency,approver_role,description,updated_by,updated_at) "
                  "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(action) DO UPDATE SET mode=excluded.mode, limit_minor=excluded.limit_minor,"
                  " currency=excluded.currency, approver_role=excluded.approver_role, description=excluded.description,"
                  " updated_by=excluded.updated_by, updated_at=excluded.updated_at",
                  (action, mode, limit_minor, currency, approver_role, description or KNOWN_ACTIONS.get(action), actor.id, iso()))
        audit(c, actor, "politica.alterada", "policy", action, dict(before) if before else None,
              {"mode": mode, "limit_minor": limit_minor, "currency": currency, "approver_role": approver_role})
