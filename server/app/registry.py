"""Cadastros da operação real: bases de serviço, clientes, unidades dos clientes e equipe.

Clientes de serviço usam commercial_model='cliente_servico' (não têm loja nem comissão).
Toda alteração é auditada; nada é apagado — registros são desativados.
"""
from __future__ import annotations

import re

from .core import R, AppError, Conflict, NotFound, Principal, audit, emit, hash_password
from .db import iso, new_id

ROLES_ALLOWED = {"admin_central", "operacoes_central", "financeiro_central", "parceiro_admin", "parceiro_operador", "tecnico"}
UF = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO",
      "RR", "SC", "SP", "SE", "TO"}


def _txt(v, n=200, required=False, label="campo"):
    v = " ".join(str(v or "").split())[:n]
    if required and not v:
        raise AppError(f"Informe {label}", "campo_obrigatorio")
    return v or None


def _uf(v):
    v = (v or "").strip().upper()
    if v and v not in UF:
        raise AppError("Estado (UF) inválido", "uf_invalida")
    return v or None


def _money(v):
    if v in (None, ""):
        return None
    try:
        x = round(float(str(v).replace(".", "").replace(",", ".")) * 100) if isinstance(v, str) else round(float(v) * 100)
    except ValueError:
        raise AppError("Valor inválido", "valor_invalido")
    if x < 0:
        raise AppError("Valor não pode ser negativo")
    return x


def overview(actor: Principal):
    actor.require("cadastros.read")
    bases = R.db.all("SELECT * FROM service_bases ORDER BY active DESC, name")
    clients = R.db.all("SELECT * FROM partners WHERE commercial_model='cliente_servico' ORDER BY status, name")
    units = R.db.all("SELECT u.*, p.name partner_name, b.name base_name FROM partner_units u JOIN partners p ON p.id=u.partner_id "
                     "LEFT JOIN service_bases b ON b.id=u.base_id WHERE p.commercial_model='cliente_servico' ORDER BY p.name, u.name")
    users = R.db.all("SELECT u.id, u.name, u.email, u.role, u.partner_id, u.base_id, u.active, p.name partner_name, b.name base_name FROM users u "
                     "LEFT JOIN partners p ON p.id=u.partner_id LEFT JOIN service_bases b ON b.id=u.base_id ORDER BY u.active DESC, u.name")
    return {"bases": bases, "clients": clients, "units": units, "users": users, "roles": sorted(ROLES_ALLOWED)}


# ------------------------------------------------------------------ bases de serviço (suas unidades)
def save_base(actor: Principal, data, base_id=None):
    actor.require("cadastros.write")
    row = {"name": _txt(data.get("name"), 120, True, "o nome da base"), "city": _txt(data.get("city"), 80, True, "a cidade"),
           "state": _uf(data.get("state")) or "SP", "address": _txt(data.get("address"), 240),
           "location_status": "confirmada" if data.get("location_confirmed") and data.get("address") else "nao_confirmada",
           "active": 0 if data.get("active") is False else 1}
    with R.db.tx() as c:
        if base_id:
            before = c.execute("SELECT * FROM service_bases WHERE id=?", (base_id,)).fetchone()
            if not before:
                raise NotFound("Base não encontrada")
            c.execute("UPDATE service_bases SET name=?,city=?,state=?,address=?,location_status=?,active=? WHERE id=?",
                      (row["name"], row["city"], row["state"], row["address"], row["location_status"], row["active"], base_id))
            audit(c, actor, "base.alterada", "service_base", base_id, dict(before), row)
        else:
            base_id = new_id("bas")
            c.execute("INSERT INTO service_bases(id,name,city,state,address,location_status,active,created_at,is_demo) VALUES (?,?,?,?,?,?,?,?,?)",
                      (base_id, row["name"], row["city"], row["state"], row["address"], row["location_status"], row["active"], iso(),
                       1 if R.settings.is_demo else 0))
            audit(c, actor, "base.criada", "service_base", base_id, None, row)
        emit(c, "cadastro.base", "cadastros", "service_base", base_id, status="ativa" if row["active"] else "inativa", payload={"name": row["name"]})
    return base_id


