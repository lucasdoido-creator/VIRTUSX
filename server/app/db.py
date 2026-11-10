"""Banco de dados (SQLite em WAL). O SQL é mantido portável para migração a PostgreSQL.

Valores monetários: inteiros em unidades mínimas (centavos). Datas: ISO-8601 UTC com sufixo Z.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

_clock_offset = timedelta(0)
_lock = threading.RLock()


def now() -> datetime:
    return datetime.now(timezone.utc) + _clock_offset


def advance_clock(**kw):
    """Somente para testes: avança o relógio lógico."""
    global _clock_offset
    _clock_offset += timedelta(**kw)


def reset_clock():
    global _clock_offset
    _clock_offset = timedelta(0)


def iso(dt: datetime | None = None) -> str:
    dt = dt or now()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def dumps(o) -> str:
    return json.dumps(o, ensure_ascii=False, sort_keys=True, default=str)


def loads(s):
    return json.loads(s) if s else None


SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS regions (
  code TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL, locale TEXT NOT NULL,
  timezone TEXT NOT NULL, sales_enabled INTEGER NOT NULL DEFAULT 0,
  sales_status_note TEXT, tax_mode TEXT NOT NULL, tax_status_note TEXT,
  payment_provider TEXT, logistics_provider TEXT, seller_entity TEXT NOT NULL,
  return_window_days INTEGER NOT NULL DEFAULT 7, config_json TEXT
);

CREATE TABLE IF NOT EXISTS partners (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, legal_name TEXT, region_code TEXT NOT NULL REFERENCES regions(code),
  commercial_model TEXT NOT NULL CHECK (commercial_model IN ('central_commission','b2b_resale','cliente_servico')),
  commission_rate_bp INTEGER, status TEXT NOT NULL DEFAULT 'ativo', contact_name TEXT, segment TEXT, contract_minor INTEGER, notes TEXT,
  created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS partner_units (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL REFERENCES partners(id), name TEXT NOT NULL,
  country TEXT NOT NULL, state TEXT, city TEXT, address TEXT,
  location_status TEXT NOT NULL CHECK (location_status IN ('confirmada','nao_confirmada')),
  location_source TEXT, lat REAL, lng REAL, is_demo INTEGER NOT NULL DEFAULT 0, base_id TEXT, active INTEGER NOT NULL DEFAULT 1, geo_source TEXT
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, name TEXT NOT NULL, password_hash TEXT NOT NULL,
  role TEXT NOT NULL, partner_id TEXT REFERENCES partners(id), active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, base_id TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
  id TEXT PRIMARY KEY, sku TEXT NOT NULL UNIQUE, name TEXT NOT NULL, description TEXT,
  brand TEXT NOT NULL, attributes_json TEXT, status TEXT NOT NULL DEFAULT 'ativo',
  created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_translations (
  product_id TEXT NOT NULL REFERENCES products(id), locale TEXT NOT NULL, name TEXT NOT NULL,
  description TEXT, status TEXT NOT NULL DEFAULT 'rascunho', PRIMARY KEY (product_id, locale)
);

CREATE TABLE IF NOT EXISTS product_regions (
  product_id TEXT NOT NULL REFERENCES products(id), region_code TEXT NOT NULL REFERENCES regions(code),
  price_minor INTEGER NOT NULL CHECK (price_minor >= 0), b2b_price_minor INTEGER,
  unit_cost_minor INTEGER, active INTEGER NOT NULL DEFAULT 1, reorder_point INTEGER NOT NULL DEFAULT 5,
  PRIMARY KEY (product_id, region_code)
);

CREATE TABLE IF NOT EXISTS partner_products (
  partner_id TEXT NOT NULL REFERENCES partners(id), product_id TEXT NOT NULL REFERENCES products(id),
  authorized INTEGER NOT NULL DEFAULT 1, PRIMARY KEY (partner_id, product_id)
);

CREATE TABLE IF NOT EXISTS warehouses (
  id TEXT PRIMARY KEY, region_code TEXT NOT NULL REFERENCES regions(code), name TEXT NOT NULL,
  state TEXT, city TEXT, location_status TEXT NOT NULL DEFAULT 'nao_confirmada', is_demo INTEGER NOT NULL DEFAULT 0,
  lat REAL, lng REAL, geo_source TEXT
);

CREATE TABLE IF NOT EXISTS inventory (
  warehouse_id TEXT NOT NULL REFERENCES warehouses(id), product_id TEXT NOT NULL REFERENCES products(id),
  on_hand INTEGER NOT NULL CHECK (on_hand >= 0), reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved >= 0),
  updated_at TEXT NOT NULL, CHECK (reserved <= on_hand), PRIMARY KEY (warehouse_id, product_id)
);

CREATE TABLE IF NOT EXISTS inventory_movements (
  id TEXT PRIMARY KEY, warehouse_id TEXT NOT NULL, product_id TEXT NOT NULL, kind TEXT NOT NULL,
  qty INTEGER NOT NULL, order_id TEXT, reservation_id TEXT, at TEXT NOT NULL, actor TEXT
);

CREATE TABLE IF NOT EXISTS storefronts (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL REFERENCES partners(id), slug TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL, template TEXT NOT NULL, config_json TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS storefront_products (
  storefront_id TEXT NOT NULL REFERENCES storefronts(id), product_id TEXT NOT NULL REFERENCES products(id),
  position INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (storefront_id, product_id)
);

CREATE TABLE IF NOT EXISTS orders (
  id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, access_token_hash TEXT NOT NULL,
  region_code TEXT NOT NULL REFERENCES regions(code), storefront_id TEXT REFERENCES storefronts(id),
  partner_id TEXT REFERENCES partners(id), unit_id TEXT REFERENCES partner_units(id),
  sales_model TEXT NOT NULL, seller_of_record TEXT NOT NULL, status TEXT NOT NULL,
  currency TEXT NOT NULL, subtotal_minor INTEGER NOT NULL, shipping_minor INTEGER NOT NULL,
  total_minor INTEGER NOT NULL, customer_name TEXT NOT NULL, customer_email TEXT NOT NULL,
  shipping_json TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, correlation_id TEXT NOT NULL,
  cancel_reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS order_items (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), product_id TEXT NOT NULL,
  sku TEXT NOT NULL, name TEXT NOT NULL, qty INTEGER NOT NULL CHECK (qty > 0),
  unit_price_minor INTEGER NOT NULL, line_total_minor INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reservations (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), warehouse_id TEXT NOT NULL,
  product_id TEXT NOT NULL, qty INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ativa','consumida','liberada')),
  expires_at TEXT NOT NULL, created_at TEXT NOT NULL, closed_at TEXT, close_reason TEXT
);

CREATE TABLE IF NOT EXISTS payments (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), provider TEXT NOT NULL,
  provider_ref TEXT NOT NULL UNIQUE, status TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, fee_minor INTEGER NOT NULL DEFAULT 0, is_simulated INTEGER NOT NULL,
  last_event_occurred_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_inbox (
  id TEXT PRIMARY KEY, provider TEXT NOT NULL, external_event_id TEXT NOT NULL, event_type TEXT NOT NULL,
  payload TEXT NOT NULL, occurred_at TEXT, received_at TEXT NOT NULL, processed_at TEXT,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT, last_error TEXT,
  UNIQUE (provider, external_event_id)
);

CREATE TABLE IF NOT EXISTS shipments (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL UNIQUE REFERENCES orders(id), provider TEXT NOT NULL,
  tracking_code TEXT NOT NULL, status TEXT NOT NULL, is_simulated INTEGER NOT NULL,
  registered_at TEXT NOT NULL, registered_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS commission_entries (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL REFERENCES partners(id), order_id TEXT NOT NULL REFERENCES orders(id),
  kind TEXT NOT NULL CHECK (kind IN ('apuracao','estorno','ajuste')),
  base_minor INTEGER NOT NULL, rate_bp INTEGER NOT NULL, amount_minor INTEGER NOT NULL, currency TEXT NOT NULL,
  status TEXT NOT NULL, release_at TEXT, created_at TEXT NOT NULL, note TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_commission_once ON commission_entries(order_id, kind) WHERE kind IN ('apuracao','estorno');

CREATE TABLE IF NOT EXISTS refunds (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), payment_id TEXT REFERENCES payments(id),
  amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
  action_request_id TEXT, created_at TEXT NOT NULL, executed_at TEXT, provider_ref TEXT
);

CREATE TABLE IF NOT EXISTS returns (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id), reason TEXT NOT NULL,
  status TEXT NOT NULL, refund_id TEXT, created_at TEXT NOT NULL, created_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
  id TEXT PRIMARY KEY, order_id TEXT REFERENCES orders(id), partner_id TEXT, subject TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT NOT NULL, draft_reply TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT, partner_id TEXT REFERENCES partners(id),
  unit_id TEXT REFERENCES partner_units(id), assignee_type TEXT CHECK (assignee_type IN ('humano','agente')),
  assignee_id TEXT, priority TEXT NOT NULL DEFAULT 'media' CHECK (priority IN ('baixa','media','alta','critica')),
  due_at TEXT, status TEXT NOT NULL CHECK (status IN ('a_fazer','em_andamento','aguardando_terceiro','aguardando_aprovacao','concluida','cancelada')),
  kind TEXT NOT NULL DEFAULT 'tarefa' CHECK (kind IN ('tarefa','pendencia')), pendency_type TEXT,
  source TEXT, related_entity TEXT, created_by TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  completed_by TEXT, completed_at TEXT, recurrence_rule TEXT, recurrence_next_at TEXT, recurrence_parent_id TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0, checklist_json TEXT, template TEXT
);
CREATE TABLE IF NOT EXISTS task_dependencies (task_id TEXT NOT NULL, depends_on_id TEXT NOT NULL, PRIMARY KEY(task_id, depends_on_id));
CREATE TABLE IF NOT EXISTS task_comments (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, author TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_history (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, at TEXT NOT NULL, actor TEXT NOT NULL, field TEXT NOT NULL, old TEXT, new TEXT);

CREATE TABLE IF NOT EXISTS attachments (
  id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, partner_id TEXT, filename TEXT NOT NULL,
  mime TEXT, size INTEGER NOT NULL, sha256 TEXT NOT NULL, storage_path TEXT NOT NULL, uploaded_by TEXT NOT NULL,
  is_evidence INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, role TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, current_task_id TEXT, current_run_id TEXT,
  current_partner_id TEXT, status_since TEXT NOT NULL, last_confirmed_at TEXT, config_json TEXT NOT NULL,
  budget_month_minor INTEGER NOT NULL DEFAULT 0, budget_currency TEXT NOT NULL DEFAULT 'USD',
  next_run_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_runs (
  id TEXT PRIMARY KEY, agent_id TEXT NOT NULL REFERENCES agents(id), task_id TEXT, partner_id TEXT, trigger TEXT NOT NULL,
  status TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, summary TEXT, evidence_json TEXT,
  cost_minor INTEGER NOT NULL DEFAULT 0, cost_currency TEXT NOT NULL DEFAULT 'USD', cost_known INTEGER NOT NULL DEFAULT 1,
  tokens_in INTEGER NOT NULL DEFAULT 0, tokens_out INTEGER NOT NULL DEFAULT 0, correlation_id TEXT
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, at TEXT NOT NULL, tool TEXT NOT NULL,
  input_summary TEXT, output_summary TEXT, ok INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
  action TEXT PRIMARY KEY, mode TEXT NOT NULL CHECK (mode IN ('automatico','revisao','proibido')),
  limit_minor INTEGER, currency TEXT, approver_role TEXT, description TEXT, updated_by TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS action_requests (
  id TEXT PRIMARY KEY, action TEXT NOT NULL, partner_id TEXT, payload_json TEXT NOT NULL, amount_minor INTEGER,
  currency TEXT, requested_by_type TEXT NOT NULL, requested_by TEXT NOT NULL, status TEXT NOT NULL,
  reason TEXT, decided_by TEXT, decided_at TEXT, executed_at TEXT, external_effects_json TEXT,
  compensation_options TEXT, correlation_id TEXT, created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_flags (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by TEXT, updated_at TEXT);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, source TEXT NOT NULL,
  entity_type TEXT, entity_id TEXT, partner_id TEXT, region_code TEXT, correlation_id TEXT,
  status TEXT NOT NULL, occurred_at TEXT NOT NULL, received_at TEXT NOT NULL, payload_json TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_events_partner ON events(partner_id, id);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, actor_type TEXT NOT NULL, actor_id TEXT NOT NULL,
  action TEXT NOT NULL, entity_type TEXT, entity_id TEXT, partner_id TEXT, before_json TEXT, after_json TEXT,
  correlation_id TEXT
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log é somente inclusão'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log é somente inclusão'); END;

CREATE TABLE IF NOT EXISTS closings (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('vendas','comissoes','conciliacao_pagamentos','servicos')),
  partner_id TEXT NOT NULL REFERENCES partners(id), unit_id TEXT, period_type TEXT NOT NULL CHECK (period_type IN ('diario','semanal','mensal')),
  period_start TEXT NOT NULL, period_end TEXT NOT NULL, timezone TEXT NOT NULL, local_label TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL, settlement_status TEXT NOT NULL,
  totals_json TEXT NOT NULL, divergences_json TEXT NOT NULL, currency TEXT NOT NULL,
  prepared_by TEXT, prepared_at TEXT, reviewed_by TEXT, reviewed_at TEXT, approved_by TEXT, approved_at TEXT,
  closed_by TEXT, closed_at TEXT, supersedes_id TEXT, superseded_by_id TEXT, reopen_reason TEXT,
  is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS payouts (
  id TEXT PRIMARY KEY, closing_id TEXT NOT NULL REFERENCES closings(id), partner_id TEXT NOT NULL, amount_minor INTEGER NOT NULL,
  currency TEXT NOT NULL, reference TEXT NOT NULL, is_simulated INTEGER NOT NULL, settled_at TEXT NOT NULL, registered_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
  id TEXT PRIMARY KEY, partner_id TEXT, code TEXT NOT NULL, name TEXT NOT NULL, category TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('modulo_interno','integracao_conectada','link_externo','planejada')),
  status TEXT NOT NULL, url TEXT, critical INTEGER NOT NULL DEFAULT 0, last_sync_at TEXT, last_error TEXT,
  note TEXT, UNIQUE(partner_id, code)
);

CREATE TABLE IF NOT EXISTS ventures (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL REFERENCES partners(id), idea TEXT NOT NULL, keywords TEXT,
  status TEXT NOT NULL, summary TEXT, analysis_json TEXT, plan_source TEXT, created_by TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS venture_staff (
  venture_id TEXT NOT NULL REFERENCES ventures(id), agent_id TEXT NOT NULL REFERENCES agents(id), role TEXT NOT NULL,
  hired_at TEXT NOT NULL, cost_note TEXT NOT NULL, PRIMARY KEY (venture_id, agent_id)
);
CREATE TABLE IF NOT EXISTS ceo_briefings (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL, venture_id TEXT, local_day TEXT NOT NULL, kind TEXT NOT NULL,
  summary TEXT, content_json TEXT NOT NULL, sources_json TEXT NOT NULL, brain TEXT, cost_minor INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS brain_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, tier TEXT NOT NULL,
  purpose TEXT, partner_id TEXT, tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL, cost_usd REAL NOT NULL, ok INTEGER NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS service_bases (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT, city TEXT, address TEXT,
  location_status TEXT NOT NULL DEFAULT 'nao_confirmada', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL,
  is_demo INTEGER NOT NULL DEFAULT 0, lat REAL, lng REAL, geo_source TEXT
);
CREATE TABLE IF NOT EXISTS channels (
  id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('whatsapp','interno')), name TEXT NOT NULL, status TEXT NOT NULL,
  base_id TEXT, config_json TEXT, created_at TEXT NOT NULL, created_by TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
  id TEXT PRIMARY KEY, partner_id TEXT NOT NULL REFERENCES partners(id), name TEXT NOT NULL, phone TEXT NOT NULL, role TEXT,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(phone)
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY, channel_id TEXT NOT NULL REFERENCES channels(id), partner_id TEXT, contact_id TEXT, wa_id TEXT,
  contact_name TEXT, status TEXT NOT NULL, unread INTEGER NOT NULL DEFAULT 0, assigned_to TEXT, last_inbound_at TEXT,
  last_message_at TEXT, created_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_conv_wa ON conversations(channel_id, wa_id) WHERE wa_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id), direction TEXT NOT NULL CHECK (direction IN ('in','out','nota')),
  body TEXT NOT NULL, wa_message_id TEXT UNIQUE, status TEXT NOT NULL, author TEXT, is_simulated INTEGER NOT NULL DEFAULT 0,
  occurred_at TEXT, created_at TEXT NOT NULL, error TEXT
);
CREATE TABLE IF NOT EXISTS research_cache (key TEXT PRIMARY KEY, fetched_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_errors (
  id TEXT PRIMARY KEY, key TEXT NOT NULL UNIQUE, message TEXT NOT NULL, source TEXT, line INTEGER, where_ TEXT, stack TEXT,
  build TEXT, server_build TEXT, user_agent TEXT, user_id TEXT, first_at TEXT NOT NULL, last_at TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 1, resolved INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS idempotency_keys (key TEXT PRIMARY KEY, scope TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
"""


