"""Núcleo: runtime, segurança, permissões, eventos e auditoria."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from dataclasses import dataclass

from .db import DB, dumps, iso, now


class Runtime:
    db: DB = None
    settings = None


R = Runtime()


class AppError(Exception):
    status = 400

    def __init__(self, message, code="erro", status=None, extra=None):
        super().__init__(message)
        self.message = message
        self.code = code
        if status:
            self.status = status
        self.extra = extra or {}


class Forbidden(AppError):
    status = 403


class NotFound(AppError):
    status = 404


class Conflict(AppError):
    status = 409


# ---------------------------------------------------------------- senhas e tokens
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def sign_payload(secret: str, ts: int, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- dados pessoais
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+)")


def mask_email(e: str) -> str:
    if not e or "@" not in e:
        return "***"
    u, d = e.split("@", 1)
    return f"{u[0]}***@{d}"


def mask_name(n: str) -> str:
    parts = (n or "").split()
    if not parts:
        return "***"
    return parts[0] + (" " + parts[-1][0] + "." if len(parts) > 1 else "")


def scrub(text: str) -> str:
    """Remove e-mails e sequências longas de dígitos (cartões, documentos) de textos de log."""
    if not text:
        return text
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    return re.sub(r"\d{8,}", "[número oculto]", text)


# ---------------------------------------------------------------- permissões
ROLE_PERMS = {
    "admin_central": {"*"},
    "operacoes_central": {
        "ventures.read",
        "orders.read", "orders.read_pii", "orders.cancel", "shipments.create", "inventory.read",
        "partners.read", "tasks.read", "tasks.write", "agents.read", "agents.run", "events.read",
        "closings.read", "apps.read", "storefronts.read", "returns.create", "audit.read",
        "approvals.read", "world.read", "catalog.read",
    },
    "financeiro_central": {
        "orders.read", "orders.read_pii", "partners.read", "commissions.read", "closings.read",
        "closings.prepare", "closings.review", "closings.approve", "closings.close", "closings.reopen",
        "closings.settle", "approvals.read", "approvals.decide", "tasks.read", "tasks.write", "events.read",
        "agents.read", "apps.read", "audit.read", "world.read", "inventory.read", "catalog.read",
    },
    "parceiro_admin": {
        "ventures.read", "ventures.write", "agents.run.ceo",
        "orders.read", "storefronts.read", "storefronts.write", "tasks.read", "tasks.write",
        "closings.read", "closings.review", "commissions.read", "apps.read", "partners.read",
        "events.read", "agents.read", "world.read", "catalog.read", "inventory.read",
    },
    "parceiro_operador": {
        "ventures.read",
        "orders.read", "storefronts.read", "tasks.read", "tasks.write", "apps.read", "partners.read",
        "events.read", "agents.read", "world.read", "catalog.read",
    },
}
CENTRAL_ROLES = {"admin_central", "operacoes_central", "financeiro_central"}


@dataclass
class Principal:
    kind: str  # user | agent | system | public
    id: str
    role: str = ""
    partner_id: str | None = None
    name: str = ""

    @property
    def is_central(self):
        return self.kind in ("system", "agent") or self.role in CENTRAL_ROLES

    @property
    def label(self):
        return f"{self.kind}:{self.id}"

    def can(self, perm: str) -> bool:
        if self.kind == "system":
            return True
        perms = ROLE_PERMS.get(self.role, set())
        return "*" in perms or perm in perms

    def require(self, perm: str, partner_id: str | None = None):
        if not self.can(perm):
            raise Forbidden(f"Permissão necessária: {perm}", "sem_permissao")
        self.require_partner(partner_id)

    def require_partner(self, partner_id):
        if partner_id is not None and not self.is_central and self.partner_id != partner_id:
            raise Forbidden("Acesso restrito aos dados do próprio parceiro", "isolamento_parceiro")

    def partner_scope(self):
        """None = todos (central); caso contrário, id do parceiro."""
        return None if self.is_central else self.partner_id


SYSTEM = Principal("system", "sistema", "admin_central", None, "Sistema")


# ---------------------------------------------------------------- eventos e auditoria
def emit(c, type_, source, entity_type=None, entity_id=None, partner_id=None, region=None,
         correlation_id=None, status="ok", occurred_at=None, payload=None):
    """Registra evento persistente (sem dados pessoais). Assinantes leem por id crescente."""
    received = iso()
    cur = c.execute(
        "INSERT INTO events(type,source,entity_type,entity_id,partner_id,region_code,correlation_id,status,"
        "occurred_at,received_at,payload_json,is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (type_, source, entity_type, entity_id, partner_id, region, correlation_id, status,
         occurred_at or received, received, dumps(payload or {}), 1 if R.settings.is_demo else 0),
    )
    return cur.lastrowid


def audit(c, actor: Principal, action, entity_type=None, entity_id=None, before=None, after=None,
          partner_id=None, correlation_id=None):
    c.execute(
        "INSERT INTO audit_log(at,actor_type,actor_id,action,entity_type,entity_id,partner_id,before_json,after_json,correlation_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (iso(), actor.kind, actor.id, action, entity_type, entity_id, partner_id,
         dumps(before) if before is not None else None, dumps(after) if after is not None else None, correlation_id),
    )


def get_flag(key, default=None):
    v = R.db.val("SELECT value FROM system_flags WHERE key=?", key)
    return v if v is not None else default


def set_flag(c, key, value, actor: Principal):
    old = c.execute("SELECT value FROM system_flags WHERE key=?", (key,)).fetchone()
    c.execute("INSERT INTO system_flags(key,value,updated_by,updated_at) VALUES (?,?,?,?) "
              "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_by=excluded.updated_by, updated_at=excluded.updated_at",
              (key, value, actor.label, iso()))
    audit(c, actor, "flag.set", "flag", key, {"value": old[0] if old else None}, {"value": value})


def money(minor: int, currency: str) -> str:
    sym = {"BRL": "R$", "USD": "US$", "EUR": "€"}.get(currency, currency)
    neg = minor < 0
    v = abs(minor)
    s = f"{v // 100:,}".replace(",", ".") + "," + f"{v % 100:02d}"
    return f"{'-' if neg else ''}{sym} {s}"


__all__ = ["R", "AppError", "Forbidden", "NotFound", "Conflict", "Principal", "SYSTEM", "emit", "audit",
           "now", "iso"]
