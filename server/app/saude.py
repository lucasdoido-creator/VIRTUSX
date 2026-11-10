"""Vigia do sistema: verificações automáticas, erros de tela, cópia de segurança e alertas.

Tudo determinístico e sem custo. Roda no agendador; o resultado aparece no indicador "Saúde" do cabeçalho.
- Erros da tela (JavaScript) chegam por /api/saude/erro-tela e ficam registrados (sem dados pessoais).
- Verificações: banco, agendador, versão em execução × arquivos no disco, mapa, IA local, WhatsApp,
  webhooks na fila de falhas, agentes com falha, erros de tela, cópia de segurança e espaço em disco.
- Correções automáticas: cópia diária do banco (mantém 14), reinício quando há arquivos novos
  (feito pelo supervisor server/rodar.py usado pelos iniciar*.bat), nova tentativa de downloads de mapa.
- Alerta: verificação que fica vermelha gera evento "sistema.alerta" e UMA pendência crítica para a central.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path

from .config import ROOT
from .core import R, SYSTEM, emit
from .db import iso, now, new_id

LEVELS = ("verde", "amarelo", "vermelho", "cinza")
_rate = {"window": 0.0, "count": 0}
_cache = {"at": 0.0, "result": None, "ollama": (0.0, None), "prev": {}}
_lock = threading.Lock()


# ------------------------------------------------------------------ versão (impressão digital dos arquivos)
def fingerprint() -> str:
    h = hashlib.sha1()
    files = sorted(list((ROOT / "server" / "app").glob("*.py")) + [p for p in (ROOT / "web").rglob("*")
                   if p.is_file() and p.suffix in (".js", ".css", ".html") and "vendor" not in p.parts])
    for p in files:
        try:
            st = p.stat()
            h.update(f"{p.relative_to(ROOT).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
        except OSError:
            pass
    return h.hexdigest()[:12]


BUILD = fingerprint()          # versão carregada na memória deste processo
STARTED_AT = iso()


# ------------------------------------------------------------------ erros da tela
def report_client_error(actor, data: dict, user_agent: str = "") -> dict:
    t = time.time()
    with _lock:
        if t - _rate["window"] > 60:
            _rate["window"], _rate["count"] = t, 0
        _rate["count"] += 1
        if _rate["count"] > 60:
            return {"accepted": False, "reason": "limite"}
    msg = str(data.get("message") or "")[:500]
    if not msg:
        return {"accepted": False, "reason": "vazio"}
    src = str(data.get("source") or "")[:200]
    line = int(data.get("line") or 0) if str(data.get("line") or "0").isdigit() else 0
    where = str(data.get("where") or "")[:80]
    build = str(data.get("build") or "")[:20]
    key = hashlib.sha1(f"{msg}|{src}|{line}|{where}".encode()).hexdigest()[:20]
    ts = iso()
    uid = getattr(actor, "id", None) if actor is not None and getattr(actor, "kind", "") == "user" else None
    with R.db.tx() as c:
        row = c.execute("SELECT id, count FROM client_errors WHERE key=?", (key,)).fetchone()
        if row:
            c.execute("UPDATE client_errors SET count=count+1, last_at=?, build=?, resolved=0 WHERE id=?", (ts, build, row[0]))
            first = False
        else:
            c.execute("INSERT INTO client_errors(id,key,message,source,line,where_,stack,build,server_build,user_agent,user_id,first_at,last_at,count,resolved)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)",
                      (new_id("err"), key, msg, src, line, where, str(data.get("stack") or "")[:2000], build, BUILD,
                       (user_agent or "")[:200], uid, ts, ts))
            first = True
            emit(c, "sistema.erro_tela", "vigia", "client_error", key, status="falhou",
                 payload={"mensagem": msg[:160], "onde": where, "versao_tela": build, "versao_servidor": BUILD})
    return {"accepted": True, "new": first}


def list_client_errors(limit=50):
    return R.db.all("SELECT key, message, source, line, where_ AS onde, build, server_build, count, first_at, last_at, resolved"
                    " FROM client_errors ORDER BY last_at DESC LIMIT ?", limit)


def resolve_client_errors(actor):
    actor.require("sistema.saude")
    with R.db.tx() as c:
        n = c.execute("UPDATE client_errors SET resolved=1 WHERE resolved=0").rowcount
    return {"resolved": n}


# ------------------------------------------------------------------ cópia de segurança
def backup_dir() -> Path:
    d = R.settings.data_dir / "backups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def last_backup():
    files = sorted(backup_dir().glob(f"{R.settings.env}_*.sqlite3"))
    return files[-1] if files else None


def backup_now(keep=14) -> Path:
    stamp = now().strftime("%Y%m%d-%H%M%S")
    dst = backup_dir() / f"{R.settings.env}_{stamp}.sqlite3"
    tmp = dst.with_suffix(".tmp")
    src = sqlite3.connect(str(R.settings.db_path), timeout=30)
    try:
        out = sqlite3.connect(str(tmp))
        try:
            src.backup(out)                       # cópia consistente, com o sistema rodando
            ok = out.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            out.close()
    finally:
        src.close()
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError("cópia gerada não passou na verificação")
    tmp.replace(dst)
    for old in sorted(backup_dir().glob(f"{R.settings.env}_*.sqlite3"))[:-keep]:
        old.unlink(missing_ok=True)
    return dst


def backup_if_due(hours=24):
    lb = last_backup()
    if lb and time.time() - lb.stat().st_mtime < hours * 3600:
        return None
    p = backup_now()
    with R.db.tx() as c:
        emit(c, "sistema.backup", "vigia", payload={"arquivo": p.name, "bytes": p.stat().st_size})
    return p


# ------------------------------------------------------------------ verificações
def _ck(code, title, level, detail, auto=""):
    return {"code": code, "title": title, "level": level, "detail": detail, "auto": auto}


def _ollama_ok():
    at, val = _cache["ollama"]
    if time.time() - at < 60 and val is not None:
        return val
    try:
        import httpx
        r = httpx.get(R.settings.ollama_url.rstrip("/") + "/api/tags", timeout=2)
        names = [m.get("name", "") for m in r.json().get("models", [])]
        val = (True, R.settings.ollama_model in names or any(n.startswith(R.settings.ollama_model) for n in names))
    except Exception:
        val = (False, False)
    _cache["ollama"] = (time.time(), val)
    return val


def run_checks(force_key=False) -> dict:
    from . import geo
    from .main import SCHED
    S = R.settings
    out = []
    # banco
    try:
        qc = R.db.val("PRAGMA quick_check")
        out.append(_ck("banco", "Banco de dados", "verde" if qc == "ok" else "vermelho",
                       "íntegro" if qc == "ok" else f"problema: {qc}", "cópia diária automática"))
    except Exception as e:
        out.append(_ck("banco", "Banco de dados", "vermelho", f"falha ao verificar: {e}"))
    # agendador
    if not S.scheduler_enabled:
        out.append(_ck("agendador", "Agendador", "cinza", "desligado nesta execução"))
    else:
        lt = SCHED.get("last_tick")
        age = (now() - _parse(lt)).total_seconds() if lt else None
        lim = S.scheduler_interval_s * 3 + 30
        lvl = "verde" if age is not None and age < lim else ("amarelo" if age is None else "vermelho")
        out.append(_ck("agendador", "Agendador (rotinas automáticas)", lvl,
                       f"última volta há {int(age)} s" if age is not None else "ainda não rodou",
                       f"último erro: {SCHED['last_error']}" if SCHED.get("last_error") else ""))
    # versão
    disk = fingerprint()
    out.append(_ck("versao", "Versão em execução", "verde" if disk == BUILD else "amarelo",
                   f"{BUILD} desde {STARTED_AT[:16].replace('T', ' ')}" if disk == BUILD else
                   f"arquivos novos no disco ({disk}) — o servidor reinicia sozinho em segundos se foi aberto pelo iniciar*.bat",
                   "reinício automático pelo supervisor"))
    # mapa
    gs, im = geo.status(), geo.imagery_status()
    lvl = "verde" if gs["countries"] and im["world"] else "amarelo"  # sem mapa o sistema segue funcionando (tabelas)
    out.append(_ck("mapa", "Mapa real e satélite", lvl,
                   ("países ok" if gs["countries"] else "países não baixados") + " · " + ("satélite ok" if im["world"] else "satélite não baixado")
                   + (f" · erro: {gs['error'] or im['error']}" if (gs["error"] or im["error"]) else ""),
                   "novo download automático ao abrir o mapa"))
    # IA local
    up, has_model = _ollama_ok()
    out.append(_ck("ia_local", "IA grátis local (Ollama)", "verde" if up and has_model else "amarelo",
                   "ligada com o modelo " + S.ollama_model if up and has_model else
                   ("ligada, mas sem o modelo " + S.ollama_model if up else "desligada — o sistema segue com regras fixas"),
                   "o iniciar*.bat liga o Ollama"))
    # chave da Claude (reserva paga dos CEOs) — teste sem custo
    from . import brain
    k = brain.key_check(force=force_key)
    extra = f" · chave {k['chave']} lida de {k['origem']}" if k.get("configurada") else ""
    if k.get("substituiu_variavel_antiga"):
        extra += " (havia uma chave antiga no Windows; a do arquivo foi usada)"
    out.append(_ck("claude", "Claude (chave da API)", k["nivel"], k["detalhe"] + extra, "teste automático a cada hora, sem gastar crédito"))
    # WhatsApp
    from . import channels
    if not channels.configured():
        out.append(_ck("whatsapp", "WhatsApp", "cinza", "não configurado" + (" (demo usa simulação)" if S.is_demo else "")))
    else:
        fails = R.db.val("SELECT COUNT(*) FROM messages WHERE direction='out' AND status='falhou' AND created_at>?", iso(now() - _td(hours=24))) or 0
        last_in = R.db.val("SELECT MAX(created_at) FROM messages WHERE direction='in' AND is_simulated=0")
        out.append(_ck("whatsapp", "WhatsApp", "amarelo" if fails else "verde",
                       f"{fails} envio(s) com falha em 24 h" if fails else
                       ("última mensagem recebida: " + last_in[:16].replace("T", " ") if last_in else "configurado; nenhuma mensagem real recebida ainda")))
    # webhooks
    dead = R.db.val("SELECT COUNT(*) FROM webhook_inbox WHERE status='dead_letter'") or 0
    out.append(_ck("webhooks", "Integrações (webhooks)", "vermelho" if dead else "verde",
                   f"{dead} na fila de falhas" if dead else "sem falhas pendentes", "novas tentativas automáticas"))
    # agentes
    bad = R.db.all("SELECT name FROM agents WHERE enabled=1 AND status='falhou'")
    out.append(_ck("agentes", "Agentes", "amarelo" if bad else "verde",
                   ("com falha: " + ", ".join(a["name"] for a in bad[:5])) if bad else "nenhum com falha"))
    # erros de tela
    n = R.db.val("SELECT COALESCE(SUM(count),0) FROM client_errors WHERE resolved=0 AND last_at>?", iso(now() - _td(hours=24))) or 0
    out.append(_ck("tela", "Erros na tela (navegador)", "verde" if not n else ("amarelo" if n < 20 else "vermelho"),
                   f"{n} ocorrência(s) nas últimas 24 h" if n else "nenhum nas últimas 24 h", "registrados automaticamente"))
    # backup
    lb = last_backup()
    age_h = (time.time() - lb.stat().st_mtime) / 3600 if lb else None
    out.append(_ck("backup", "Cópia de segurança", "verde" if age_h is not None and age_h < 30 else "amarelo",
                   f"última há {age_h:.0f} h ({lb.name})" if lb else "nenhuma ainda — a primeira sai na próxima volta do agendador",
                   "diária, guarda 14 cópias em data/backups"))
    # disco
    try:
        free = shutil.disk_usage(S.data_dir).free / 1e9
        out.append(_ck("disco", "Espaço em disco", "verde" if free > 1 else ("amarelo" if free > 0.2 else "vermelho"), f"{free:.1f} GB livres"))
    except OSError:
        pass
    worst = "vermelho" if any(x["level"] == "vermelho" for x in out) else ("amarelo" if any(x["level"] == "amarelo" for x in out) else "verde")
    res = {"level": worst, "checked_at": iso(), "build": BUILD, "disk_build": disk, "started_at": STARTED_AT, "checks": out}
    _cache["at"], _cache["result"] = time.time(), res
    return res


def _td(**kw):
    from datetime import timedelta
    return timedelta(**kw)


def _parse(s):
    from datetime import datetime
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _alert(res):
    """Transição para vermelho → evento + uma pendência crítica (sem duplicar enquanto estiver aberta)."""
    from . import tasks
    prev = _cache["prev"]
    for ck in res["checks"]:
        before = prev.get(ck["code"])
        prev[ck["code"]] = ck["level"]
        if ck["level"] == "vermelho" and before != "vermelho":
            with R.db.tx() as c:
                emit(c, "sistema.alerta", "vigia", "check", ck["code"], status="falhou", payload={"verificacao": ck["title"], "detalhe": ck["detail"][:200]})
                ref = f"saude:{ck['code']}"
                if not c.execute("SELECT 1 FROM tasks WHERE related_entity=? AND status NOT IN ('concluida','cancelada')", (ref,)).fetchone():
                    tasks.create_task(c, SYSTEM, {"title": f"Alerta do sistema: {ck['title']}", "description": ck["detail"][:500],
                                                  "kind": "pendencia", "pendency_type": "falha_integracao", "priority": "critica",
                                                  "source": "vigia", "related_entity": ref})
        elif before == "vermelho" and ck["level"] != "vermelho":
            with R.db.tx() as c:
                emit(c, "sistema.normalizado", "vigia", "check", ck["code"], payload={"verificacao": ck["title"]})


def routine(every_s=300):
    """Chamado a cada volta do agendador; trabalho pesado no máximo a cada `every_s`."""
    if time.time() - _cache["at"] < every_s and _cache["result"] is not None:
        return _cache["result"]
    try:
        backup_if_due()
    except Exception as e:
        with R.db.tx() as c:
            emit(c, "sistema.backup_falhou", "vigia", status="falhou", payload={"erro": str(e)[:200]})
    res = run_checks()
    _alert(res)
    return res


def summary():
    """Resumo sem detalhes (pode aparecer no /api/health)."""
    r = _cache["result"]
    return {"level": r["level"] if r else "cinza", "checked_at": r["checked_at"] if r else None}


def overview(actor, refresh=False):
    actor.require("sistema.saude")
    res = run_checks(force_key=refresh) if refresh or _cache["result"] is None else _cache["result"]
    return {**res, "client_errors": list_client_errors(30),
            "backups": [{"file": p.name, "bytes": p.stat().st_size} for p in sorted(backup_dir().glob(f"{R.settings.env}_*.sqlite3"))[-5:]]}