class DB:
    def __init__(self, path):
        self.path = str(path)
        self._local = threading.local()

    def connect(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA foreign_keys=ON")
            c.execute("PRAGMA busy_timeout=10000")
            self._local.conn = c
        return c

    def init(self):
        c = self.connect()
        c.executescript(SCHEMA)
        migrate(c)

    @contextmanager
    def tx(self):
        """Transação de escrita serializada (BEGIN IMMEDIATE). Reentrante na mesma thread."""
        c = self.connect()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            try:
                yield c
            finally:
                self._local.depth -= 1
            return
        with _lock:
            c.execute("BEGIN IMMEDIATE")
            self._local.depth = 1
            self._local.after = []
            try:
                yield c
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                self._local.after = []
                raise
            finally:
                self._local.depth = 0
        callbacks, self._local.after = self._local.after, []
        for fn in callbacks:
            fn()

    def after_commit(self, fn):
        """Executa fn somente depois que a transação atual for confirmada (ou já, se não houver transação)."""
        if getattr(self._local, "depth", 0):
            self._local.after.append(fn)
        else:
            fn()

    def one(self, sql, *args):
        r = self.connect().execute(sql, args).fetchone()
        return dict(r) if r else None

    def all(self, sql, *args):
        return [dict(r) for r in self.connect().execute(sql, args).fetchall()]

    def val(self, sql, *args):
        r = self.connect().execute(sql, args).fetchone()
        return r[0] if r else None

    def close(self):
        c = getattr(self._local, "conn", None)
        if c:
            c.close()
            self._local.conn = None


# ------------------------------------------------------------------ migrações (bancos já existentes no seu computador)
ADDED_COLUMNS = {
    "partners": ["segment TEXT", "contract_minor INTEGER", "notes TEXT"],
    "partner_units": ["base_id TEXT", "active INTEGER NOT NULL DEFAULT 1", "geo_source TEXT"],
    "warehouses": ["lat REAL", "lng REAL", "geo_source TEXT"],
    "service_bases": ["lat REAL", "lng REAL", "geo_source TEXT"],
    "tasks": ["checklist_json TEXT", "template TEXT"],
    "users": ["base_id TEXT"],
}
CHECK_TOKENS = {"partners": "cliente_servico", "closings": "'servicos'"}


def _create_sql(table):
    import re
    m = re.search(r"CREATE TABLE IF NOT EXISTS %s \((.*?)\n\);" % table, SCHEMA, re.S)
    return m.group(0).replace("CREATE TABLE IF NOT EXISTS %s (" % table, "CREATE TABLE %s__novo (" % table)


def migrate(c):
    """Idempotente: adiciona colunas novas e reconstrói tabelas cujas regras CHECK mudaram, preservando os dados."""
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col.split()[0] not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    for table, token in CHECK_TOKENS.items():
        cur = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0]
        if token in cur:
            continue
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(_create_sql(table))
            new_cols = {r[1] for r in c.execute(f"PRAGMA table_info({table}__novo)")}
            keep = ",".join(x for x in cols if x in new_cols)
            c.execute(f"INSERT INTO {table}__novo({keep}) SELECT {keep} FROM {table}")
            c.execute(f"DROP TABLE {table}")
            c.execute(f"ALTER TABLE {table}__novo RENAME TO {table}")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
        finally:
            c.execute("PRAGMA foreign_keys=ON")
