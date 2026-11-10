"""CEO de cada base/filial: organiza e administra as tarefas dos lugares que a base atende.

Determinístico e grátis (sem IA paga). A cada volta:
1. lê as unidades atendidas pela base, as tarefas abertas, as atrasadas e a carga de cada técnico ativo da base;
2. cria a preventiva mensal das unidades que estão sem nenhuma (tarefa a fazer, sem responsável);
3. propõe a distribuição das tarefas sem responsável entre os técnicos da base (menor carga primeiro).
   Atribuir trabalho a pessoas segue a política "tarefas.atribuir": sem política → UMA solicitação de aprovação
   por volta com a lista inteira (sem enxurrada de pendências); política automática → aplica direto;
4. encaminha à central o que está atrasado há mais de 2 dias (uma pendência por tarefa, sem duplicar);
5. registra o resumo da base (aparece no painel da base e no mapa).
"""
from __future__ import annotations

from datetime import timedelta

from .core import R, Principal, emit
from .db import dumps, iso, loads, now, parse_iso
from . import policies, tasks

PREFIX = "base_"
CONFIG = {
    "ferramentas": ["tarefas.listar", "tarefas.criar (preventiva mensal)", "tarefas.atribuir (via política)", "pendencias.encaminhar"],
    "dados_permitidos": ["tarefas e unidades atendidas pela própria base", "técnicos da base"],
    "dados_proibidos": ["dados de outras bases", "dados financeiros de clientes"],
    "limites": "Não envia mensagens nem gasta. Atribuir tarefas a pessoas depende da política 'tarefas.atribuir' (sem política: aprovação).",
    "gatilhos": ["agendamento", "execução manual"], "frequencia": "a cada 30 min", "orcamento": "R$ 0 (determinístico)",
    "encaminhamento_humano": "Atrasos acima de 2 dias e qualquer atribuição sem política automática.", "freq_min": 30,
}
policies.KNOWN_ACTIONS["tarefas.atribuir"] = "Atribuir tarefas a técnicos (proposta do CEO da base)"


def code_of(base_id):
    return PREFIX + base_id


def ensure_base_ceos(c):
    for b in c.execute("SELECT id, name, active FROM service_bases").fetchall():
        code = code_of(b["id"])
        row = c.execute("SELECT enabled FROM agents WHERE code=?", (code,)).fetchone()
        if not row:
            c.execute("INSERT INTO agents(id,code,name,role,enabled,status,status_since,config_json,budget_month_minor,budget_currency,next_run_at)"
                      " VALUES (?,?,?,?,?,'disponivel',?,?,0,'BRL',?)",
                      (f"agt_{code}", code, f"CEO da base · {b['name']}", "CEO da base", 1 if b["active"] else 0, iso(), dumps(CONFIG),
                       iso(now() + timedelta(minutes=1))))
        elif row["enabled"] != (1 if b["active"] else 0):
            c.execute("UPDATE agents SET enabled=? WHERE code=?", (1 if b["active"] else 0, code))


@policies.executor("tarefas.atribuir")
def _apply_assignments(c, payload, req, actor):
    done = []
    for a in payload.get("atribuicoes", []):
        t = c.execute("SELECT status, assignee_id FROM tasks WHERE id=?", (a["task_id"],)).fetchone()
        u = c.execute("SELECT active FROM users WHERE id=? AND role='tecnico'", (a["user_id"],)).fetchone()
        if not t or t["assignee_id"] or t["status"] in ("concluida", "cancelada") or not u or not u["active"]:
            continue                      # mudou desde a proposta: pula, sem forçar
        tasks.update_task(c, actor, a["task_id"], {"assignee_type": "humano", "assignee_id": a["user_id"]},
                          note=f"Atribuída pelo CEO da base ({payload.get('base')}) — carga equilibrada.")
        done.append(a["task_id"])
    return {"atribuidas": done}


def _base_of(run):
    return run.agent["code"][len(PREFIX):]


