"""Financeiro manual e estoque de TODAS as operações (sede, bases, clientes, países).

Financeiro: lançamentos de receita/despesa, previstos ou realizados, por categoria e data. Nada é apagado: lançamento errado
é CANCELADO com motivo (auditoria). Valores em centavos (inteiros) — sem arredondamento de ponto flutuante.
Estoque: itens por operação, movimentos (entrada, saída, ajuste) com motivo e saldo após o movimento; saída nunca deixa
saldo negativo; abaixo do mínimo gera UMA pendência até normalizar.
"""
from __future__ import annotations

import re
from datetime import date

from .core import R, AppError, Conflict, NotFound, Principal, SYSTEM, audit, emit
from .db import iso, new_id

SCOPES = ("central", "base", "cliente", "mercado")
CATEGORIAS = {"receita": ["serviços", "mensalidade de parceiro", "venda de produto", "contrato", "outras receitas"],
              "despesa": ["salários/freelancers", "materiais", "deslocamento", "aluguel", "impostos", "ferramentas/software",
                          "marketing/anúncios", "outras despesas"]}


def _money(v):
    s = str(v or "").strip().replace("R$", "").replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})*(,\d{1,2})?|\d+(,\d{1,2})?", s):
        s = s.replace(".", "").replace(",", ".")
    try:
        cents = round(float(s) * 100)
    except ValueError:
        raise AppError("Valor inválido (ex.: 1.250,90)", "valor_invalido")
    if cents <= 0 or cents > 10**12:
        raise AppError("Valor deve ser maior que zero", "valor_invalido")
    return cents


def _scope_name(c, st, sid):
    if st == "central":
        return "Sede / central"
    q = {"base": "SELECT name FROM service_bases WHERE id=?", "cliente": "SELECT name FROM partners WHERE id=?",
         "mercado": "SELECT country || ' · ' || id FROM markets WHERE id=?"}[st]
    r = c.execute(q, (sid,)).fetchone()
    if not r:
        raise AppError("Operação não encontrada", "escopo_invalido")
    return r[0]


def scopes(actor: Principal):
    """Operações disponíveis para lançar (menu da tela)."""
    actor.require("fin.read") if actor.can("fin.read") else actor.require("estoque.read")
    out = [{"tipo": "central", "id": "central", "nome": "Sede / central"}]
    out += [{"tipo": "base", "id": r["id"], "nome": "Base · " + r["name"]} for r in R.db.all("SELECT id, name FROM service_bases ORDER BY name")]
    out += [{"tipo": "cliente", "id": r["id"], "nome": "Cliente · " + r["name"]} for r in R.db.all("SELECT id, name FROM partners WHERE status!='encerrado' ORDER BY name")]
    out += [{"tipo": "mercado", "id": r["id"], "nome": "País · " + r["country"]} for r in R.db.all("SELECT id, country FROM markets WHERE status!='encerrado'")]
    return out


