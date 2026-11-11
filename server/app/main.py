"""API HTTP (Starlette), stream de eventos (SSE) e agendador do servidor."""
from __future__ import annotations

import asyncio
import mimetypes
import base64
import json
import threading
import time
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import ROOT, load_settings
from .core import R, AppError, Conflict, Forbidden, NotFound, Principal, audit, emit, hash_token, new_token, verify_password
from .db import DB, iso, loads, now
from datetime import timedelta
from . import agents, brain, ceo, channels, closings, commands, commerce, ops, policies, registry, saude, seed, tasks

WEB = ROOT / "web"
# No Windows o registro pode mapear .js para text/plain, o que bloqueia módulos JS no navegador.
for _ext, _mime in ((".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"), (".html", "text/html"),
                    (".svg", "image/svg+xml"), (".json", "application/json")):
    mimetypes.add_type(_mime, _ext)
SCHED = {"last_tick": None, "last_error": None, "ticks": 0}


# ------------------------------------------------------------------ utilidades
def cookie_name():
    # um cookie por ambiente: demo (porta 8000) e produção (8001) podem ficar abertos ao mesmo tempo sem se derrubar
    return f"sessao_{R.settings.env}"


def principal(req: Request) -> Principal:
    tok = req.cookies.get(cookie_name())
    if not tok:
        raise AppError("Autenticação necessária", "nao_autenticado", 401)
    s = R.db.one("SELECT s.*, u.email, u.name, u.role, u.partner_id, u.active FROM sessions s JOIN users u ON u.id=s.user_id WHERE token_hash=?",
                 hash_token(tok))
    if not s or not s["active"] or s["expires_at"] < iso():
        raise AppError("Sessão expirada", "nao_autenticado", 401)
    return Principal("user", s["user_id"], s["role"], s["partner_id"], s["name"])


async def body(req: Request):
    try:
        raw = await req.body()
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        raise AppError("JSON inválido")


def ok(data=None, status=200):
    return JSONResponse({"ok": True, "data": data}, status_code=status)


def endpoint(fn):
    async def wrapper(req: Request):
        try:
            return await fn(req)
        except AppError as e:
            return JSONResponse({"ok": False, "error": e.code, "message": e.message, **({"extra": e.extra} if e.extra else {})},
                                status_code=e.status)
    return wrapper


def T(fn, *a, **kw):
    return run_in_threadpool(fn, *a, **kw)


# ------------------------------------------------------------------ autenticação
_LOGIN_FAILS: dict = {}
@endpoint
async def login(req):
    b = await body(req)
    ip = req.client.host if req.client else "?"
    t = time.time()
    fails = [x for x in _LOGIN_FAILS.get(ip, []) if t - x < 300]
    if len(fails) >= 10:   # proteção contra tentativa de adivinhar senha
        raise AppError("Muitas tentativas de login. Aguarde 5 minutos.", "muitas_tentativas", 429)
    u = R.db.one("SELECT * FROM users WHERE email=? AND active=1", (b.get("email") or "").strip().lower())
    if not u or not verify_password(b.get("password") or "", u["password_hash"]):
        _LOGIN_FAILS[ip] = fails + [t]
        with R.db.tx() as c:
            emit(c, "sessao.falha_login", "auth", status="falhou", payload={"origem": "local" if ip in ("127.0.0.1", "::1", "testclient") else "rede"})
        await asyncio.sleep(0.3)
        raise AppError("E-mail ou senha inválidos", "credenciais_invalidas", 401)
    _LOGIN_FAILS.pop(ip, None)
    tok = new_token()
    with R.db.tx() as c:
        c.execute("INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES (?,?,?,?)",
                  (hash_token(tok), u["id"], iso(), iso(now() + timedelta(hours=R.settings.session_hours))))
        audit(c, Principal("user", u["id"], u["role"], u["partner_id"]), "sessao.login", "user", u["id"])
    r = ok({"id": u["id"], "name": u["name"], "role": u["role"], "partner_id": u["partner_id"]})
    r.set_cookie(cookie_name(), tok, httponly=True, samesite="strict", secure=req.url.scheme == "https", max_age=R.settings.session_hours * 3600)
    return r


@endpoint
async def setup_get(req):
    return ok({"needed": seed.needs_setup(), "environment": R.settings.env})


@endpoint
async def setup_post(req):
    b = await body(req)
    uid = await T(seed.create_first_admin, b.get("name"), b.get("email"), b.get("password"))
    return ok({"id": uid})


@endpoint
async def logout(req):
    tok = req.cookies.get(cookie_name())
    if tok:
        with R.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (hash_token(tok),))
    r = ok()
    r.delete_cookie(cookie_name())
    return r


@endpoint
async def me(req):
    p = principal(req)
    from .core import ROLE_PERMS
    perms = sorted(ROLE_PERMS.get(p.role, set()))
    partner = R.db.one("SELECT id,name,region_code FROM partners WHERE id=?", p.partner_id) if p.partner_id else None
    return ok({"id": p.id, "name": p.name, "role": p.role, "partner": partner, "is_central": p.is_central, "permissions": perms,
               "environment": R.settings.env})


