"""Pesquisa gratuita e previsões determinísticas.

- Notícias: feeds RSS públicos do Google Notícias, sem chave e sem custo; cache de 12 h por consulta.
  Títulos de terceiros são DADOS: passam por scrub() e vão ao cérebro delimitados, nunca como instrução.
- Previsão: regressão linear simples sobre vendas confirmadas diárias (código, não IA).
"""
from __future__ import annotations

import json
import statistics
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import timedelta

from .core import R, scrub
from .db import dumps, iso, loads, now, parse_iso

LOCALES = {"BR": ("pt-BR", "BR", "BR:pt-419"), "PT": ("pt-PT", "PT", "PT:pt-150"), "US": ("en-US", "US", "US:en")}
TRANSPORT = {"fetch": None}  # injeção para testes


def _fetch(url):
    if TRANSPORT["fetch"]:
        return TRANSPORT["fetch"](url)
    import httpx
    r = httpx.get(url, timeout=15, follow_redirects=True, headers={"User-Agent": "VIRTUSX/1.0 (+pesquisa de mercado)"})
    r.raise_for_status()
    return r.text


def news(query: str, region: str = "BR", limit: int = 8):
    """Retorna {items:[{title,source,link,published}], error, cached, fetched_at}."""
    query = " ".join((query or "").split())[:120]
    if not query:
        return {"items": [], "error": "consulta vazia", "cached": False, "fetched_at": None}
    hl, gl, ceid = LOCALES.get(region, LOCALES["BR"])
    key = f"news:{region}:{query.lower()}"
    cached = R.db.one("SELECT * FROM research_cache WHERE key=?", key)
    if cached and now() - parse_iso(cached["fetched_at"]) < timedelta(hours=12):
        return {**loads(cached["payload"]), "cached": True, "fetched_at": cached["fetched_at"]}
    if not R.settings.research_enabled:
        return {"items": [], "error": "pesquisa externa desativada (RESEARCH=0)", "cached": False, "fetched_at": None}
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": hl, "gl": gl, "ceid": ceid})
    try:
        root = ET.fromstring(_fetch(url))
        items = []
        for it in root.iter("item"):
            src = it.find("source")
            items.append({"title": scrub((it.findtext("title") or "").strip())[:240],
                          "source": (src.text if src is not None else "")[:80],
                          "link": (it.findtext("link") or "").strip()[:500],
                          "published": (it.findtext("pubDate") or "").strip()[:40]})
            if len(items) >= limit:
                break
        payload = {"items": items, "error": None, "query": query, "url": url}
    except Exception as e:
        return {"items": [], "error": f"falha ao buscar notícias ({type(e).__name__})", "cached": False, "fetched_at": None}
    with R.db.tx() as c:
        c.execute("INSERT INTO research_cache(key,fetched_at,payload) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
                  "fetched_at=excluded.fetched_at, payload=excluded.payload", (key, iso(), dumps(payload)))
    return {**payload, "cached": False, "fetched_at": iso()}


def sales_series(partner_id, days=28):
    end = now()
    start = end - timedelta(days=days)
    rows = R.db.all("SELECT substr(o.created_at,1,10) d, SUM(o.total_minor) v FROM orders o JOIN payments p ON p.order_id=o.id "
                    "WHERE o.partner_id=? AND p.status IN ('confirmado','reembolsado') AND o.created_at>=? GROUP BY d", partner_id, iso(start))
    by = {r["d"]: r["v"] for r in rows}
    return [by.get((start + timedelta(days=i + 1)).strftime("%Y-%m-%d"), 0) for i in range(days)]


def forecast(partner_id, horizon=30):
    """Projeção linear das vendas dos próximos `horizon` dias (em unidades mínimas). Determinística."""
    s = sales_series(partner_id)
    active_days = sum(1 for v in s if v)
    if active_days < 7:
        return {"ok": False, "motivo": f"dados insuficientes ({active_days} dia(s) com vendas nos últimos 28; mínimo 7)",
                "historico_28d_minor": sum(s)}
    n = len(s)
    xs = list(range(n))
    mx, my = statistics.fmean(xs), statistics.fmean(s)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, s)) / sum((x - mx) ** 2 for x in xs)
    inter = my - slope * mx
    proj = [max(0.0, inter + slope * (n + i)) for i in range(horizon)]
    resid = statistics.pstdev([y - (inter + slope * x) for x, y in zip(xs, s)])
    total = sum(proj)
    return {"ok": True, "historico_28d_minor": sum(s), "media_diaria_minor": round(my), "tendencia_diaria_minor": round(slope),
            "previsao_minor": round(total), "faixa_minor": [round(max(0, total - resid * horizon ** 0.5)), round(total + resid * horizon ** 0.5)],
            "horizonte_dias": horizon, "metodo": "regressão linear sobre 28 dias"}


def internal_snapshot(partner_id):
    p = R.db.one("SELECT p.*, r.currency, r.name region_name FROM partners p JOIN regions r ON r.code=p.region_code WHERE p.id=?", partner_id)
    prods = R.db.all("SELECT pr.name, rp.price_minor, rp.unit_cost_minor FROM partner_products pp JOIN products pr ON pr.id=pp.product_id "
                     "JOIN product_regions rp ON rp.product_id=pr.id AND rp.region_code=? WHERE pp.partner_id=? AND pp.authorized=1",
                     p["region_code"], partner_id)
    from .commerce import available_qty
    pids = R.db.all("SELECT product_id FROM partner_products WHERE partner_id=? AND authorized=1", partner_id)
    return {"parceiro": p["name"], "regiao": p["region_name"], "moeda": p["currency"], "modelo": p["commercial_model"],
            "produtos_autorizados": [{"nome": x["name"], "preco_minor": x["price_minor"], "custo_minor": x["unit_cost_minor"]} for x in prods],
            "estoque_disponivel_total": sum(available_qty(x["product_id"], p["region_code"]) for x in pids),
            "pedidos_abertos": R.db.val("SELECT COUNT(*) FROM orders WHERE partner_id=? AND status IN ('aguardando_pagamento','pago')", partner_id),
            "tarefas_abertas": R.db.val("SELECT COUNT(*) FROM tasks WHERE partner_id=? AND status NOT IN ('concluida','cancelada')", partner_id),
            "previsao": forecast(partner_id)}