# ------------------------------------------------------------------ financeiro manual
def add_entry(actor: Principal, d: dict):
    actor.require("fin.write")
    st, sid = d.get("scope_type"), d.get("scope_id") or "central"
    if st not in SCOPES:
        raise AppError("Operação inválida", "escopo_invalido")
    kind = d.get("kind")
    if kind not in CATEGORIAS:
        raise AppError("Tipo deve ser receita ou despesa", "tipo_invalido")
    cat = " ".join(str(d.get("category") or "").split())[:60] or ("outras receitas" if kind == "receita" else "outras despesas")
    desc = " ".join(str(d.get("description") or "").split())[:240]
    if not desc:
        raise AppError("Descreva o lançamento", "campo_obrigatorio")
    dt = str(d.get("date") or date.today().isoformat())[:10]
    try:
        date.fromisoformat(dt)
    except ValueError:
        raise AppError("Data inválida", "data_invalida")
    status = d.get("status") or "realizado"
    if status not in ("previsto", "realizado"):
        raise AppError("Situação inválida", "status_invalido")
    cur = (d.get("currency") or "BRL").upper()[:3]
    eid = new_id("fin")
    with R.db.tx() as c:
        name = _scope_name(c, st, sid)
        c.execute("INSERT INTO fin_entries(id,scope_type,scope_id,scope_name,kind,category,description,amount_minor,currency,date,status,created_by,created_at)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, st, sid, name, kind, cat, desc, _money(d.get("amount")), cur, dt, status, actor.label, iso()))
        audit(c, actor, "financeiro.lancamento", "fin_entry", eid, None, {"tipo": kind, "operacao": name, "valor": d.get("amount")})
        emit(c, "financeiro.lancamento", "financeiro", "fin_entry", eid, status=status, payload={"tipo": kind, "operacao": name})
    return R.db.one("SELECT * FROM fin_entries WHERE id=?", eid)


def cancel_entry(actor: Principal, eid, reason):
    actor.require("fin.write")
    reason = " ".join(str(reason or "").split())[:200]
    if not reason:
        raise AppError("Informe o motivo do cancelamento", "motivo_obrigatorio")
    with R.db.tx() as c:
        r = c.execute("SELECT canceled_at FROM fin_entries WHERE id=?", (eid,)).fetchone()
        if not r:
            raise NotFound("Lançamento não encontrado")
        if r[0]:
            raise Conflict("Lançamento já cancelado", "ja_cancelado")
        c.execute("UPDATE fin_entries SET canceled_at=?, cancel_reason=? WHERE id=?", (iso(), reason, eid))
        audit(c, actor, "financeiro.cancelado", "fin_entry", eid, None, {"motivo": reason})
    return {"id": eid, "cancelado": True}


def settle_entry(actor: Principal, eid):
    actor.require("fin.write")
    with R.db.tx() as c:
        n = c.execute("UPDATE fin_entries SET status='realizado' WHERE id=? AND status='previsto' AND canceled_at IS NULL", (eid,)).rowcount
        if not n:
            raise Conflict("Só lançamento previsto e ativo pode ser marcado como realizado", "nao_previsto")
        audit(c, actor, "financeiro.realizado", "fin_entry", eid)
    return {"id": eid, "status": "realizado"}


def list_entries(actor: Principal, month=None, scope_type=None, scope_id=None):
    actor.require("fin.read")
    sql, args = "SELECT * FROM fin_entries WHERE 1=1", []
    if month:
        sql += " AND substr(date,1,7)=?"
        args.append(month[:7])
    if scope_type:
        sql += " AND scope_type=?"
        args.append(scope_type)
    if scope_id:
        sql += " AND scope_id=?"
        args.append(scope_id)
    rows = R.db.all(sql + " ORDER BY date DESC, created_at DESC LIMIT 1000", *args)
    tot = {}
    for r in rows:
        if r["canceled_at"]:
            continue
        k = (r["scope_name"], r["currency"])
        t = tot.setdefault(k, {"operacao": r["scope_name"], "moeda": r["currency"], "receitas": 0, "despesas": 0, "previsto_receitas": 0, "previsto_despesas": 0})
        pre = "" if r["status"] == "realizado" else "previsto_"
        t[pre + ("receitas" if r["kind"] == "receita" else "despesas")] += r["amount_minor"]
    for t in tot.values():
        t["resultado"] = t["receitas"] - t["despesas"]
    return {"lancamentos": rows, "totais": sorted(tot.values(), key=lambda x: x["operacao"]), "categorias": CATEGORIAS}


# ------------------------------------------------------------------ estoque por operação
def save_item(actor: Principal, d: dict, item_id=None):
    actor.require("estoque.write")
    st, sid = d.get("scope_type"), d.get("scope_id") or "central"
    if st not in SCOPES:
        raise AppError("Operação inválida", "escopo_invalido")
    name = " ".join(str(d.get("name") or "").split())[:120]
    sku = re.sub(r"[^A-Za-z0-9._-]", "", str(d.get("sku") or ""))[:40] or None
    if not name:
        raise AppError("Informe o nome do item", "campo_obrigatorio")
    try:
        min_qty = float(str(d.get("min_qty") or 0).replace(",", "."))
    except ValueError:
        raise AppError("Mínimo inválido", "valor_invalido")
    unit = " ".join(str(d.get("unit") or "un").split())[:12]
    cost = _money(d["cost"]) if str(d.get("cost") or "").strip() else None
    with R.db.tx() as c:
        sname = _scope_name(c, st, sid)
        if item_id:
            if not c.execute("SELECT 1 FROM stock_items WHERE id=?", (item_id,)).fetchone():
                raise NotFound("Item não encontrado")
            c.execute("UPDATE stock_items SET name=?, sku=?, unit=?, min_qty=?, cost_minor=?, updated_at=? WHERE id=?",
                      (name, sku, unit, min_qty, cost, iso(), item_id))
        else:
            item_id = new_id("stk")
            c.execute("INSERT INTO stock_items(id,scope_type,scope_id,scope_name,sku,name,unit,qty,min_qty,cost_minor,currency,active,created_at,updated_at)"
                      " VALUES (?,?,?,?,?,?,?,0,?,?,?,1,?,?)", (item_id, st, sid, sname, sku, name, unit, min_qty, cost, "BRL", iso(), iso()))
        audit(c, actor, "estoque.item", "stock_item", item_id, None, {"nome": name, "operacao": sname})
        _check_min(c, item_id)
    return R.db.one("SELECT * FROM stock_items WHERE id=?", item_id)


def move(actor: Principal, item_id, kind, qty, reason):
    actor.require("estoque.write")
    if kind not in ("entrada", "saida", "ajuste"):
        raise AppError("Movimento inválido", "movimento_invalido")
    try:
        q = float(str(qty).replace(",", "."))
    except ValueError:
        raise AppError("Quantidade inválida", "quantidade_invalida")
    if kind != "ajuste" and q <= 0 or kind == "ajuste" and q < 0:
        raise AppError("Quantidade inválida", "quantidade_invalida")
    reason = " ".join(str(reason or "").split())[:200]
    if not reason:
        raise AppError("Informe o motivo do movimento", "motivo_obrigatorio")
    with R.db.tx() as c:
        it = c.execute("SELECT * FROM stock_items WHERE id=?", (item_id,)).fetchone()
        if not it:
            raise NotFound("Item não encontrado")
        new = it["qty"] + q if kind == "entrada" else it["qty"] - q if kind == "saida" else q
        if new < 0:
            raise Conflict(f"Saldo insuficiente: há {it['qty']:g} {it['unit']}", "saldo_insuficiente")
        c.execute("UPDATE stock_items SET qty=?, updated_at=? WHERE id=?", (new, iso(), item_id))
        c.execute("INSERT INTO stock_moves(id,item_id,kind,qty,qty_after,reason,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (new_id("mov"), item_id, kind, q, new, reason, actor.label, iso()))
        audit(c, actor, "estoque.movimento", "stock_item", item_id, {"qty": it["qty"]}, {"qty": new, "tipo": kind, "motivo": reason})
        emit(c, "estoque.movimento", "estoque", "stock_item", item_id, status=kind, payload={"item": it["name"], "saldo": new})
        _check_min(c, item_id)
    return R.db.one("SELECT * FROM stock_items WHERE id=?", item_id)


def _check_min(c, item_id):
    from . import tasks
    it = c.execute("SELECT * FROM stock_items WHERE id=?", (item_id,)).fetchone()
    ref = f"stock:{item_id}"
    open_t = c.execute("SELECT id FROM tasks WHERE related_entity=? AND status NOT IN ('concluida','cancelada')", (ref,)).fetchone()
    if it["min_qty"] and it["qty"] < it["min_qty"] and not open_t:
        tasks.create_task(c, SYSTEM, {"title": f"Estoque baixo · {it['name']} ({it['scope_name']})", "kind": "pendencia", "pendency_type": "estoque_baixo",
                                      "priority": "alta", "related_entity": ref, "source": "estoque",
                                      "description": f"Saldo {it['qty']:g} {it['unit']} abaixo do mínimo {it['min_qty']:g}. Repor ou ajustar o mínimo."})
    elif open_t and (not it["min_qty"] or it["qty"] >= it["min_qty"]):
        tasks.update_task(c, SYSTEM, open_t[0], {"status": "concluida"}, note="Estoque normalizado.", force=True)


def list_stock(actor: Principal, scope_type=None, scope_id=None):
    actor.require("estoque.read")
    sql, args = "SELECT * FROM stock_items WHERE active=1", []
    if scope_type:
        sql += " AND scope_type=?"
        args.append(scope_type)
    if scope_id:
        sql += " AND scope_id=?"
        args.append(scope_id)
    items = R.db.all(sql + " ORDER BY scope_name, name", *args)
    for i in items:
        i["baixo"] = bool(i["min_qty"] and i["qty"] < i["min_qty"])
        i["movimentos"] = R.db.all("SELECT kind, qty, qty_after, reason, created_by, created_at FROM stock_moves WHERE item_id=? ORDER BY created_at DESC LIMIT 5", i["id"])
    return items
