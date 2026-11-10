"""Supervisor do servidor (usado pelos iniciar*.bat e pelo run.sh).

- Arquivos do sistema mudaram (atualização salva no disco)  → o servidor termina o que está fazendo e reinicia sozinho.
- O servidor caiu por erro                                  → reinicia com espera crescente (2 s, 5 s, 10 s, 30 s, 60 s).
- Ctrl+C / fechar a janela                                  → desliga normalmente.
- Já existe um servidor nessa porta                         → avisa e não abre outro.
Registro: data/logs/supervisor.log

Uso: python rodar.py [--host 127.0.0.1] [--port 8000]      (ambiente vem de APP_ENV)
"""
from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EXIT_UPDATE = 3
BACKOFF = (2, 5, 10, 30, 60)


def log(msg):
    line = f"[{datetime.now().strftime('%d/%m %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        d = Path(os.environ.get("DATA_DIR") or ROOT / "data") / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "supervisor.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def port_busy(host, port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def child(host, port, watch_s):
    """Processo filho: roda o uvicorn e sai com código 3 quando os arquivos mudam."""
    sys.path.insert(0, str(HERE))
    import uvicorn
    from app import saude
    base = saude.BUILD
    server = uvicorn.Server(uvicorn.Config("app.main:create_app", factory=True, host=host, port=port, timeout_graceful_shutdown=3))
    flag = {"update": False}

    def watch():
        seen = None
        while not server.should_exit:
            time.sleep(watch_s)
            fp = saude.fingerprint()
            if fp == base:
                seen = None
                continue
            if fp == seen:              # igual em duas leituras seguidas: cópia terminou
                flag["update"] = True
                server.should_exit = True
                return
            seen = fp

    threading.Thread(target=watch, daemon=True).start()
    server.run()
    sys.exit(EXIT_UPDATE if flag["update"] else 0)


def supervise(host, port, watch_s, max_restarts=None):
    if port_busy(host, port):
        log(f"Já existe um servidor em http://{host}:{port} — use esse (feche a outra janela se quiser reiniciar).")
        return 0
    crashes, restarts = [], 0
    while True:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--filho", "--host", host, "--port", str(port), "--watch", str(watch_s)]
        p = subprocess.Popen(cmd, cwd=str(HERE))
        try:
            code = p.wait()
        except KeyboardInterrupt:
            try:
                p.wait(timeout=10)
            except (subprocess.TimeoutExpired, KeyboardInterrupt):
                p.kill()
            log("Servidor desligado.")
            return 0
        restarts += 1
        if max_restarts is not None and restarts > max_restarts:
            return code
        if code == 0:
            log("Servidor desligado.")
            return 0
        if code == EXIT_UPDATE:
            log("Arquivos atualizados — reiniciando com a versão nova…")
            crashes.clear()
            continue
        t = time.time()
        crashes = [c for c in crashes if t - c < 600] + [t]
        wait = BACKOFF[min(len(crashes) - 1, len(BACKOFF) - 1)]
        log(f"O servidor parou com erro (código {code}). Nova tentativa em {wait} s — veja a mensagem acima. "
            f"Falhas nos últimos 10 min: {len(crashes)}.")
        try:
            time.sleep(wait)
        except KeyboardInterrupt:
            log("Servidor desligado.")
            return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--watch", type=float, default=2.0)
    ap.add_argument("--filho", action="store_true")
    a = ap.parse_args(argv)
    if a.filho:
        child(a.host, a.port, a.watch)
    else:
        sys.exit(supervise(a.host, a.port, a.watch))


if __name__ == "__main__":
    main()