def r_base(run):
    c, bid = run.c, _base_of(run)
    base = c.execute("SELECT * FROM service_bases WHERE id=?", (bid,)).fetchone()
    if not base:
        return run.finish("falhou", "Base não encontrada.")
    units = [dict(u) for u in c.execute("SELECT u.*, p.name partner_name FROM partner_units u JOIN partners p ON p.id=u.partner_id "
                                        "WHERE u.base_id=? AND u.active=1", (bid,)).fetchall()]
    uids = [u["id"] for u in units]
    techs = [dict(t) for t in c.execute("SELECT id, name FROM users WHERE role='tecnico' AND active=1 AND base_id=?", (bid,)).fetchall()]
    ph = ",".join("?" * len(uids)) or "''"
    open_t = [dict(t) for t in c.execute(f"SELECT * FROM tasks WHERE unit_id IN ({ph}) AND status NOT IN ('concluida','cancelada')", uids).fetchall()]
    run.tool("tarefas.listar", f"{len(units)} unidades", f"{len(open_t)} abertas · {len(techs)} técnicos ativos")

    # 1. preventiva mensal
    since = iso(now() - timedelta(days=30))
    created = []
    for u in units:
        has = c.execute("SELECT 1 FROM tasks WHERE unit_id=? AND template='preventiva_ti' AND (status NOT IN ('concluida','cancelada') "
                        "OR completed_at>=?) LIMIT 1", (u["id"], since)).fetchone()
        if not has:
            tid = tasks.create_task(c, run.actor, {"title": f"Preventiva mensal · {u['name']}", "partner_id": u["partner_id"], "unit_id": u["id"],
                                                   "template": "preventiva_ti", "priority": "media", "source": "ceo_base",
                                                   "due_at": iso(now() + timedelta(days=7)),
                                                   "description": "Agendada pelo CEO da base: unidade sem preventiva nos últimos 30 dias."})
            created.append(tid)
            open_t.append(dict(c.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()))
    run.tool("tarefas.criar", "preventivas do mês", f"{len(created)} criada(s)")

    # 2. distribuição por carga (proposta → política)
    load = {t["id"]: sum(1 for x in open_t if x["assignee_type"] == "humano" and x["assignee_id"] == t["id"]) for t in techs}
    pend_req = c.execute("SELECT 1 FROM action_requests WHERE action='tarefas.atribuir' AND status='aguardando_aprovacao' "
                         "AND payload_json LIKE ?", (f'%"base_id": "{bid}"%',)).fetchone()
    proposals = []
    if techs and not pend_req:
        prio = {"critica": 0, "alta": 1, "media": 2, "baixa": 3}
        free = sorted([t for t in open_t if not t["assignee_id"] and t["status"] == "a_fazer"],
                      key=lambda t: (prio.get(t["priority"], 3), t["due_at"] or "9999"))
        names = {t["id"]: t["name"] for t in techs}
        for t in free:
            uid = min(load, key=lambda k: (load[k], names[k]))
            load[uid] += 1
            proposals.append({"task_id": t["id"], "titulo": t["title"], "user_id": uid, "tecnico": names[uid]})
    mode = None
    if proposals:
        _, mode = policies.request_action(c, "tarefas.atribuir", {"base_id": bid, "base": base["name"], "atribuicoes": proposals}, run.actor,
                                          reason=f"CEO da base {base['name']}: {len(proposals)} tarefa(s) sem responsável")
    run.tool("tarefas.atribuir", f"{len(proposals)} proposta(s)",
             ("aplicadas (política automática)" if mode == "automatico" else "aguardando sua aprovação") if proposals
             else ("já há proposta aguardando aprovação" if pend_req else ("sem técnicos ativos na base" if not techs else "nada a distribuir")))

    # 3. atrasos > 2 dias → central
    esc = []
    for t in open_t:
        if t["due_at"] and parse_iso(t["due_at"]) < now() - timedelta(days=2) and t["kind"] == "tarefa":
            ref = f"task:{t['id']}"
            if not c.execute("SELECT 1 FROM tasks WHERE related_entity=? AND status NOT IN ('concluida','cancelada')", (ref,)).fetchone():
                tasks.create_task(c, run.actor, {"title": f"Atraso na base {base['name']}: {t['title']}", "partner_id": t["partner_id"],
                                                 "kind": "pendencia", "pendency_type": "prazo_vencido", "priority": "alta",
                                                 "source": "ceo_base", "related_entity": ref,
                                                 "description": "Mais de 2 dias de atraso. Encaminhado pelo CEO da base para decisão."})
                esc.append(t["id"])
    overdue = [t for t in open_t if tasks.is_overdue(t)]
    done30 = c.execute(f"SELECT COUNT(*) FROM tasks WHERE unit_id IN ({ph}) AND status='concluida' AND completed_at>=?", (*uids, since)).fetchone()[0]
    resumo = {"base": base["name"], "unidades": len(units), "tecnicos": len(techs), "abertas": len(open_t), "atrasadas": len(overdue),
              "concluidas_30d": done30, "preventivas_criadas": len(created), "propostas": len(proposals), "modo": mode,
              "encaminhadas": len(esc), "carga": [{"tecnico": t["name"], "abertas": load.get(t["id"], 0)} for t in techs],
              "por_unidade": [{"unidade": u["name"], "cliente": u["partner_name"],
                               "abertas": sum(1 for t in open_t if t["unit_id"] == u["id"]),
                               "atrasadas": sum(1 for t in overdue if t["unit_id"] == u["id"])} for u in units]}
    msg = (f"{len(units)} unidade(s) · {len(open_t)} aberta(s) · {len(overdue)} atrasada(s) · {done30} concluída(s) em 30 dias · "
           f"{len(created)} preventiva(s) agendada(s) · {len(proposals)} atribuição(ões) "
           f"{'aplicadas' if mode == 'automatico' else 'para aprovar' if proposals else ''} · {len(esc)} encaminhada(s).")
    emit(c, "ceo_base.resumo", "ceo_base", "service_base", bid, payload={"abertas": len(open_t), "atrasadas": len(overdue)})
    run.finish("concluido", msg, resumo)


def last_summary(base_id):
    r = R.db.one("SELECT r.summary, r.evidence_json, r.finished_at FROM agent_runs r JOIN agents a ON a.id=r.agent_id "
                 "WHERE a.code=? AND r.status='concluido' ORDER BY r.started_at DESC LIMIT 1", code_of(base_id))
    return {"resumo": r["summary"], "dados": loads(r["evidence_json"]), "em": r["finished_at"]} if r else None