# ------------------------------------------------------------------ clientes
def save_client(actor: Principal, data, client_id=None):
    actor.require("cadastros.write")
    row = {"name": _txt(data.get("name"), 120, True, "o nome do cliente"), "legal_name": _txt(data.get("legal_name"), 160),
           "segment": _txt(data.get("segment"), 80), "contact_name": _txt(data.get("contact_name"), 120),
           "contract_minor": _money(data.get("contract")), "notes": _txt(data.get("notes"), 1000),
           "status": "inativo" if data.get("active") is False else "ativo"}
    with R.db.tx() as c:
        if client_id:
            before = c.execute("SELECT * FROM partners WHERE id=? AND commercial_model='cliente_servico'", (client_id,)).fetchone()
            if not before:
                raise NotFound("Cliente não encontrado")
            c.execute("UPDATE partners SET name=?,legal_name=?,segment=?,contact_name=?,contract_minor=?,notes=?,status=? WHERE id=?",
                      (row["name"], row["legal_name"], row["segment"], row["contact_name"], row["contract_minor"], row["notes"], row["status"], client_id))
            audit(c, actor, "cliente.alterado", "partner", client_id, dict(before), row, client_id)
        else:
            if c.execute("SELECT 1 FROM partners WHERE lower(name)=lower(?)", (row["name"],)).fetchone():
                raise Conflict("Já existe um cliente com esse nome", "duplicado")
            client_id = new_id("cli")
            c.execute("INSERT INTO partners(id,name,legal_name,region_code,commercial_model,commission_rate_bp,status,contact_name,segment,"
                      "contract_minor,notes,created_at,is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (client_id, row["name"], row["legal_name"], "BR", "cliente_servico", None, row["status"], row["contact_name"],
                       row["segment"], row["contract_minor"], row["notes"], iso(), 1 if R.settings.is_demo else 0))
            for code, name, cat, kind, st, note in [
                ("tarefas", "Tarefas e checklists de TI", "estoque", "modulo_interno", "ativo", None),
                ("documentos", "Documentos e evidências", "documentos", "modulo_interno", "ativo", None),
                ("financeiro", "Fechamento mensal de serviços", "financeiro", "modulo_interno", "ativo", None),
                ("whatsapp", "WhatsApp Business", "comunicacao", "planejada", "planejado", "Requer conta Meta."),
                ("helpdesk", "Abertura de chamados pelo cliente", "atendimento", "planejada", "planejado", "Próxima etapa.")]:
                c.execute("INSERT INTO integrations(id,partner_id,code,name,category,kind,status,critical,note,last_sync_at) VALUES (?,?,?,?,?,?,?,0,?,?)",
                          (new_id("app"), client_id, code, name, cat, kind, st, note, iso() if kind == "modulo_interno" else None))
            from .ceo import ensure_ceos
            ensure_ceos(c)
            audit(c, actor, "cliente.criado", "partner", client_id, None, row, client_id)
        emit(c, "cadastro.cliente", "cadastros", "partner", client_id, client_id, "BR", status=row["status"], payload={"name": row["name"]})
    return client_id


