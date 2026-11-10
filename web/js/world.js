// Cena da cidade operacional. Posições são ILUSTRATIVAS (layout), não geográficas.
import { to, motion } from "./anim.js";
import { esc } from "./api.js";

const REGION_POS = { US: [-95, 0, -45], PT: [95, 0, -45], BR: [-5, 0, 80] };
const REGION_TINT = { US: [0.10, 0.14, 0.24], PT: [0.13, 0.12, 0.22], BR: [0.08, 0.17, 0.17] };
const HQ = [0, 0, -10];
const SECTORS = { catalogo: [-22, 0, -30], logistica: [22, 0, -30], atendimento: [-22, 0, 10], financeiro: [22, 0, 10] };
const SECTOR_COLOR = { catalogo: [0.35, 0.30, 0.75], logistica: [0.10, 0.50, 0.55], atendimento: [0.55, 0.35, 0.20], financeiro: [0.20, 0.50, 0.30] };
export const LEVEL_RGB = { verde: [0.20, 0.83, 0.60], amarelo: [0.98, 0.75, 0.14], vermelho: [0.97, 0.44, 0.44], cinza: [0.58, 0.64, 0.72] };
export const AGENT_RGB = { disponivel: [0.37, 0.92, 0.83], executando: [0.38, 0.65, 1.0], aguardando_integracao: [0.98, 0.75, 0.14],
  aguardando_decisao: [0.65, 0.55, 0.98], falhou: [0.97, 0.44, 0.44], concluido: [0.20, 0.83, 0.60], desativado: [0.4, 0.45, 0.52] };
export const AGENT_LABEL = { disponivel: "disponível", executando: "executando", aguardando_integracao: "aguardando integração",
  aguardando_decisao: "aguardando decisão", falhou: "falhou", concluido: "concluído", desativado: "desativado" };
const AGENT_HOME = { coordenador: [0, 0, 6], estoque: SECTORS.logistica, financeiro: SECTORS.financeiro, catalogo: SECTORS.catalogo,
  analise: [-8, 0, 6], atendimento: SECTORS.atendimento, parceiros: [8, 0, 6] };
export const FLOORS = [["recepcao", "Recepção"], ["comercial", "Comercial"], ["operacao", "Operação"], ["financeiro", "Financeiro"],
  ["pendencias", "Pendências"], ["aplicativos", "Aplicativos"], ["equipe", "Equipe e agentes"]];

const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];

