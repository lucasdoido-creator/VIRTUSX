"""Cérebro de IA misto, GRÁTIS primeiro.

Ordem por nível:
- 'rotina'  (briefings, rascunhos, resumos): só Ollama local (grátis). Claude apenas se BRAIN_ROUTINE_FALLBACK_CLAUDE=1.
- 'decisao' (plano de uma ideia nova): Ollama primeiro; o Claude só é chamado se a IA grátis estiver indisponível
  ou devolver um plano inválido — e apenas dentro dos tetos diário e mensal. Aviso automático ao chegar a 80% do mês.
Sem nenhum cérebro disponível → BrainUnavailable e o chamador usa o caminho determinístico (sem IA).

Regras: conteúdo externo vai sempre delimitado como dado; números e cálculos vêm do código, nunca do modelo.
"""
from __future__ import annotations

import json
import time

from .core import R, AppError
from .db import iso, now

PRICES_USD_PER_MTOK = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0), "claude-opus-5-5": (4.0, 20.0)}
_ollama_state = {"checked_at": 0.0, "ok": False, "models": [], "error": None}

# pontos de injeção para testes
TRANSPORT = {"ollama": None, "claude": None}


class BrainUnavailable(AppError):
    def __init__(self, why):
        super().__init__(f"Cérebro de IA indisponível: {why}", "cerebro_indisponivel", 503)


def day_spend_usd():
    return R.db.val("SELECT COALESCE(SUM(cost_usd),0) FROM brain_usage WHERE at>=?", now().strftime("%Y-%m-%dT00:00:00")) or 0.0


def _maybe_alert():
    """Ao passar de 80% do teto mensal: um aviso por mês (evento + pendência para a central)."""
    cap = R.settings.brain_monthly_cap_usd
    spent = month_spend_usd()
    if cap <= 0 or spent < 0.8 * cap:
        return
    key = "brain_alert_" + now().strftime("%Y-%m")
    from .core import Principal, emit, get_flag, set_flag
    if get_flag(key):
        return
    from . import tasks
    with R.db.tx() as c:
        set_flag(c, key, "1", Principal("system", "cerebro"))
        emit(c, "cerebro.alerta_gasto", "cerebro", status="80%", payload={"gasto_usd": round(spent, 4), "teto_usd": cap})
        tasks.create_task(c, Principal("system", "cerebro"), {
            "title": f"Gasto com Claude chegou a 80% do teto mensal (US$ {spent:.2f} de {cap:.2f})", "kind": "pendencia",
            "pendency_type": "orcamento_ia", "priority": "alta", "source": "cerebro",
            "description": "Ao atingir o teto, o sistema continua apenas com IA grátis e regras. Ajuste BRAIN_MONTHLY_CAP_USD se quiser."})


def month_spend_usd():
    start = now().strftime("%Y-%m-01T00:00:00")
    return R.db.val("SELECT COALESCE(SUM(cost_usd),0) FROM brain_usage WHERE at>=?", start) or 0.0


def _http_post(url, payload, headers=None, timeout=120):
    import httpx
    r = httpx.post(url, json=payload, headers=headers or {}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def ollama_status(force=False):
    if TRANSPORT["ollama"] is not None:
        return {"ok": True, "models": [R.settings.ollama_model], "error": None}
    if not force and time.time() - _ollama_state["checked_at"] < 60:
        return dict(_ollama_state)
    try:
        import httpx
        r = httpx.get(R.settings.ollama_url.rstrip("/") + "/api/tags", timeout=2)
        r.raise_for_status()
        models = [m.get("name") for m in r.json().get("models", [])]
        _ollama_state.update(ok=True, models=models, error=None if R.settings.ollama_model in models
                             else f"modelo {R.settings.ollama_model} não baixado (rode: ollama pull {R.settings.ollama_model})")
        if R.settings.ollama_model not in models:
            _ollama_state["ok"] = False
    except Exception as e:  # Ollama não instalado/desligado
        _ollama_state.update(ok=False, models=[], error=f"Ollama não encontrado em {R.settings.ollama_url} ({type(e).__name__})")
    _ollama_state["checked_at"] = time.time()
    return dict(_ollama_state)


def status():
    o = ollama_status()
    return {"ollama": {"ok": o["ok"], "url": R.settings.ollama_url, "model": R.settings.ollama_model, "error": o["error"]},
            "claude": {"configured": bool(R.settings.anthropic_api_key), "decision_model": R.settings.decision_model},
            "month_spend_usd": round(month_spend_usd(), 4), "monthly_cap_usd": R.settings.brain_monthly_cap_usd,
            "day_spend_usd": round(day_spend_usd(), 4), "daily_cap_usd": R.settings.brain_daily_cap_usd,
            "routine_fallback_to_claude": R.settings.routine_fallback_to_claude}


def _call_ollama(system, user, max_tokens, json_mode):
    payload = {"model": R.settings.ollama_model, "stream": False, "options": {"num_predict": max_tokens, "temperature": 0.3},
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if json_mode:
        payload["format"] = "json"
    fn = TRANSPORT["ollama"] or (lambda p: _http_post(R.settings.ollama_url.rstrip("/") + "/api/chat", p))
    data = fn(payload)
    return data["message"]["content"], int(data.get("prompt_eval_count") or 0), int(data.get("eval_count") or 0), 0.0


def _call_claude(system, user, max_tokens, model):
    payload = {"model": model, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user}]}
    fn = TRANSPORT["claude"] or (lambda p: _http_post("https://api.anthropic.com/v1/messages", p, {
        "x-api-key": R.settings.anthropic_api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}, 90))
    data = fn(payload)
    tin, tout = data["usage"]["input_tokens"], data["usage"]["output_tokens"]
    pin, pout = PRICES_USD_PER_MTOK.get(model, (4.0, 20.0))
    return "".join(b.get("text", "") for b in data["content"]), tin, tout, tin / 1e6 * pin + tout / 1e6 * pout