# ------------------------------------------------------------------ público (loja)
@endpoint
async def store_get(req):
    return ok(await T(commerce.public_storefront, req.path_params["slug"]))


@endpoint
async def store_checkout(req):
    b = await body(req)
    res = await T(commerce.checkout, req.path_params["slug"], b.get("items"), b.get("customer"), b.get("shipping"),
                  req.headers.get("idempotency-key"))
    return ok(res, 200 if res["duplicate"] else 201)


@endpoint
async def order_pay(req):
    b = await body(req)
    return ok(await T(commerce.pay, req.path_params["code"], b.get("token"), b.get("test_card")))


@endpoint
async def order_public(req):
    return ok(await T(commerce.public_order, req.path_params["code"], req.query_params.get("token")))


@endpoint
async def webhook(req):
    raw = await req.body()
    return ok(await T(commerce.receive_webhook, req.path_params["provider"], raw, dict(req.headers)))


@endpoint
async def wa_verify(req):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse(await T(channels.webhook_verify, dict(req.query_params)))


@endpoint
async def wa_receive(req):
    raw = await req.body()
    return ok(await T(channels.webhook_receive, raw, dict(req.headers)))


# ------------------------------------------------------------------ privado
def private(fn):
    @endpoint
    async def w(req):
        p = principal(req)
        if req.method not in ("GET", "HEAD") and req.headers.get("x-requested-with") != "plataforma":
            raise Forbidden("Cabeçalho anti-CSRF ausente", "csrf")
        b = await body(req) if req.method in ("POST", "PUT", "PATCH") else {}
        res = await run_in_threadpool(fn, req, p, b)
        if isinstance(res, Response):
            return res
        return ok(res)
    return w


def qp(req, k):
    v = req.query_params.get(k)
    return v if v not in (None, "") else None


def _world(req, p, b): return ops.world(p)
def _overview(req, p, b): return ops.overview(p)
def _building(req, p, b): return ops.partner_building(p, req.path_params["id"])
def _orders(req, p, b): return commerce.list_orders(p, qp(req, "partner_id"), qp(req, "status"), qp(req, "q"))
def _order(req, p, b): return commerce.get_order(p, req.path_params["id"])
def _ship(req, p, b): return commerce.register_shipment(p, req.path_params["id"])
def _cancel(req, p, b): return commerce.cancel_order(p, req.path_params["id"], b.get("reason") or "cancelado pela operação")
def _return(req, p, b): return {"return_id": commerce.create_return(p, req.path_params["id"], b.get("reason") or "devolução")}
def _receive_return(req, p, b): return commerce.receive_return(p, req.path_params["id"])


def _tasks(req, p, b):
    return tasks.list_tasks(p, qp(req, "partner_id"), qp(req, "status"), qp(req, "assignee_id"), qp(req, "kind"),
                            qp(req, "overdue") == "1", qp(req, "q"))


def _task_create(req, p, b):
    with R.db.tx() as c:
        tid = tasks.create_task(c, p, {k: b.get(k) for k in tasks.FIELDS if k in b}, b.get("depends_on") or ())
    return tasks.get_task(tid, p)


def _task_get(req, p, b): return tasks.get_task(req.path_params["id"], p)


def _task_patch(req, p, b):
    with R.db.tx() as c:
        tasks.update_task(c, p, req.path_params["id"], {k: v for k, v in b.items() if k != "note"}, note=b.get("note"))
    return tasks.get_task(req.path_params["id"], p)


def _task_comment(req, p, b): return {"id": tasks.add_comment(p, req.path_params["id"], b.get("body"))}


def _task_attach(req, p, b):
    try:
        content = base64.b64decode(b.get("content_base64") or "", validate=True)
    except Exception:
        raise AppError("Conteúdo base64 inválido")
    return {"id": tasks.add_attachment(p, "task", req.path_params["id"], b.get("filename"), b.get("mime"), content, bool(b.get("is_evidence")))}


def _attachment(req, p, b):
    a = R.db.one("SELECT * FROM attachments WHERE id=?", req.path_params["id"])
    if not a:
        raise NotFound("Anexo não encontrado")
    p.require("tasks.read", a["partner_id"])
    return FileResponse(a["storage_path"], media_type=a["mime"], filename=a["filename"])


def _sf_create(req, p, b):
    return {"id": commerce.create_storefront(p, req.path_params["id"], b.get("slug"), b.get("title") or "", b.get("template") or "vitrine-classica",
                                             b.get("config"), b.get("product_ids") or [])}


def _sf_patch(req, p, b):
    commerce.update_storefront(p, req.path_params["id"], b.get("title"), b.get("config"), b.get("product_ids"), b.get("published"))
    return R.db.one("SELECT id,slug,published FROM storefronts WHERE id=?", req.path_params["id"])


