"""Tarefas e pendências: estados, dependências, recorrência no servidor, comentários, anexos e histórico."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from .core import R, AppError, Conflict, NotFound, Principal, audit, emit
from .db import dumps, iso, loads, new_id, now, parse_iso

STATES = ("a_fazer", "em_andamento", "aguardando_terceiro", "aguardando_aprovacao", "concluida", "cancelada")
OPEN_STATES = ("a_fazer", "em_andamento", "aguardando_terceiro", "aguardando_aprovacao")
PRIORITIES = ("baixa", "media", "alta", "critica")
RECURRENCE = {"diaria": timedelta(days=1), "semanal": timedelta(days=7), "mensal": None}
FIELDS = ("title", "description", "partner_id", "unit_id", "assignee_type", "assignee_id", "priority", "due_at",
          "status", "kind", "pendency_type", "source", "related_entity", "recurrence_rule", "template")

# Modelos de checklist para serviços de TI (editáveis aqui; cada tarefa guarda a sua própria cópia)
TEMPLATES = {
    "preventiva_ti": {"title": "Visita preventiva de TI", "items": [
        "Verificar estado dos computadores e periféricos", "Conferir atualizações do sistema e antivírus",
        "Testar rede, Wi-Fi e internet (velocidade registrada)", "Verificar backup mais recente e testar restauração de 1 arquivo",
        "Checar impressoras e suprimentos", "Revisar nobreak/energia e cabeamento", "Registrar pendências e próximos passos com o responsável"]},
    "chamado_ti": {"title": "Atendimento de chamado", "items": [
        "Registrar sintoma relatado e quem abriu", "Diagnóstico e causa identificada", "Solução aplicada",
        "Teste com o usuário e aceite", "Registrar tempo gasto e peças/licenças usadas"]},
    "backup": {"title": "Verificação de backup", "items": [
        "Backup do dia concluído sem erros", "Cópia externa/nuvem atualizada", "Teste de restauração realizado", "Espaço disponível adequado"]},
    "inventario": {"title": "Inventário de equipamentos", "items": [
        "Listar computadores (patrimônio, usuário, sistema)", "Listar rede (roteador, switches, APs)", "Listar licenças e vencimentos",
        "Fotografar etiquetas/patrimônio", "Atualizar planilha/inventário"]},
    "implantacao": {"title": "Implantação de nova unidade", "items": [
        "Levantamento do local e pontos de rede", "Instalação de internet e roteador", "Configuração de estações e impressoras",
        "Contas de e-mail e acessos criados", "Treinamento rápido dos usuários", "Documentação entregue ao responsável"]},
}


def checklist_from(template):
    t = TEMPLATES.get(template)
    return [{"item": i, "done": False, "by": None, "at": None} for i in t["items"]] if t else None


def _next_occurrence(rule, base):
    if rule == "mensal":
        m = base.month + 1
        y = base.year + (m > 12)
        m = (m - 1) % 12 + 1
        day = min(base.day, 28)
        return base.replace(year=y, month=m, day=day)
    return base + RECURRENCE[rule]


def _validate(c, data, actor: Principal):
    if "title" in data and not (data["title"] or "").strip():
        raise AppError("Título obrigatório")
    if data.get("status") and data["status"] not in STATES:
        raise AppError("Status inválido")
    if data.get("priority") and data["priority"] not in PRIORITIES:
        raise AppError("Prioridade inválida")
    if data.get("recurrence_rule") and data["recurrence_rule"] not in RECURRENCE:
        raise AppError("Recorrência inválida (diaria, semanal, mensal)")
    if data.get("template") and data["template"] not in TEMPLATES:
        raise AppError("Modelo de checklist inexistente")
    if data.get("partner_id") and not _own_only(actor):  # técnico já foi checado pela atribuição
        actor.require_partner(data["partner_id"])
    if data.get("unit_id"):
        u = c.execute("SELECT partner_id FROM partner_units WHERE id=?", (data["unit_id"],)).fetchone()
        if not u or (data.get("partner_id") and u[0] != data["partner_id"]):
            raise AppError("Unidade não pertence ao parceiro")
    at = data.get("assignee_type")
    if at == "agente":
        if not c.execute("SELECT 1 FROM agents WHERE id=?", (data.get("assignee_id"),)).fetchone():
            raise AppError("Agente inexistente")
    elif at == "humano":
        u = c.execute("SELECT partner_id, role, active FROM users WHERE id=?", (data.get("assignee_id"),)).fetchone()
        if not u:
            raise AppError("Responsável inexistente")
        if not u["active"]:
            raise AppError("Responsável ainda não autorizado/ativo", "responsavel_inativo")
        pid = data.get("partner_id")
        if u["partner_id"] and pid and u["partner_id"] != pid:
            raise AppError("Responsável não pertence a este parceiro")


def create_task(c, actor: Principal, data: dict, depends_on=()):
    if actor.kind == "user":
        actor.require("tasks.write", data.get("partner_id"))
        if not actor.is_central and not data.get("partner_id"):
            data["partner_id"] = actor.partner_id
    if not data.get("title") and data.get("template") in TEMPLATES:
        data["title"] = TEMPLATES[data["template"]]["title"]  # título vem do modelo de checklist
    _validate(c, data, actor)
    tid = new_id("tsk")
    ts = iso()
    row = {k: data.get(k) for k in FIELDS}
    if row["template"] and not row["title"]:
        row["title"] = TEMPLATES[row["template"]]["title"]
    checklist = data.get("checklist") or (checklist_from(row["template"]) if row["template"] else None)
    row["status"] = row["status"] or "a_fazer"
    row["priority"] = row["priority"] or "media"
    row["kind"] = row["kind"] or "tarefa"
    rec_next = None
    if row["recurrence_rule"]:
        rec_next = data.get("recurrence_start") or ts
    c.execute(
        "INSERT INTO tasks(id,title,description,partner_id,unit_id,assignee_type,assignee_id,priority,due_at,status,kind,"
        "pendency_type,source,related_entity,recurrence_rule,recurrence_next_at,recurrence_parent_id,created_by,created_at,updated_at,is_demo,"
        "template,checklist_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, row["title"], row["description"], row["partner_id"], row["unit_id"], row["assignee_type"], row["assignee_id"],
         row["priority"], row["due_at"], row["status"], row["kind"], row["pendency_type"], row["source"] or actor.kind,
         row["related_entity"], row["recurrence_rule"], rec_next, data.get("recurrence_parent_id"), actor.label, ts, ts,
         1 if R.settings.is_demo else 0, row["template"], dumps(checklist) if checklist else None))
    for d in depends_on or ():
        if not c.execute("SELECT 1 FROM tasks WHERE id=?", (d,)).fetchone():
            raise AppError(f"Dependência inexistente: {d}")
        c.execute("INSERT INTO task_dependencies(task_id,depends_on_id) VALUES (?,?)", (tid, d))
    c.execute("INSERT INTO task_history(task_id,at,actor,field,old,new) VALUES (?,?,?,?,?,?)",
              (tid, ts, actor.label, "criada", None, row["status"]))
    audit(c, actor, "tarefa.criada", "task", tid, None, {"title": row["title"], "status": row["status"]}, row["partner_id"])
    emit(c, "pendencia.criada" if row["kind"] == "pendencia" else "tarefa.criada", "tarefas", "task", tid,
         row["partner_id"], None, None, row["status"],
         payload={"title": row["title"], "kind": row["kind"], "priority": row["priority"], "unit_id": row["unit_id"],
                  "assignee_type": row["assignee_type"], "assignee_id": row["assignee_id"]})
    return tid


def _own_only(actor):
    return actor.kind == "user" and actor.can("tasks.own_only") and not actor.can("*")


def _check_access(actor, t, perm):
    if _own_only(actor):  # técnico/freelancer: somente tarefas atribuídas a ele
        actor.require(perm)
        if not (t["assignee_type"] == "humano" and t["assignee_id"] == actor.id):
            raise NotFound("Tarefa não encontrada")
        return
    actor.require(perm, t["partner_id"])
    if t["partner_id"] is None and not actor.is_central:
        raise NotFound("Tarefa não encontrada")


def _names():
    n = {f"user:{r['id']}": r["name"] for r in R.db.all("SELECT id, name FROM users")}
    n.update({f"agent:{r['id']}": r["name"] for r in R.db.all("SELECT id, name FROM agents")})
    return n


def _label(names, kind, ident):
    if not ident:
        return None
    return names.get(f"{'agent' if kind == 'agente' else 'user'}:{ident}", ident)


def get_task(task_id, actor: Principal):
    t = R.db.one("SELECT * FROM tasks WHERE id=?", task_id)
    if not t:
        raise NotFound("Tarefa não encontrada")
    _check_access(actor, t, "tasks.read")
    t["checklist"] = loads(t.pop("checklist_json"))
    names = _names()
    t["assignee_name"] = _label(names, t["assignee_type"], t["assignee_id"])
    for i in t["checklist"] or []:
        i["by_name"] = names.get(i["by"], i["by"]) if i.get("by") else None
    t["completed_by_name"] = names.get(t["completed_by"], t["completed_by"]) if t["completed_by"] else None
    t["depends_on"] = [r["depends_on_id"] for r in R.db.all("SELECT depends_on_id FROM task_dependencies WHERE task_id=?", task_id)]
    t["comments"] = R.db.all("SELECT * FROM task_comments WHERE task_id=? ORDER BY created_at", task_id)
    t["attachments"] = R.db.all("SELECT id,filename,mime,size,sha256,uploaded_by,is_evidence,created_at FROM attachments "
                                "WHERE entity_type='task' AND entity_id=? ORDER BY created_at", task_id)
    t["history"] = R.db.all("SELECT * FROM task_history WHERE task_id=? ORDER BY id", task_id)
    t["overdue"] = is_overdue(t)
    return t


def is_overdue(t):
    return bool(t.get("due_at") and t["status"] in OPEN_STATES and parse_iso(t["due_at"]) < now())


def update_task(c, actor: Principal, task_id, changes: dict, note=None, force=False):
    t = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not t:
        raise NotFound("Tarefa não encontrada")
    t = dict(t)
    if actor.kind == "user":
        _check_access(actor, t, "tasks.write")
    changes = {k: v for k, v in changes.items() if k in FIELDS and k not in ("partner_id",)}
    if _own_only(actor):  # técnico só altera status e comenta; não reatribui nem muda prazos
        changes = {k: v for k, v in changes.items() if k == "status"}
    merged = {**t, **changes}
    _validate(c, {**changes, "partner_id": t["partner_id"], **({"assignee_type": merged["assignee_type"],
              "assignee_id": merged["assignee_id"]} if ("assignee_id" in changes or "assignee_type" in changes) else {})}, actor)
    new_status = changes.get("status")
    if new_status and new_status != t["status"]:
        if t["status"] in ("concluida", "cancelada") and not force:
            raise Conflict("Tarefa encerrada não pode mudar de estado.", "tarefa_encerrada")
        if new_status in ("em_andamento", "concluida") and not force:
            pend = c.execute("SELECT d.depends_on_id FROM task_dependencies d JOIN tasks x ON x.id=d.depends_on_id "
                             "WHERE d.task_id=? AND x.status!='concluida'", (task_id,)).fetchall()
            if pend:
                raise Conflict("Dependências não concluídas: " + ", ".join(p[0] for p in pend), "dependencia_pendente")
        if new_status == "concluida" and t["status"] == "aguardando_aprovacao" and not force and t["related_entity"]:
            raise Conflict("Pendência de aprovação só é concluída pela decisão da solicitação.", "decisao_necessaria")
        cl = loads(t.get("checklist_json"))
        if new_status == "concluida" and cl and not force and not all(i["done"] for i in cl):
            falta = sum(1 for i in cl if not i["done"])
            raise Conflict(f"Checklist incompleto: {falta} item(ns) pendente(s).", "checklist_incompleto")
    ts = iso()
    sets, vals = [], []
    for k, v in changes.items():
        if t.get(k) != v:
            sets.append(f"{k}=?")
            vals.append(v)
            c.execute("INSERT INTO task_history(task_id,at,actor,field,old,new) VALUES (?,?,?,?,?,?)",
                      (task_id, ts, actor.label, k, None if t.get(k) is None else str(t.get(k)), None if v is None else str(v)))
    if new_status == "concluida" and t["status"] != "concluida":
        sets += ["completed_by=?", "completed_at=?"]
        vals += [actor.label, ts]
    if not sets and not note:
        return t
    if sets:
        c.execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at=? WHERE id=?", (*vals, ts, task_id))
    if new_status == "concluida" and t["status"] != "concluida":
        from . import pontos
        pontos.award(c, t)   # cashback do cliente: uma vez por serviço
    if note:
        c.execute("INSERT INTO task_comments(id,task_id,author,body,created_at) VALUES (?,?,?,?,?)",
                  (new_id("cmt"), task_id, actor.label, note, ts))
    audit(c, actor, "tarefa.atualizada", "task", task_id, {k: t.get(k) for k in changes}, changes, t["partner_id"])
    emit(c, "tarefa.atualizada", "tarefas", "task", task_id, t["partner_id"], None, None,
         changes.get("status", t["status"]),
         payload={"title": t["title"], "kind": t["kind"], "changes": sorted(changes), "unit_id": t.get("unit_id"), "assignee_type": merged["assignee_type"],
                  "assignee_id": merged["assignee_id"]})
    return merged


def set_checklist_item(actor: Principal, task_id, index, done):
    with R.db.tx() as c:
        t = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise NotFound("Tarefa não encontrada")
        t = dict(t)
        _check_access(actor, t, "tasks.write")
        if t["status"] in ("concluida", "cancelada"):
            raise Conflict("Tarefa encerrada", "tarefa_encerrada")
        cl = loads(t["checklist_json"]) or []
        if not isinstance(index, int) or not 0 <= index < len(cl):
            raise AppError("Item inexistente")
        cl[index].update(done=bool(done), by=actor.label if done else None, at=iso() if done else None)
        c.execute("UPDATE tasks SET checklist_json=?, updated_at=? WHERE id=?", (dumps(cl), iso(), task_id))
        c.execute("INSERT INTO task_history(task_id,at,actor,field,old,new) VALUES (?,?,?,?,?,?)",
                  (task_id, iso(), actor.label, "checklist", cl[index]["item"][:80], "feito" if done else "desfeito"))
        if t["status"] == "a_fazer" and done:
            c.execute("UPDATE tasks SET status='em_andamento' WHERE id=?", (task_id,))
        emit(c, "tarefa.checklist", "tarefas", "task", task_id, t["partner_id"], status="feito" if done else "desfeito",
             payload={"feitos": sum(1 for i in cl if i["done"]), "total": len(cl)})
    return cl


def add_comment(actor: Principal, task_id, body):
    if not (body or "").strip():
        raise AppError("Comentário vazio")
    with R.db.tx() as c:
        t = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not t:
            raise NotFound("Tarefa não encontrada")
        _check_access(actor, dict(t), "tasks.write")
        t = (t["partner_id"],)
        cid = new_id("cmt")
        c.execute("INSERT INTO task_comments(id,task_id,author,body,created_at) VALUES (?,?,?,?,?)",
                  (cid, task_id, actor.label, body[:4000], iso()))
        emit(c, "tarefa.comentario", "tarefas", "task", task_id, t[0])
    return cid


ALLOWED_MIME = {"application/pdf", "image/png", "image/jpeg", "text/plain", "text/csv",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
MAX_UPLOAD = 10 * 1024 * 1024


def add_attachment(actor: Principal, entity_type, entity_id, filename, mime, content: bytes, is_evidence=False):
    if len(content) > MAX_UPLOAD:
        raise AppError("Arquivo acima de 10 MB")
    if mime not in ALLOWED_MIME:
        raise AppError(f"Tipo de arquivo não permitido: {mime}")
    if entity_type != "task":
        raise AppError("Anexos suportados apenas em tarefas nesta etapa")
    with R.db.tx() as c:
        t = c.execute("SELECT * FROM tasks WHERE id=?", (entity_id,)).fetchone()
        if not t:
            raise NotFound("Tarefa não encontrada")
        _check_access(actor, dict(t), "tasks.write")
        t = (t["partner_id"],)
        sha = hashlib.sha256(content).hexdigest()
        aid = new_id("anx")
        d = R.settings.uploads_dir
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{aid}.bin"  # nome interno; o nome original fica só nos metadados
        path.write_bytes(content)
        safe_name = "".join(ch for ch in (filename or "arquivo") if ch.isalnum() or ch in "._- ")[:120] or "arquivo"
        c.execute("INSERT INTO attachments(id,entity_type,entity_id,partner_id,filename,mime,size,sha256,storage_path,uploaded_by,"
                  "is_evidence,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (aid, entity_type, entity_id, t[0], safe_name, mime, len(content), sha, str(path), actor.label,
                   1 if is_evidence else 0, iso()))
        c.execute("INSERT INTO task_history(task_id,at,actor,field,old,new) VALUES (?,?,?,?,?,?)",
                  (entity_id, iso(), actor.label, "anexo", None, f"{safe_name} ({sha[:12]})"))
        audit(c, actor, "anexo.adicionado", "attachment", aid, None, {"sha256": sha, "task": entity_id}, t[0])
        emit(c, "tarefa.anexo", "tarefas", "task", entity_id, t[0], payload={"evidencia": bool(is_evidence)})
    return aid


def list_tasks(actor: Principal, partner_id=None, status=None, assignee_id=None, kind=None, overdue=None, q=None, limit=300):
    actor.require("tasks.read")
    scope = actor.partner_scope()
    sql = "SELECT * FROM tasks WHERE (recurrence_rule IS NULL OR recurrence_parent_id IS NOT NULL)"
    args = []
    if _own_only(actor):
        sql += " AND assignee_type='humano' AND assignee_id=?"
        args.append(actor.id)
        scope = None
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
    if assignee_id:
        sql += " AND assignee_id=?"
        args.append(assignee_id)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    if q:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY CASE status WHEN 'concluida' THEN 2 WHEN 'cancelada' THEN 3 ELSE 1 END, " \
           "CASE priority WHEN 'critica' THEN 0 WHEN 'alta' THEN 1 WHEN 'media' THEN 2 ELSE 3 END, due_at LIMIT ?"
    args.append(limit)
    rows = R.db.all(sql, *args)
    names = _names()
    for r in rows:
        r["overdue"] = is_overdue(r)
        r["assignee_name"] = _label(names, r["assignee_type"], r["assignee_id"])
        r.pop("checklist_json", None)
    if overdue:
        rows = [r for r in rows if r["overdue"]]
    return rows


def list_series(actor: Principal):
    actor.require("tasks.read")
    scope = actor.partner_scope()
    if scope:
        return R.db.all("SELECT * FROM tasks WHERE recurrence_rule IS NOT NULL AND recurrence_parent_id IS NULL AND partner_id=?", scope)
    return R.db.all("SELECT * FROM tasks WHERE recurrence_rule IS NOT NULL AND recurrence_parent_id IS NULL")


def process_recurrences():
    """Executado pelo agendador do servidor (independe do painel aberto)."""
    created = []
    with R.db.tx() as c:
        series = c.execute("SELECT * FROM tasks WHERE recurrence_rule IS NOT NULL AND recurrence_parent_id IS NULL "
                           "AND status NOT IN ('cancelada') AND recurrence_next_at <= ?", (iso(),)).fetchall()
        for s in series:
            s = dict(s)
            occ = parse_iso(s["recurrence_next_at"])
            due = occ + timedelta(hours=20)
            if s["assignee_type"] == "humano" and not c.execute("SELECT active FROM users WHERE id=?", (s["assignee_id"],)).fetchone()[0]:
                s["assignee_type"], s["assignee_id"] = None, None  # responsável desativado: a tarefa nasce sem responsável
            tid = create_task(c, Principal("system", "recorrencia"), {
                "title": f"{s['title']} — {occ.strftime('%d/%m')}", "description": s["description"],
                "partner_id": s["partner_id"], "unit_id": s["unit_id"], "assignee_type": s["assignee_type"],
                "assignee_id": s["assignee_id"], "priority": s["priority"], "due_at": iso(due), "kind": s["kind"],
                "source": "recorrencia", "recurrence_parent_id": s["id"], "template": s.get("template")})
            nxt = _next_occurrence(s["recurrence_rule"], occ)
            while nxt <= now():
                nxt = _next_occurrence(s["recurrence_rule"], nxt)
            c.execute("UPDATE tasks SET recurrence_next_at=? WHERE id=?", (iso(nxt), s["id"]))
            created.append(tid)
    return created