def _estimate_claude(model, system, user, max_tokens):
    pin, pout = PRICES_USD_PER_MTOK.get(model, (4.0, 20.0))
    tin = (len(system) + len(user)) / 3.5
    return tin / 1e6 * pin + max_tokens / 1e6 * pout


def _record(provider, model, tier, purpose, partner_id, tin, tout, cost, ok, error=None):
    with R.db.tx() as c:
        c.execute("INSERT INTO brain_usage(at,provider,model,tier,purpose,partner_id,tokens_in,tokens_out,cost_usd,ok,error) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (iso(), provider, model, tier, purpose, partner_id, tin, tout, cost, 1 if ok else 0, (error or "")[:300] or None))


def think(system, user, tier="rotina", purpose="", partner_id=None, max_tokens=900, json_mode=True, validate=None):
    """Retorna {text, data (se JSON), provider, model, cost_usd, tokens_in, tokens_out}. Nunca chamar dentro de transação."""
    order = []
    claude_ok = bool(R.settings.anthropic_api_key)
    if tier == "decisao":
        order.append("ollama")
        if claude_ok:
            order.append("claude")
    else:
        order.append("ollama")
        if claude_ok and R.settings.routine_fallback_to_claude:
            order.append("claude")
    reasons = []
    for prov in order:
        if prov == "ollama":
            st = ollama_status()
            if not st["ok"]:
                reasons.append(st["error"] or "Ollama indisponível")
                continue
            try:
                text, tin, tout, cost = _call_ollama(system, user, max_tokens, json_mode)
            except Exception as e:
                _record("ollama", R.settings.ollama_model, tier, purpose, partner_id, 0, 0, 0.0, False, str(e))
                reasons.append(f"Ollama falhou: {type(e).__name__}")
                continue
            model = R.settings.ollama_model
        else:
            model = R.settings.decision_model
            est = _estimate_claude(model, system, user, max_tokens)
            if month_spend_usd() + est > R.settings.brain_monthly_cap_usd:
                reasons.append(f"teto mensal de US$ {R.settings.brain_monthly_cap_usd:.2f} atingido")
                continue
            if day_spend_usd() + est > R.settings.brain_daily_cap_usd:
                reasons.append(f"teto diário de US$ {R.settings.brain_daily_cap_usd:.2f} atingido")
                continue
            try:
                text, tin, tout, cost = _call_claude(system, user, max_tokens, model)
            except Exception as e:
                _record("claude", model, tier, purpose, partner_id, 0, 0, 0.0, False, str(e))
                reasons.append(f"Claude falhou: {type(e).__name__}")
                continue
        _record(prov, model, tier, purpose, partner_id, tin, tout, cost, True)
        if prov == "claude":
            _maybe_alert()
        out = {"text": text, "provider": prov, "model": model, "cost_usd": cost, "tokens_in": tin, "tokens_out": tout, "data": None}
        if json_mode:
            out["data"] = parse_json(text)
        if validate is not None:
            checked = validate(out["data"])
            if not checked:  # resposta fraca: sobe para o próximo cérebro (se houver e couber no teto)
                reasons.append(f"{prov} devolveu resposta inválida")
                continue
            out["data"] = checked
        return out
    if not order or (tier == "rotina" and not claude_ok and not reasons):
        reasons.append("nenhum provedor configurado")
    raise BrainUnavailable("; ".join(reasons) or "nenhum provedor configurado")


def parse_json(text):
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):] if "{" in t else t
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        return json.loads(t[i:j + 1])
    except json.JSONDecodeError:
        return None