def _closings(req, p, b): return closings.list_closings(p, qp(req, "partner_id"))
def _closing_prepare(req, p, b):
    return {"id": closings.prepare(p, b.get("kind"), b.get("partner_id"), b.get("period_type"), b.get("local_day"), b.get("unit_id"))}
def _closing_advance(req, p, b): return {"status": closings.advance(p, req.path_params["id"], b.get("note") or "")}
def _closing_reopen(req, p, b): return {"id": closings.reopen(p, req.path_params["id"], b.get("reason"))}
def _closing_settle(req, p, b): return {"payout_id": closings.settle(p, req.path_params["id"], b.get("reference"))}
def _adjust(req, p, b): return {"id": closings.add_adjustment(p, b.get("partner_id"), b.get("order_id"), b.get("amount_minor"), b.get("note"))}

def _agents(req, p, b): return agents.list_agents(p)
def _agent_runs(req, p, b): return agents.agent_runs(p, req.path_params["id"])
def _agent_run(req, p, b): return agents.run_agent(req.path_params["code"], "manual", p)
def _pause(req, p, b):
    agents.set_paused(p, bool(b.get("paused")))
    return {"paused": bool(b.get("paused"))}


def _approvals(req, p, b):
    p.require("approvals.read")
    rows = R.db.all("SELECT * FROM action_requests ORDER BY created_at DESC LIMIT 200")
    for r in rows:
        r["payload"] = loads(r.pop("payload_json"))
        r["effects"] = loads(r.pop("external_effects_json"))
    return rows


def _approve(req, p, b): return policies.decide_request(req.path_params["id"], bool(b.get("approve")), p, b.get("note") or "")
def _approval_cancel(req, p, b):
    p.require("approvals.decide")
    return policies.cancel_request(req.path_params["id"], p)


def _policies(req, p, b):
    p.require("approvals.read")
    rows = R.db.all("SELECT * FROM policies")
    known = {r["action"] for r in rows}
    return {"policies": rows, "actions_without_policy": [{"action": a, "description": d} for a, d in policies.KNOWN_ACTIONS.items() if a not in known]}


def _policy_put(req, p, b):
    policies.upsert_policy(p, req.path_params["action"], b.get("mode"), b.get("limit_minor"), b.get("currency"), b.get("approver_role"))
    return R.db.one("SELECT * FROM policies WHERE action=?", req.path_params["action"])


def events_for(p: Principal, after: int, limit=200):
    scope = p.partner_scope()
    if scope:
        rows = R.db.all("SELECT * FROM events WHERE id>? AND partner_id=? ORDER BY id LIMIT ?", after, scope, limit)
    else:
        rows = R.db.all("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", after, limit)
    for r in rows:
        r["payload"] = loads(r.pop("payload_json"))
    return rows


def _events(req, p, b):
    p.require("events.read")
    return {"events": events_for(p, int(qp(req, "after") or 0), min(int(qp(req, "limit") or 200), 1000)),
            "last_id": R.db.val("SELECT COALESCE(MAX(id),0) FROM events")}


def _audit(req, p, b):
    p.require("audit.read")
    return R.db.all("SELECT * FROM audit_log ORDER BY id DESC LIMIT 300")


def _inventory(req, p, b):
    p.require("inventory.read")
    return R.db.all("SELECT w.region_code, w.name warehouse, p.sku, p.name product, i.on_hand, i.reserved, i.on_hand - i.reserved available, "
                    "rp.reorder_point, i.updated_at FROM inventory i JOIN warehouses w ON w.id=i.warehouse_id JOIN products p ON p.id=i.product_id "
                    "LEFT JOIN product_regions rp ON rp.product_id=p.id AND rp.region_code=w.region_code ORDER BY w.region_code, p.sku")


def _inbox(req, p, b):
    p.require("events.read")
    if not p.is_central:
        raise Forbidden("Somente central")
    return R.db.all("SELECT id,provider,external_event_id,event_type,occurred_at,received_at,processed_at,status,attempts,last_error "
                    "FROM webhook_inbox ORDER BY received_at DESC LIMIT 200")


def _requeue(req, p, b): return {"status": commerce.requeue_dead_letter(p, req.path_params["id"])}


def _demo_replay(req, p, b):
    """Reenvia exatamente o mesmo evento (mesmo id) — demonstra proteção contra duplicidade."""
    if not R.settings.is_demo:
        raise Forbidden("Somente no ambiente demo")
    p.require("webhooks.requeue")
    item = R.db.one("SELECT * FROM webhook_inbox WHERE id=?", b.get("inbox_id"))
    if not item:
        raise NotFound("Entrada não encontrada")
    from .providers import DemoPaymentProvider
    ev = json.loads(item["payload"])
    raw, headers = DemoPaymentProvider.build_webhook(ev["type"], ev["data"], ev["id"], ev["occurred_at"])
    return commerce.receive_webhook("demo", raw, headers)