export class World {
  constructor(renderer, labelsEl, handlers) {
    this.r = renderer; this.labelsEl = labelsEl; this.h = handlers; this.r.reduced = motion.reduced;
    this.objs = new Map(); this.labels = []; this.agentPos = new Map(); this.filters = { levels: new Set(Object.keys(LEVEL_RGB)), region: null };
    this.r.onClick = m => m && m.pick && this.h.onSelect(m.pick.kind, m.pick.id, m.pick);
    this.r.onFrame = () => this._placeLabels();
    this.r.onCamera = () => {};
  }
  build(data) {
    this.data = data; const r = this.r; r.clear(); this.objs.clear();
    this.labelsEl.innerHTML = ""; this.labels = [];
    r.add({ pos: [0, -1, 20], scale: [520, 1, 420], color: [0.035, 0.055, 0.095], grid: 1 });
    const central = data.view === "central";
    // prédio central e setores
    const hq = r.add({ pos: HQ, scale: [16, 52, 16], color: [0.10, 0.16, 0.28], win: 1, pick: { kind: "hq", id: "hq" } });
    r.add({ pos: add3(HQ, [0, 52, 0]), scale: [10, 4, 10], color: [0.37, 0.92, 0.83], emissive: 0.6 });
    this.objs.set("hq", hq); this._label("hq", "Administração central", add3(HQ, [0, 60, 0]), { kind: "hq", id: "hq" });
    if (central) for (const s of data.sectors) {
      const m = r.add({ pos: SECTORS[s.code], scale: [12, 12, 12], color: SECTOR_COLOR[s.code], win: 1, pick: { kind: "sector", id: s.code, data: s } });
      this.objs.set("sector:" + s.code, m); this._label("sector:" + s.code, `${s.name}<small> · ${esc(s.metric)}</small>`, add3(SECTORS[s.code], [0, 15, 0]), { kind: "sector", id: s.code }, true);
    }
    // regiões
    for (const reg of data.regions) {
      const rp = REGION_POS[reg.code]; if (!rp) continue;
      const hasPartner = data.partners.some(p => p.region_code === reg.code); if (!central && !hasPartner) continue;
      const plat = r.add({ pos: rp, scale: [74, 0.6, 70], color: REGION_TINT[reg.code], pick: { kind: "region", id: reg.code, data: reg } });
      this.objs.set("region:" + reg.code, plat);
      this._label("region:" + reg.code, `${esc(reg.name)} <small>${reg.sales_enabled ? "vendas liberadas" : "vendas bloqueadas"}</small>`, add3(rp, [-26, 3, -33]), { kind: "region", id: reg.code }, false);
      r.lines.push({ a: add3(HQ, [0, 40, 0]), b: add3(rp, [-20, 7, -18]), color: [0.37, 0.92, 0.83, 0.35] });
    }
    for (const w of data.warehouses) {
      const rp = REGION_POS[w.region_code]; if (!rp) continue;
      const pos = add3(rp, [-20, 0.6, -18]);
      const m = r.add({ pos, scale: [24, 7, 13], color: [0.22, 0.26, 0.33], pick: { kind: "warehouse", id: w.id, data: w } });
      if (w.low) r.add({ pos: add3(pos, [0, 7, 0]), scale: [24, 0.5, 13], color: LEVEL_RGB.amarelo, emissive: 0.5, alpha: 0.7 });
      this.objs.set("warehouse:" + w.id, m);
      this._label("warehouse:" + w.id, `${esc(w.name)} <small>${w.stock.on_hand - w.stock.reserved} disp. · ${w.stock.reserved} reserv.</small>`, add3(pos, [0, 10, 0]), { kind: "warehouse", id: w.id }, true);
    }
    // parceiros
    const byRegion = {};
    for (const p of data.partners) (byRegion[p.region_code] = byRegion[p.region_code] || []).push(p);
    for (const [code, list] of Object.entries(byRegion)) {
      const rp = REGION_POS[code];
      list.forEach((p, i) => {
        const pos = add3(rp, [-14 + (i % 3) * 19, 0.6, 6 + Math.floor(i / 3) * 22]);
        const lvl = p.status.level, h = 20 + Math.min(p.units.length, 4) * 5;
        const visible = this._passes(p);
        const base = r.add({ pos: add3(pos, [0, 0, 0]), scale: [13, 0.5, 13], color: LEVEL_RGB[lvl], emissive: 0.35, alpha: 0.55, visible });
        const b = r.add({ pos, scale: [9, h, 9], color: [0.11, 0.16, 0.26], win: 1, visible, pick: { kind: "partner", id: p.id, data: p } });
        const beacon = r.add({ pos: add3(pos, [0, h, 0]), scale: [4, 1.6, 4], color: LEVEL_RGB[lvl], emissive: 0.9, visible, pick: { kind: "status", id: p.id, data: p } });
        p.units.forEach((u, j) => r.add({ pos: add3(pos, [7.5, 0, -3 + j * 4]), scale: [3, 3.5, 3], color: u.location_status === "confirmada" ? [0.25, 0.45, 0.6] : [0.22, 0.25, 0.3], visible,
          pick: { kind: "unit", id: u.id, data: { ...u, partner: p.name } } }));
        this.objs.set("partner:" + p.id, b); this.objs.set("partnerparts:" + p.id, [base, beacon]);
        b.home = pos; b.h = h;
        this._label("partner:" + p.id, `<span class="sw" style="background:rgb(${LEVEL_RGB[lvl].map(x => x * 255 | 0)})"></span>${esc(p.name)} <small>· ${statusText(lvl)}</small>`,
          add3(pos, [0, h + 4, 0]), { kind: "partner", id: p.id }, false, () => this._passes(p));
        r.lines.push({ a: add3(rp, [-20, 7, -18]), b: add3(pos, [0, 2, 0]), color: [0.65, 0.55, 0.98, visible ? 0.35 : 0.05] });
      });
    }
    this._buildAgents(data.agents);
    if (this.inside) this.enterBuilding(this.inside, true);
    r.dirty = true;
  }
  _buildAgents(agents) {
    for (const a of agents) {
      const target = this._agentTarget(a); const prev = this.agentPos.get(a.id) || target; const cur = { x: prev[0], z: prev[2] };
      const col = AGENT_RGB[a.status] || AGENT_RGB.disponivel; const alpha = a.enabled ? 1 : 0.45;
      const body = this.r.add({ pos: [cur.x, 0.6, cur.z], scale: [1.8, 3.4, 1.8], color: col, emissive: a.status === "executando" ? 0.6 : 0.15, alpha, pick: { kind: "agent", id: a.id, data: a } });
      const head = this.r.add({ pos: [cur.x, 4.2, cur.z], scale: [1.5, 1.5, 1.5], color: [0.9, 0.92, 0.96], alpha, pick: { kind: "agent", id: a.id, data: a } });
      const key = "agent:" + a.id; this.objs.set(key, body);
      const lp = [cur.x, 7.5, cur.z];
      const lbl = this._label(key, `<span class="sw" style="background:rgb(${col.map(x => x * 255 | 0)})"></span>${esc(a.name.split(" · ")[0])} <small>${esc(a.role)} · ${AGENT_LABEL[a.status] || a.status}</small>`, lp, { kind: "agent", id: a.id }, true, null, "agent");
      if (prev[0] !== target[0] || prev[2] !== target[2]) {
        to(cur, { x: target[0], z: target[2], duration: 2.2, ease: "power3.inOut", onUpdate: () => {
          body.pos = [cur.x, 0.6, cur.z]; head.pos = [cur.x, 4.2, cur.z]; lbl.p = [cur.x, 7.5, cur.z]; this.r.dirty = true; } });
      }
      this.agentPos.set(a.id, target);
    }
  }
  _agentTarget(a) {
    const idx = (this.data.agents.indexOf(a) % 4) * 2.4 - 3.6;
    if (a.current_partner_id) { const b = this.objs.get("partner:" + a.current_partner_id); if (b) return add3(b.home, [-7, 0, 6 + idx * 0.5]); }
    const home = AGENT_HOME[a.code] || [0, 0, 8];
    return a.enabled ? add3(home, [idx, 0, 9]) : add3(HQ, [idx * 1.5, 0, 22]);
  }
  _label(key, html, p, pick, small, visibleFn, cls) {
    if (this.r.lite && cls === "agent") { /* em modo leve, avatares sem rótulo flutuante (dados seguem nas tabelas) */ }
    const el = document.createElement("button"); el.className = "lbl" + (cls ? " " + cls : ""); el.innerHTML = html; el.type = "button";
    el.setAttribute("aria-label", el.textContent);
    el.addEventListener("click", ev => { ev.stopPropagation(); this.h.onSelect(pick.kind, pick.id, pick); });
    this.labelsEl.appendChild(el);
    const L = { key, el, p, small, visibleFn, cls }; this.labels.push(L); return L;
  }
  _placeLabels() {
    const zoomedOut = this.r.cam.dist > 250;
    for (const L of this.labels) {
      const vis = (!L.visibleFn || L.visibleFn()) && !(this.r.lite && L.cls === "agent") && !(zoomedOut && L.small) && !(this.inside && L.cls === "agent" && false);
      const s = vis ? this.r.project(L.p) : null;
      if (!s) { L.el.style.display = "none"; continue; }
      L.el.style.display = ""; L.el.style.left = s.x + "px"; L.el.style.top = s.y + "px"; L.el.style.zIndex = String(1000 - Math.round(s.depth * 500));
    }
  }
  _passes(p) { return this.filters.levels.has(p.status.level) && (!this.filters.region || this.filters.region === p.region_code); }
  setFilters(f) { Object.assign(this.filters, f); if (this.data) this.build(this.data); }
  fly(target, dist, pitch = 0.75, yaw) {
    const asp = this.r.canvas.clientWidth / Math.max(1, this.r.canvas.clientHeight);
    if (asp < 1) dist *= Math.min(2.2, 0.95 / asp); // tela em pé (celular): afasta a câmera para caber
    to(this.r.cam, { tx: target[0], ty: target[1], tz: target[2], dist, pitch, ...(yaw !== undefined ? { yaw } : {}), duration: 1.3, ease: "power3.inOut",
      onUpdate: () => (this.r.dirty = true) });
  }
  focus(kind, id) {
    if (kind === "global") return this.fly([0, 0, 10], 260, 0.85, 0.5);
    if (kind === "region") return this.fly(add3(REGION_POS[id], [0, 0, 5]), 120, 0.8);
    if (kind === "partner") { const b = this.objs.get("partner:" + id); if (b) this.fly(add3(b.home, [0, b.h / 2, 0]), 70, 0.45); return; }
    if (kind === "hq") return this.fly(add3(HQ, [0, 15, 0]), 110, 0.6);
    if (kind === "sector") return this.fly(SECTORS[id], 55, 0.6);
    if (kind === "warehouse") { const m = this.objs.get("warehouse:" + id); if (m) this.fly(m.pos, 60, 0.6); }
    if (kind === "agent") { const m = this.objs.get("agent:" + id); if (m) this.fly(m.pos, 40, 0.5); }
  }
  // "Entrar" no prédio: o volume fica translúcido e os andares se separam, cada um clicável.
  enterBuilding(pid, instant) {
    this.exitBuilding(true); this.inside = pid; const b = this.objs.get("partner:" + pid); if (!b) return;
    b.alpha = 0.12; this.floorMeshes = [];
    FLOORS.forEach(([code, name], i) => {
      const y = { v: instant || motion.reduced ? 2 + i * 7 : 0 };
      const m = this.r.add({ pos: [b.home[0], y.v, b.home[2]], scale: [12, 1.2, 12], color: [0.16, 0.26, 0.40], emissive: 0.15, pick: { kind: "floor", id: code, partner: pid } });
      const L = this._label("floor:" + code, name, [b.home[0] + 9, y.v + 1, b.home[2]], { kind: "floor", id: code, partner: pid }, false);
      this.floorMeshes.push([m, L]);
      if (!instant) to(y, { v: 2 + i * 7, duration: 0.9 + i * 0.07, ease: "power3.inOut", onUpdate: () => { m.pos = [b.home[0], y.v, b.home[2]]; L.p = [b.home[0] + 9, y.v + 1, b.home[2]]; this.r.dirty = true; } });
    });
    this.r.dirty = true;
  }
  highlightFloor(code) {
    (this.floorMeshes || []).forEach(([m, L]) => { const on = m.pick.id === code; m.color = on ? [0.37, 0.92, 0.83] : [0.16, 0.26, 0.40]; m.emissive = on ? 0.5 : 0.15; L.el.style.borderColor = on ? "var(--accent)" : ""; });
    this.r.dirty = true;
  }
  exitBuilding(silent) {
    if (!this.inside) return; const b = this.objs.get("partner:" + this.inside); if (b) b.alpha = 1;
    for (const [m, L] of this.floorMeshes || []) { this.r.meshes.splice(this.r.meshes.indexOf(m), 1); L.el.remove(); this.labels.splice(this.labels.indexOf(L), 1); }
    this.floorMeshes = []; this.inside = null; this.r.dirty = true;
  }
  // Pulso apenas para eventos REAIS recebidos do servidor.
  pulseFor(ev) {
    if (!ev.partner_id) return; const b = this.objs.get("partner:" + ev.partner_id); if (!b) return;
    const color = ev.type.startsWith("pagamento") ? [0.20, 0.83, 0.60] : ev.type.startsWith("estoque") ? [0.98, 0.75, 0.14]
      : ev.type.startsWith("comissao") ? [0.65, 0.55, 0.98] : ev.type.startsWith("envio") ? [0.38, 0.65, 1.0] : [0.37, 0.92, 0.83];
    const reg = REGION_POS[(this.data.partners.find(p => p.id === ev.partner_id) || {}).region_code];
    const from = ev.type.startsWith("estoque") || ev.type.startsWith("envio") ? add3(reg, [-20, 7, -18]) : add3(HQ, [0, 40, 0]);
    this.r.pulse(from, add3(b.home, [0, b.h, 0]), color);
  }
}

export function statusText(l) {
  return { verde: "sem pendências críticas", amarelo: "atenção", vermelho: "problema crítico", cinza: "sem dados atualizados" }[l] || l;
}
