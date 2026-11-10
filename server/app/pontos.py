"""Pontos de cashback e nível dos clientes.

- Cada serviço concluído (tarefa do cliente, não pendência) rende pontos ao cliente — regra em Cadastros (padrão 10).
- Registro somente de acréscimos, um por tarefa (UNIQUE): concluir de novo, repetir evento ou reprocessar nunca soma em dobro.
- Pontos são um registro de fidelidade: NÃO movimentam dinheiro. Resgate/abatimento é decisão humana (fica para a central).
- Nível (tamanho do prédio no mapa) vem do total de serviços concluídos.
"""
from __future__ import annotations

import json

from .core import R, Principal, AppError, audit, emit
from .db import iso, new_id

LEVELS = (0, 3, 8, 20, 50)          # serviços concluídos para chegar aos níveis 1..5
DEFAULT_PER_SERVICE = 10


def per_service():
    row = R.db.one("SELECT value FROM app_settings WHERE key='pontos_por_servico'")
    return int(json.loads(row["value"])) if row else DEFAULT_PER_SERVICE


def set_per_service(actor: Principal, value):
    actor.require("cadastros.write")
    try:
        v = int(value)
        assert 0 <= v <= 10000
    except Exception:
        raise AppError("Pontos por serviço: número inteiro de 0 a 10000", "pontos_invalidos")
    with R.db.tx() as c:
        c.execute("INSERT OR REPLACE INTO app_settings(key,value,updated_at) VALUES ('pontos_por_servico',?,?)", (json.dumps(v), iso()))
        audit(c, actor, "pontos.regra", "settings", "pontos_por_servico", None, {"por_servico": v})
    return {"por_servico": v}


def award(c, task: dict):
    """Chamado dentro da transação que conclui a tarefa."""
    if not task.get("partner_id") or task.get("kind") != "tarefa":
        return 0
    row = c.execute("SELECT value FROM app_settings WHERE key='pontos_por_servico'").fetchone()
    pts = int(json.loads(row["value"])) if row else DEFAULT_PER_SERVICE
    if pts <= 0:
        return 0
    cur = c.execute("INSERT OR IGNORE INTO loyalty_points(id,partner_id,task_id,points,reason,created_at) VALUES (?,?,?,?,?,?)",
                    (new_id("pts"), task["partner_id"], task["id"], pts, f"Serviço concluído: {task['title'][:120]}", iso()))
    if cur.rowcount:
        emit(c, "cliente.pontos", "pontos", "partner", task["partner_id"], task["partner_id"], status="creditado",
             payload={"pontos": pts, "task_id": task["id"]})
        return pts
    return 0


def level(services: int) -> int:
    return max(i + 1 for i, n in enumerate(LEVELS) if services >= n)


def summary_by_partner():
    pts = {r["partner_id"]: r["p"] for r in R.db.all("SELECT partner_id, SUM(points) p FROM loyalty_points GROUP BY partner_id")}
    svc = {r["partner_id"]: r["n"] for r in R.db.all("SELECT partner_id, COUNT(*) n FROM tasks WHERE status='concluida' AND kind='tarefa' "
                                                       "AND partner_id IS NOT NULL GROUP BY partner_id")}
    out = {}
    for pid in set(pts) | set(svc):
        n = svc.get(pid, 0); lv = level(n)
        out[pid] = {"pontos": pts.get(pid, 0), "servicos": n, "nivel": lv,
                    "proximo_nivel_em": (LEVELS[lv] - n) if lv < len(LEVELS) else None}
    return out


def history(actor: Principal, partner_id, limit=50):
    actor.require("partners.read", partner_id)
    return {"total": R.db.val("SELECT COALESCE(SUM(points),0) FROM loyalty_points WHERE partner_id=?", partner_id) or 0,
            "por_servico": per_service(),
            "lancamentos": R.db.all("SELECT points, reason, created_at, task_id FROM loyalty_points WHERE partner_id=? ORDER BY created_at DESC LIMIT ?",
                                    partner_id, limit)}