def _reg(req, p, b): return registry.overview(p)
def _reg_hq(req, p, b): return registry.save_hq(p, b)
def _pontos(req, p, b):
    from . import pontos
    return pontos.history(p, req.path_params["id"])
def _pontos_regra(req, p, b):
    from . import pontos
    return pontos.set_per_service(p, b.get("por_servico"))
def _base_ceo(req, p, b):
    p.require("cadastros.read")   # resumo da base cita unidades de vários clientes: só a central
    from . import gerente
    return gerente.last_summary(req.path_params["id"]) or {}
def _reg_base(req, p, b): return {"id": registry.save_base(p, b, req.path_params.get("id"))}
def _reg_client(req, p, b): return {"id": registry.save_client(p, b, req.path_params.get("id"))}
def _reg_unit(req, p, b): return {"id": registry.save_unit(p, b, req.path_params.get("id"))}
def _reg_user(req, p, b): return {"id": registry.save_user(p, b, req.path_params.get("id"))}
def _templates(req, p, b): return {k: v for k, v in tasks.TEMPLATES.items()}
def _checklist(req, p, b): return {"checklist": tasks.set_checklist_item(p, req.path_params["id"], b.get("index"), b.get("done"))}


def _geo_countries(req, p, b):
    p.require("world.read")
    from . import geo
    return geo.countries()


def _geo_states(req, p, b):
    p.require("world.read")
    from . import geo
    return geo.states(req.query_params.get("country") or "Brazil")


def _img(b, mime):
    return Response(b, media_type=mime, headers={"Cache-Control": "private, max-age=604800"})


def _img_unavailable(msg):
    # 204 (sem conteúdo): "ainda não disponível" é situação normal (sem internet), não erro — a tela usa o mapa escuro
    return Response(status_code=204, headers={"X-Motivo": msg.encode("ascii", "ignore").decode() or "indisponivel", "Cache-Control": "no-store"})


def _geo_img_world(req, p, b):
    p.require("world.read")
    from . import geo
    data = geo.image_world(2048 if req.query_params.get("leve") else 4096)
    return _img(data, "image/jpeg") if data else _img_unavailable("Imagem de satélite ainda não baixada (o servidor precisa de internet uma vez).")


def _geo_img_box(req, p, b):
    p.require("world.read")
    from . import geo
    try:
        w, s, e, n = (float(x) for x in (req.query_params.get("bbox") or "").split(","))
        data, box = geo.image_box(w, s, e, n)
    except ValueError as ex:
        raise AppError(str(ex) or "bbox inválido", "bbox_invalido")
    except Exception:
        return _img_unavailable("Imagem da região indisponível agora.")
    r = _img(data, "image/jpeg"); r.headers["X-Bbox"] = ",".join(str(v) for v in box); return r


def _geo_tile(req, p, b):
    p.require("world.read")
    from . import geo
    pp = req.path_params
    try:
        return _img(geo.tile(pp["z"], pp["x"], pp["y"]), "image/png")
    except ValueError as ex:
        raise AppError(str(ex), "tile_invalido")
    except Exception:
        return _img_unavailable("Mapa de ruas indisponível agora.")


def _channels(req, p, b): return channels.channels_overview(p)
def _saude(req, p, b): return saude.overview(p, bool(req.query_params.get("agora")))
def _chave_monitor(req, p, b):
    from . import brain
    return brain.set_api_key(p, b.get("key"), remove=bool(b.get("remove")), which="monitor")
def _monitor(req, p, b):
    from . import monitor
    return monitor.overview(p)
def _pais(req, p, b):
    p.require("mercados.read")
    from . import paises
    info = paises.lookup(req.path_params["nome"])
    m = R.db.one("SELECT id FROM markets WHERE status!='encerrado' AND (iso2=? OR country=?)", (info or {}).get("iso2"), (info or {}).get("name_pt") or req.path_params["nome"])
    return {"info": info, "mercado_id": m["id"] if m else None}
def _mercados(req, p, b):
    from . import mercados
    return mercados.list_markets(p)
def _mercado_new(req, p, b):
    from . import mercados, agents
    m = mercados.create_market(p, b)
    try:
        agents._dispatch(mercados.ceo_code(m["id"]), "criacao")   # CEO começa na hora (em segundo plano)
    except AppError:
        pass
    return mercados.get_market(p, m["id"])
def _mercado(req, p, b):
    from . import mercados
    return mercados.get_market(p, req.path_params["id"])


async def market_page(req):
    from . import mercados
    try:
        pr = principal(req)
        prev = pr if pr.can("mercados.read") else None
    except AppError:
        prev = None
    qp_ = req.query_params
    state = "ok" if qp_.get("obrigado") else ("erro" if qp_.get("erro") else "")
    utm = {k: qp_.get(k) for k in ("utm_source", "utm_medium", "utm_campaign", "item") if qp_.get(k)}
    html_ = await run_in_threadpool(mercados.render_site, req.path_params["slug"], prev, state, utm)
    if html_ is None:
        return Response("Página não encontrada", status_code=404, media_type="text/plain; charset=utf-8")
    return Response(html_, media_type="text/html; charset=utf-8")