# ------------------------------------------------------------------ unidades dos clientes
def save_unit(actor: Principal, data, unit_id=None):
    actor.require("cadastros.write")
    pid = data.get("client_id")
    row = {"name": _txt(data.get("name"), 120, True, "o nome da unidade"), "city": _txt(data.get("city"), 80, True, "a cidade"),
           "state": _uf(data.get("state")) or "SP", "address": _txt(data.get("address"), 240), "base_id": data.get("base_id") or None,
           "active": 0 if data.get("active") is False else 1}
    confirmed = bool(data.get("location_confirmed") and row["address"])
    with R.db.tx() as c:
        if row["base_id"] and not c.execute("SELECT 1 FROM service_bases WHERE id=?", (row["base_id"],)).fetchone():
            raise AppError("Base de atendimento inexistente")
        if unit_id:
            before = c.execute("SELECT * FROM partner_units WHERE id=?", (unit_id,)).fetchone()
            if not before:
                raise NotFound("Unidade não encontrada")
            pid = before["partner_id"]
            c.execute("UPDATE partner_units SET name=?,city=?,state=?,address=?,base_id=?,active=?,location_status=?,location_source=? WHERE id=?",
                      (row["name"], row["city"], row["state"], row["address"], row["base_id"], row["active"],
                       "confirmada" if confirmed else "nao_confirmada", f"Endereço confirmado por {actor.name or actor.id}" if confirmed else None, unit_id))
            audit(c, actor, "unidade.alterada", "unit", unit_id, dict(before), row, pid)
        else:
            if not c.execute("SELECT 1 FROM partners WHERE id=? AND commercial_model='cliente_servico'", (pid,)).fetchone():
                raise NotFound("Cliente não encontrado")
            unit_id = new_id("uni")
            c.execute("INSERT INTO partner_units(id,partner_id,name,country,state,city,address,location_status,location_source,base_id,active,is_demo)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (unit_id, pid, row["name"], "BR", row["state"], row["city"], row["address"], "confirmada" if confirmed else "nao_confirmada",
                       f"Endereço confirmado por {actor.name or actor.id}" if confirmed else None, row["base_id"], row["active"],
                       1 if R.settings.is_demo else 0))
            audit(c, actor, "unidade.criada", "unit", unit_id, None, row, pid)
        emit(c, "cadastro.unidade", "cadastros", "unit", unit_id, pid, "BR", status="ativa" if row["active"] else "inativa",
             payload={"name": row["name"], "city": row["city"]})
    return unit_id


# ------------------------------------------------------------------ equipe e acessos
def save_user(actor: Principal, data, user_id=None):
    actor.require("cadastros.write")
    role = data.get("role")
    if role not in ROLES_ALLOWED:
        raise AppError("Papel inválido")
    pid = data.get("partner_id") or None
    if role.startswith("parceiro_") and not pid:
        raise AppError("Acesso de cliente precisa estar vinculado a um cliente")
    if not role.startswith("parceiro_"):
        pid = None
    base_id = data.get("base_id") or None
    if role == "tecnico" and not base_id:
        raise AppError("Técnico/freelancer precisa estar vinculado a uma base (filial)")
    email = (data.get("email") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise AppError("E-mail inválido")
    name = _txt(data.get("name"), 120, True, "o nome")
    pw = data.get("password") or ""
    with R.db.tx() as c:
        if user_id:
            before = c.execute("SELECT id,email,name,role,partner_id,active FROM users WHERE id=?", (user_id,)).fetchone()
            if not before:
                raise NotFound("Usuário não encontrado")
            if user_id == actor.id and (data.get("active") is False or role != before["role"]):
                raise AppError("Você não pode desativar nem rebaixar o seu próprio acesso")
            c.execute("UPDATE users SET name=?, email=?, role=?, partner_id=?, base_id=?, active=? WHERE id=?",
                      (name, email, role, pid, base_id, 0 if data.get("active") is False else 1, user_id))
            if pw:
                if len(pw) < 10:
                    raise AppError("Senha precisa de pelo menos 10 caracteres")
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(pw), user_id))
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            if data.get("active") is False:
                c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
            audit(c, actor, "usuario.alterado", "user", user_id, dict(before), {"role": role, "partner_id": pid, "senha_trocada": bool(pw)})
        else:
            if len(pw) < 10:
                raise AppError("Senha inicial precisa de pelo menos 10 caracteres")
            if c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise Conflict("E-mail já cadastrado", "duplicado")
            user_id = new_id("usr")
            # freelancer entra PENDENTE: só trabalha depois de autorizado (ativar) pelo administrador
            active = 0 if role == "tecnico" and not data.get("authorize_now") else (0 if data.get("active") is False else 1)
            c.execute("INSERT INTO users(id,email,name,password_hash,role,partner_id,base_id,active,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (user_id, email, name, hash_password(pw), role, pid, base_id, active, iso()))
            audit(c, actor, "usuario.criado", "user", user_id, None, {"email": email, "role": role, "partner_id": pid, "ativo": active})
    return user_id
