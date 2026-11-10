// Cliente HTTP e assinatura de eventos em tempo real com retomada.
export async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", credentials: "same-origin", headers: { "X-Requested-With": "plataforma" } };
  if (opts.body !== undefined) { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
  if (opts.headers) Object.assign(init.headers, opts.headers);
  const r = await fetch(path, init);
  let j = null; try { j = await r.json(); } catch { /* resposta não-JSON */ }
  if (!r.ok || !j || j.ok === false) {
    const e = new Error((j && j.message) || `Erro HTTP ${r.status}`); e.status = r.status; e.code = j && j.error; throw e;
  }
  return j.data;
}

// Estados: conectando | ao_vivo | reconectando | atrasado | desconectado
export class Live {
  constructor({ onEvent, onState, onResync }) {
    this.onEvent = onEvent; this.onState = onState; this.onResync = onResync;
    this.lastId = 0; this.lastSignal = Date.now(); this.state = "conectando"; this.lostAt = null;
    this.watch = setInterval(() => this._watchdog(), 3000);
  }
  start(afterId) {
    this.lastId = afterId || this.lastId;
    this._open();
  }
  _open() {
    if (this.es) this.es.close();
    this.es = new EventSource(`/api/stream?after=${this.lastId}`);
    this.es.onopen = () => {
      const wasLost = this.lostAt !== null;
      this._set("ao_vivo"); this.lastSignal = Date.now();
      if (wasLost) { this.lostAt = null; this.onResync && this.onResync(); }
    };
    this.es.addEventListener("evento", m => {
      this.lastSignal = Date.now();
      const ev = JSON.parse(m.data);
      if (ev.id <= this.lastId) return; // proteção contra entrega repetida
      this.lastId = ev.id; this.onEvent(ev);
    });
    this.es.addEventListener("ping", m => { this.lastSignal = Date.now(); if (this.state !== "ao_vivo") this._set("ao_vivo");
      try { this.onPing && this.onPing(JSON.parse(m.data)); } catch {} });
    this.es.onerror = () => {
      if (this.lostAt === null) this.lostAt = new Date();
      this._set("reconectando");
      // EventSource reconecta sozinho enviando Last-Event-ID; se fechar, reabrimos a partir do último id conhecido.
      if (this.es.readyState === EventSource.CLOSED) setTimeout(() => this._open(), 3000);
    };
  }
  _watchdog() {
    const silent = (Date.now() - this.lastSignal) / 1000;
    if (this.state === "ao_vivo" && silent > 25) { this.lostAt = this.lostAt || new Date(); this._set("atrasado"); }
    if (this.state === "reconectando" && silent > 20) this._set("desconectado");
    if (this.state === "atrasado" && silent > 45) this._open();
  }
  _set(s) { if (s !== this.state) { this.state = s; this.onState(s, this.lostAt); } }
}

export const fmtMoney = (minor, cur) => {
  if (minor === null || minor === undefined) return "—";
  try { return new Intl.NumberFormat("pt-BR", { style: "currency", currency: cur || "BRL" }).format(minor / 100); }
  catch { return `${cur} ${(minor / 100).toFixed(2)}`; }
};
export const fmtTime = iso => iso ? new Date(iso).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "medium" }) : "—";
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