async def market_lead(req):
    """Formulário do site do país (HTML puro): grava o lead e volta para a página com a mensagem."""
    from . import vitrine
    from starlette.responses import RedirectResponse
    slug = req.path_params["slug"]
    raw = (await req.body())[:20000]
    from urllib.parse import parse_qs
    form = {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items()}
    try:
        res = await run_in_threadpool(vitrine.capture_lead, slug, form, req.client.host if req.client else "?")
    except NotFound:
        return Response("Página não encontrada", status_code=404, media_type="text/plain; charset=utf-8")
    q = "obrigado=1" if res in ("ok", "spam") else "erro=1"
    return RedirectResponse(f"/m/{slug}?{q}#orcamento", status_code=303)


async def market_img(req):
    from . import vitrine
    from starlette.responses import FileResponse
    f = await run_in_threadpool(vitrine.image_path, req.path_params["slug"], req.path_params["name"])
    if not f:
        return Response("Não encontrado", status_code=404, media_type="text/plain")
    return FileResponse(f, headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"})


def _site_items(req, p, b):
    from . import vitrine
    p.require("mercados.read")
    return {"itens": vitrine.list_items(req.path_params["mid"]), "layouts": vitrine.LAYOUTS}


def _site_item_add(req, p, b):
    from . import vitrine
    return vitrine.save_item(p, req.path_params["mid"], b)


def _site_item_upd(req, p, b):
    from . import vitrine
    return vitrine.save_item(p, req.path_params["mid"], b, req.path_params["iid"])


def _site_item_del(req, p, b):
    from . import vitrine
    return vitrine.delete_item(p, req.path_params["mid"], req.path_params["iid"])


def _site_layout(req, p, b):
    from . import vitrine
    return vitrine.set_layout(p, req.path_params["mid"], b.get("layout"))


def _op_scopes(req, p, b):
    from . import operacional
    return operacional.scopes(p)
def _fin(req, p, b):
    from . import operacional
    return operacional.list_entries(p, qp(req, "month"), qp(req, "scope_type"), qp(req, "scope_id"))
def _fin_add(req, p, b):
    from . import operacional
    return operacional.add_entry(p, b)
def _fin_cancel(req, p, b):
    from . import operacional
    return operacional.cancel_entry(p, req.path_params["id"], b.get("reason"))
def _fin_settle(req, p, b):
    from . import operacional
    return operacional.settle_entry(p, req.path_params["id"])
def _stk(req, p, b):
    from . import operacional
    return operacional.list_stock(p, qp(req, "scope_type"), qp(req, "scope_id"))
def _stk_save(req, p, b):
    from . import operacional
    return operacional.save_item(p, b, req.path_params.get("id"))
def _stk_move(req, p, b):
    from . import operacional
    return operacional.move(p, req.path_params["id"], b.get("kind"), b.get("qty"), b.get("reason"))
def _marca_get(req, p, b):
    from . import vitrine
    return vitrine.get_brand()
def _marca_set(req, p, b):
    from . import vitrine
    return vitrine.save_brand(p, b)
def _leads(req, p, b):
    from . import vitrine
    return vitrine.list_leads(p, qp(req, "market_id"))
def _lead_status(req, p, b):
    from . import vitrine
    return vitrine.set_lead_status(p, req.path_params["id"], b.get("status"))


async def sitemap_xml(req):
    from . import mercados
    return Response(await run_in_threadpool(mercados.sitemap), media_type="application/xml")


async def robots_txt(req):
    return Response("User-agent: *\nAllow: /m/\nDisallow: /api/\nSitemap: /sitemap.xml\n", media_type="text/plain")


def _chave_claude(req, p, b):
    from . import brain
    return brain.set_api_key(p, b.get("key"), remove=bool(b.get("remove")))
def _saude_resolver(req, p, b): return saude.resolve_client_errors(p)


@endpoint
async def client_error(req):
    """Erros de JavaScript da tela (inclusive antes do login). Limitado e sem dados pessoais."""
    if req.headers.get("x-requested-with") != "plataforma":
        raise Forbidden("Cabeçalho anti-CSRF ausente", "csrf")
    try:
        p = principal(req)
    except AppError:
        p = None
    b = await body(req)
    return ok(await run_in_threadpool(saude.report_client_error, p, b if isinstance(b, dict) else {}, req.headers.get("user-agent", "")))
def _channel_new(req, p, b): return {"id": channels.create_channel(p, b.get("name"), b.get("base_id"))}
def _contact_new(req, p, b): return {"id": channels.save_contact(p, b.get("partner_id"), b.get("name") or "", b.get("phone"), b.get("role"))}
def _convs(req, p, b): return channels.list_conversations(p, qp(req, "partner_id"), qp(req, "status"))
def _conv(req, p, b): return channels.get_conversation(p, req.path_params["id"])
def _conv_send(req, p, b): return channels.send(p, req.path_params["id"], b.get("text"))
def _conv_status(req, p, b):
    channels.set_status(p, req.path_params["id"], b.get("status"))
    return {"status": b.get("status")}
def _conv_suggest(req, p, b): return channels.suggest_reply(p, req.path_params["id"])
def _conv_ticket(req, p, b): return channels.open_ticket(p, req.path_params["id"], b.get("unit_id") or None)
def _conv_link(req, p, b): return {"contact_id": channels.link_client(p, req.path_params["id"], b.get("partner_id"), b.get("name"))}
def _wa_sim(req, p, b): return channels.simulate_inbound(p, b.get("phone") or "", b.get("text") or "", b.get("name") or "Cliente (simulação)")
def _cmd_parse(req, p, b): return commands.parse(p, b.get("text"))
def _cmd_exec(req, p, b): return commands.execute(p, b.get("action"), b.get("args"))


def _ceo(req, p, b): return ceo.list_ventures(p, req.path_params["id"])
def _idea(req, p, b): return {"id": ceo.submit_idea(p, req.path_params["id"], b.get("idea"), b.get("keywords"))}


def _ceo_run(req, p, b):
    pid = req.path_params["id"]
    p.require("ventures.write", pid)
    return agents.run_agent(ceo.ceo_code(pid), "manual")


def _brain(req, p, b):
    p.require("agents.read")
    st = brain.status()
    if not p.is_central:
        st.pop("month_spend_usd", None)
    return st


def _demo_flags(req, p, b):
    p.require("agents.pause")
    return {"paused": R.db.val("SELECT value FROM system_flags WHERE key='agents_paused'") == "1"}


async def stream(req: Request):
    """SSE com retomada: o cliente envia Last-Event-ID (ou ?after) e recebe tudo o que perdeu."""
    try:
        p = principal(req)
        p.require("events.read")
    except AppError as e:
        return JSONResponse({"ok": False, "error": e.code, "message": e.message}, status_code=e.status)
    after = int(req.headers.get("last-event-id") or req.query_params.get("after") or 0)

    async def gen():
        nonlocal after
        yield "retry: 3000\n\n"
        last_ping = time.time()
        while True:
            if await req.is_disconnected():
                break
            rows = await run_in_threadpool(events_for, p, after, 200)
            for r in rows:
                after = r["id"]
                yield f"id: {r['id']}\nevent: evento\ndata: {json.dumps(r, ensure_ascii=False)}\n\n"
            if time.time() - last_ping > 10:
                last_ping = time.time()
                yield f"event: ping\ndata: {json.dumps({'at': iso(), 'last_id': after, 'scheduler': SCHED['last_tick'], 'build': saude.BUILD})}\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def health(req):
    return JSONResponse({"ok": True, "environment": R.settings.env, "time": iso(), "scheduler": SCHED,
                         "build": saude.BUILD, "started_at": saude.STARTED_AT, "saude": saude.summary(),
                         "integrations": {"stripe": "configurado" if R.settings.stripe_secret_key else "pendente",
                                          "llm": "configurado" if R.settings.anthropic_api_key else "pendente",
                                          "pagamentos_demo": R.settings.is_demo},
                         "vendor": {"gsap": (WEB / "vendor" / "gsap.min.js").exists()}})


async def index(req):
    return FileResponse(WEB / "index.html")


async def store_page(req):
    return FileResponse(WEB / "loja.html")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                                                   "script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["X-Environment"] = R.settings.env
        path = request.url.path
        if path == "/" or path.startswith("/static/") or path.startswith("/loja/"):
            resp.headers["Cache-Control"] = "no-cache"   # navegador sempre confere se há versão nova (ETag)
        return resp


# ------------------------------------------------------------------ agendador
def tick():
    from .tasks import process_recurrences
    from . import geo
    for fn in (ops.heartbeat_internal_modules, geo.geocode_missing, process_recurrences, commerce.retry_due_webhooks, commerce.expire_reservations,
               commerce.release_commissions, agents.run_due_agents):
        try:
            fn()
        except Exception as e:  # um passo com falha não derruba os demais
            SCHED["last_error"] = f"{fn.__name__}: {e}"
    SCHED["last_tick"] = iso()
    SCHED["ticks"] += 1
    try:
        saude.routine()
    except Exception as e:
        SCHED["last_error"] = f"saude: {e}"


def _scheduler_loop(stop: threading.Event):
    while not stop.wait(R.settings.scheduler_interval_s):
        tick()


def create_app(**overrides):
    settings = load_settings(**overrides)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    R.settings = settings
    _LOGIN_FAILS.clear()
    R.db = DB(settings.db_path)
    R.db.init()
    with R.db.tx() as c:
        envrow = c.execute("SELECT value FROM meta WHERE key='environment'").fetchone()
        if envrow and envrow[0] != settings.env:
            raise RuntimeError("Banco pertence a outro ambiente")
        if not envrow:
            c.execute("INSERT INTO meta(key,value) VALUES ('environment',?)", (settings.env,))
        agents.ensure_agents(c)
    if settings.is_demo:
        seed.seed_demo()
    else:
        seed.seed_reference()
    with R.db.tx() as c:
        agents.ensure_agents(c)  # agentes novos entram em bancos já existentes
        ceo.ensure_ceos(c)       # um CEO de IA por parceiro
        from . import gerente
        gerente.ensure_base_ceos(c)   # um CEO por base/filial
        channels.ensure_whatsapp_channel(c)

    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        th = None
        if settings.scheduler_enabled:
            th = threading.Thread(target=_scheduler_loop, args=(stop,), daemon=True)
            th.start()
        yield
        stop.set()

    P = private
    routes = [
        Route("/", index), Route("/loja/{slug}", store_page), Route("/api/health", health),
        Route("/api/setup", setup_get), Route("/api/setup", setup_post, methods=["POST"]),
        Route("/api/auth/login", login, methods=["POST"]), Route("/api/auth/logout", logout, methods=["POST"]), Route("/api/me", me),
        Route("/api/public/stores/{slug}", store_get), Route("/api/public/stores/{slug}/checkout", store_checkout, methods=["POST"]),
        Route("/api/public/orders/{code}", order_public), Route("/api/public/orders/{code}/pay", order_pay, methods=["POST"]),
        Route("/api/webhooks/payments/{provider}", webhook, methods=["POST"]),
        Route("/api/webhooks/whatsapp", wa_verify, methods=["GET"]), Route("/api/webhooks/whatsapp", wa_receive, methods=["POST"]),
        Route("/api/world", P(_world)), Route("/api/overview", P(_overview)), Route("/api/partners/{id}/building", P(_building)),
        Route("/api/partners/{id}/storefronts", P(_sf_create), methods=["POST"]), Route("/api/storefronts/{id}", P(_sf_patch), methods=["PATCH"]),
        Route("/api/orders", P(_orders)), Route("/api/orders/{id}", P(_order)),
        Route("/api/orders/{id}/ship", P(_ship), methods=["POST"]), Route("/api/orders/{id}/cancel", P(_cancel), methods=["POST"]),
        Route("/api/orders/{id}/return", P(_return), methods=["POST"]), Route("/api/returns/{id}/receive", P(_receive_return), methods=["POST"]),
        Route("/api/tasks", P(_tasks)), Route("/api/tasks", P(_task_create), methods=["POST"]),
        Route("/api/tasks/{id}", P(_task_get)), Route("/api/tasks/{id}", P(_task_patch), methods=["PATCH"]),
        Route("/api/tasks/{id}/comments", P(_task_comment), methods=["POST"]),
        Route("/api/tasks/{id}/attachments", P(_task_attach), methods=["POST"]), Route("/api/attachments/{id}", P(_attachment)),
        Route("/api/closings", P(_closings)), Route("/api/closings", P(_closing_prepare), methods=["POST"]),
        Route("/api/closings/{id}/advance", P(_closing_advance), methods=["POST"]),
        Route("/api/closings/{id}/reopen", P(_closing_reopen), methods=["POST"]),
        Route("/api/closings/{id}/settle", P(_closing_settle), methods=["POST"]),
        Route("/api/commissions/adjustments", P(_adjust), methods=["POST"]),
        Route("/api/channels", P(_channels)), Route("/api/channels", P(_channel_new), methods=["POST"]),
        Route("/api/contacts", P(_contact_new), methods=["POST"]),
        Route("/api/conversations", P(_convs)), Route("/api/conversations/{id}", P(_conv)),
        Route("/api/conversations/{id}/send", P(_conv_send), methods=["POST"]), Route("/api/conversations/{id}/status", P(_conv_status), methods=["POST"]),
        Route("/api/conversations/{id}/suggest", P(_conv_suggest), methods=["POST"]), Route("/api/conversations/{id}/ticket", P(_conv_ticket), methods=["POST"]),
        Route("/api/conversations/{id}/link", P(_conv_link), methods=["POST"]), Route("/api/demo/whatsapp-inbound", P(_wa_sim), methods=["POST"]),
        Route("/api/commands/parse", P(_cmd_parse), methods=["POST"]), Route("/api/commands/execute", P(_cmd_exec), methods=["POST"]),
        Route("/api/geo/countries", P(_geo_countries)), Route("/api/geo/states", P(_geo_states)),
        Route("/api/saude", P(_saude)), Route("/api/sistema/chave-claude", P(_chave_claude), methods=["POST"]),
        Route("/api/sistema/chave-monitor", P(_chave_monitor), methods=["POST"]), Route("/api/monitor", P(_monitor)),
        Route("/api/paises/{nome}", P(_pais)), Route("/api/mercados", P(_mercados)), Route("/api/mercados", P(_mercado_new), methods=["POST"]),
        Route("/api/mercados/{id}", P(_mercado)),
        Route("/m/{slug}", market_page), Route("/m/{slug}/lead", market_lead, methods=["POST"]), Route("/m/{slug}/img/{name}", market_img),
        Route("/api/mercados/{mid}/site-itens", P(_site_items)), Route("/api/mercados/{mid}/site-itens", P(_site_item_add), methods=["POST"]),
        Route("/api/mercados/{mid}/site-itens/{iid}", P(_site_item_upd), methods=["PUT"]),
        Route("/api/mercados/{mid}/site-itens/{iid}", P(_site_item_del), methods=["DELETE"]),
        Route("/api/mercados/{mid}/layout", P(_site_layout), methods=["PUT"]),
        Route("/api/marca", P(_marca_get)), Route("/api/marca", P(_marca_set), methods=["POST"]),
        Route("/api/operacoes", P(_op_scopes)), Route("/api/financeiro-manual", P(_fin)), Route("/api/financeiro-manual", P(_fin_add), methods=["POST"]),
        Route("/api/financeiro-manual/{id}/cancelar", P(_fin_cancel), methods=["POST"]), Route("/api/financeiro-manual/{id}/realizar", P(_fin_settle), methods=["POST"]),
        Route("/api/estoque-operacoes", P(_stk)), Route("/api/estoque-operacoes", P(_stk_save), methods=["POST"]),
        Route("/api/estoque-operacoes/{id}", P(_stk_save), methods=["PUT"]), Route("/api/estoque-operacoes/{id}/movimento", P(_stk_move), methods=["POST"]),
        Route("/api/leads", P(_leads)), Route("/api/leads/{id}", P(_lead_status), methods=["PATCH"]), Route("/sitemap.xml", sitemap_xml), Route("/robots.txt", robots_txt), Route("/api/saude/erros/resolver", P(_saude_resolver), methods=["POST"]),
        Route("/api/saude/erro-tela", client_error, methods=["POST"]),
        Route("/api/geo/imagem/mundo", P(_geo_img_world)), Route("/api/geo/imagem/regiao", P(_geo_img_box)),
        Route("/api/geo/tile/{z:int}/{x:int}/{y:int}", P(_geo_tile)),
        Route("/api/cadastros", P(_reg)), Route("/api/cadastros/sede", P(_reg_hq), methods=["POST"]), Route("/api/partners/{id}/pontos", P(_pontos)), Route("/api/cadastros/pontos", P(_pontos_regra), methods=["POST"]), Route("/api/bases/{id}/ceo", P(_base_ceo)),
        Route("/api/cadastros/bases", P(_reg_base), methods=["POST"]), Route("/api/cadastros/bases/{id}", P(_reg_base), methods=["PUT"]),
        Route("/api/cadastros/clientes", P(_reg_client), methods=["POST"]), Route("/api/cadastros/clientes/{id}", P(_reg_client), methods=["PUT"]),
        Route("/api/cadastros/unidades", P(_reg_unit), methods=["POST"]), Route("/api/cadastros/unidades/{id}", P(_reg_unit), methods=["PUT"]),
        Route("/api/cadastros/usuarios", P(_reg_user), methods=["POST"]), Route("/api/cadastros/usuarios/{id}", P(_reg_user), methods=["PUT"]),
        Route("/api/task-templates", P(_templates)), Route("/api/tasks/{id}/checklist", P(_checklist), methods=["POST"]),
        Route("/api/partners/{id}/ceo", P(_ceo)), Route("/api/partners/{id}/ideas", P(_idea), methods=["POST"]),
        Route("/api/partners/{id}/ceo/run", P(_ceo_run), methods=["POST"]), Route("/api/brain", P(_brain)),
        Route("/api/agents", P(_agents)), Route("/api/agents/pause", P(_pause), methods=["POST"]),
        Route("/api/agents/{id}/runs", P(_agent_runs)), Route("/api/agents/{code}/run", P(_agent_run), methods=["POST"]),
        Route("/api/approvals", P(_approvals)), Route("/api/approvals/{id}/decide", P(_approve), methods=["POST"]),
        Route("/api/approvals/{id}/cancel", P(_approval_cancel), methods=["POST"]),
        Route("/api/policies", P(_policies)), Route("/api/policies/{action}", P(_policy_put), methods=["PUT"]),
        Route("/api/events", P(_events)), Route("/api/stream", stream), Route("/api/audit", P(_audit)),
        Route("/api/inventory", P(_inventory)), Route("/api/webhooks/inbox", P(_inbox)),
        Route("/api/webhooks/inbox/{id}/requeue", P(_requeue), methods=["POST"]),
        Route("/api/demo/replay-webhook", P(_demo_replay), methods=["POST"]),
        Mount("/static", StaticFiles(directory=str(WEB)), name="static"),
    ]
    app = Starlette(routes=routes, lifespan=lifespan, middleware=[Middleware(SecurityHeaders)])
    app.state.tick = tick
    return app
