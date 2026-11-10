// Mundo operacional sobre o MAPA REAL (países e estados do Natural Earth, projeção equiretangular).
// Posição = coordenada da cidade (aproximada) ou do endereço confirmado; o painel sempre diz qual é a fonte.
import { to, motion } from "./anim.js";
import { esc } from "./api.js";

export const K = 200;  // unidades do mundo por grau (1 unidade ≈ 0,55 km: prédios não cobrem cidades vizinhas)
const F = K / 40;     // distâncias de câmera/rótulos em escala de mapa acompanham K
const ll = (lat, lng) => [lng * K, 0, -lat * K];
const SECTOR_OFF = { catalogo: [-20, 0, -20], logistica: [20, 0, -20], atendimento: [-20, 0, 20], financeiro: [20, 0, 20] };
const SECTOR_COLOR = { catalogo: [0.35, 0.30, 0.75], logistica: [0.10, 0.50, 0.55], atendimento: [0.55, 0.35, 0.20], financeiro: [0.20, 0.50, 0.30] };
export const LEVEL_RGB = { verde: [0.20, 0.83, 0.60], amarelo: [0.98, 0.75, 0.14], vermelho: [0.97, 0.44, 0.44], cinza: [0.58, 0.64, 0.72] };
export const AGENT_RGB = { disponivel: [0.37, 0.92, 0.83], executando: [0.38, 0.65, 1.0], aguardando_integracao: [0.98, 0.75, 0.14],
  aguardando_decisao: [0.65, 0.55, 0.98], falhou: [0.97, 0.44, 0.44], concluido: [0.20, 0.83, 0.60], desativado: [0.4, 0.45, 0.52] };
export const AGENT_LABEL = { disponivel: "disponível", executando: "executando", aguardando_integracao: "aguardando integração",
  aguardando_decisao: "aguardando decisão", falhou: "falhou", concluido: "concluído", desativado: "desativado" };
const AGENT_HOME = { coordenador: [0, 0, 26], estoque: SECTOR_OFF.logistica, financeiro: SECTOR_OFF.financeiro, catalogo: SECTOR_OFF.catalogo,
  analise: [-8, 0, 26], atendimento: SECTOR_OFF.atendimento, parceiros: [8, 0, 26], pesquisa: [-14, 0, 30], conteudo: [14, 0, 30] };
export const FLOORS = [["recepcao", "Recepção"], ["comercial", "Comercial"], ["operacao", "Operação"], ["financeiro", "Financeiro"],
  ["pendencias", "Pendências"], ["aplicativos", "Aplicativos"], ["equipe", "Equipe e agentes"], ["atendimento", "Atendimento · WhatsApp"],
  ["diretoria", "Diretoria · CEO de IA"]];

const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const COUNTRY_PT = { Brazil: "Brasil", Portugal: "Portugal", "United States of America": "Estados Unidos" };

const LABEL_PRIO = { floor: 11, region: 10, hq: 9, market: 8.7, base: 8.5, partner: 8, freelancer: 5.5, unit: 6, warehouse: 5, sector: 4, estado: 3, agent: 2 };
const labelPrio = L => L.prio ?? (L.prio = (L.cls || "").includes("ceo") ? 4.5 : (LABEL_PRIO[L.key.split(":")[0]] ?? 1));
const loadImg = src => new Promise((ok, fail) => { const i = new Image(); i.onload = () => ok(i); i.onerror = fail; i.src = src; });
function pip(lon, lat, ring) {  // ponto no polígono (anel achatado [lon,lat,...])
  let inside = false;
  for (let i = 0, j = ring.length - 2; i < ring.length; j = i, i += 2) {
    const xi = ring[i], yi = ring[i + 1], xj = ring[j], yj = ring[j + 1];
    if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}
function hitArea(list, lon, lat) {
  for (const c of list) { const b = c.bbox; if (lon < b[0] || lon > b[2] || lat < b[1] || lat > b[3]) continue;
    if (c.rings.some(r => pip(lon, lat, r))) return c; }
  return null;
}
function segments(list, y) {
  let n = 0; for (const c of list) for (const r of c.rings) n += r.length / 2;
  const out = new Float32Array(n * 6); let k = 0;
  for (const c of list) for (const r of c.rings) for (let i = 0; i < r.length; i += 2) {
    const j = (i + 2) % r.length; out[k++] = r[i] * K; out[k++] = y; out[k++] = -r[i + 1] * K; out[k++] = r[j] * K; out[k++] = y; out[k++] = -r[j + 1] * K;
  }
  return out.subarray(0, k);
}

export class World {
  constructor(renderer, labelsEl, handlers) {
    this.r = renderer; this.labelsEl = labelsEl; this.h = handlers; this.r.reduced = motion.reduced; this.r.K = K; this.r.maxDist = 14000 * F;
    this.objs = new Map(); this.labels = []; this.agentPos = new Map(); this.filters = { levels: new Set(Object.keys(LEVEL_RGB)), region: null };
    this.countries = []; this.states = null; this.tip = document.getElementById("hoverTip");
    this.r.onClick = (m, g) => {
      if (m && m.pick) return this.h.onSelect(m.pick.kind, m.pick.id, m.pick);
      if (!g) return; const lon = g[0] / K, lat = -g[2] / K;
      const st = this.states && hitArea(this.states.states, lon, lat);
      if (st) return this.h.onSelect("state", st.name, { data: st, country: this.states.country });
      const c = hitArea(this.countries, lon, lat); if (c) this.h.onSelect("country", c.name, { data: c });
    };
    let pending = false;
    this.r.onHover = e => { if (pending || !this.tip) return; pending = true; requestAnimationFrame(() => { pending = false; this._hover(e); }); };
    this.r.onFrame = () => this._placeLabels();
    let bm = null; try { bm = localStorage.getItem("mapaBase"); } catch {} this.basemap = bm || "satelite";
    this.r.onCamera = () => this._detailSoon();
    this.r.onInteract = () => { if (this._camTw) { this._camTw.kill(); this._camTw = null; } };   // o mouse manda
  }
  _hover(e) {
    const rc = this.r.canvas.getBoundingClientRect(), g = this.r.groundPoint(e.clientX - rc.left, e.clientY - rc.top);
    if (!g) { this.tip.style.display = "none"; return; }
    const lon = g[0] / K, lat = -g[2] / K, st = this.states && hitArea(this.states.states, lon, lat), c = hitArea(this.countries, lon, lat);
    if (!c && !st) { this.tip.style.display = "none"; return; }
    this.tip.textContent = st ? `${st.name} · ${c ? (c.name_pt || c.name) : ""}` : (c.name_pt || c.name);
    this.tip.style.display = ""; this.tip.style.left = (e.clientX - rc.left + 14) + "px"; this.tip.style.top = (e.clientY - rc.top + 10) + "px";
  }
  // Camada do mapa: textura (oceano/terra/países com operação) + fronteiras em lote.
  setGeo(geo) {
    if (!geo || !geo.available) return;
    this.countries = geo.countries; this._paint();
    if (!this.satReq) {  // imagem de satélite real (NASA Blue Marble); sem ela, fica o mapa vetorial
      this.satReq = true; const img = new Image();
      img.onload = () => { this.sat = img; this._paint(); this.h.onImagery && this.h.onImagery("mundo", true); this._detailSoon(); };
      img.onerror = () => { this.h.onImagery && this.h.onImagery("mundo", false); };
      img.src = "/api/geo/imagem/mundo" + (this.r.lite ? "?leve=1" : "");
    }
    this.r.addLineSet("paises", segments(this.countries, 0.25), [0.40, 0.75, 1.0, 0.55]);
  }
  _paint() {
    if (!this.countries.length) return;
    const W = this.r.lite ? 2048 : 4096, H = W / 2, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
    const g = cv.getContext("2d"), X = lon => (lon + 180) / 360 * W, Y = lat => (90 - lat) / 180 * H;
    const sat = this.sat && this.basemap !== "vetor";
    if (sat) { g.drawImage(this.sat, 0, 0, W, H); g.fillStyle = "rgba(2,8,22,0.28)"; g.fillRect(0, 0, W, H); }  // leve escurecida p/ leitura dos rótulos
    else { g.fillStyle = "#040a1a"; g.fillRect(0, 0, W, H); }
    g.strokeStyle = sat ? "rgba(255,255,255,0.06)" : "rgba(90,120,200,0.12)"; g.lineWidth = 1;
    for (let lon = -180; lon <= 180; lon += 15) { g.beginPath(); g.moveTo(X(lon), 0); g.lineTo(X(lon), H); g.stroke(); }
    for (let lat = -90; lat <= 90; lat += 15) { g.beginPath(); g.moveTo(0, Y(lat)); g.lineTo(W, Y(lat)); g.stroke(); }
    const active = new Set(Object.values((this.data && this.data.region_country) || {}).filter(n =>
      this.data.partners.some(p => (this.data.region_country || {})[p.region_code] === n)));
    for (const c of this.countries) {
      if (sat && !active.has(c.name)) continue;
      g.fillStyle = sat ? "rgba(94,234,212,0.13)" : active.has(c.name) ? "#0f3a44" : "#0e1a33"; g.beginPath();
      for (const r of c.rings) { g.moveTo(X(r[0]), Y(r[1])); for (let i = 2; i < r.length; i += 2) g.lineTo(X(r[i]), Y(r[i + 1])); g.closePath(); }
      g.fill("evenodd");
    }
    this._active = [...active].sort().join("|"); this.r.setTexture(cv);
  }
  setStates(st) {
    this.states = st && st.states && st.states.length ? st : null;
    this.labels.filter(L => L.key.startsWith("estado:")).forEach(L => { L.el.remove(); this.labels.splice(this.labels.indexOf(L), 1); });
    this.r.removeLineSet("estados");
    if (!this.states) return;
    this.r.addLineSet("estados", segments(this.states.states, 0.3), [0.75, 0.55, 1.0, 0.45]);
    for (const s of this.states.states) {
      const b = s.bbox; this._label("estado:" + s.name, esc(s.code || s.name), ll((b[1] + b[3]) / 2, (b[0] + b[2]) / 2), { kind: "state", id: s.name, data: s, country: st.country },
        false, null, "estado", [0, 2600 * F]);
    }
  }
  _place(p) {  // evita sobreposição de prédios na mesma cidade (espiral determinística)
    const cell = 12; for (let i = 0; i < 60; i++) {
      const a = i * 2.4, rr = i ? cell * (0.8 + i * 0.18) : 0, q = [p[0] + Math.cos(a) * rr, 0, p[2] + Math.sin(a) * rr];
      const key = `${Math.round(q[0] / cell)},${Math.round(q[2] / cell)}`; if (!this.occ.has(key)) { this.occ.add(key); return q; }
    }
    return p;
  }
  _coordOf(lat, lng, region) {
    if (lat != null && lng != null) return { pos: ll(lat, lng), approx: false };
    const c = ((this.data && this.data.country_center) || {})[region] || [0, 0];
    return { pos: ll(c[0], c[1]), approx: true };
  }
  _shadow(pos, w, d, h) {   // sombra suave projetada (sol a nordeste): dá volume e "assenta" os prédios no chão
    const off = Math.min(h * 0.22, 14);
    this.r.add({ pos: [pos[0] - off * 0.6, 0.04, pos[2] - off * 0.35], scale: [w + off * 0.9, 0.02, d + off * 0.5], color: [0, 0, 0], alpha: 0.32 });
  }
  _reserve(p, rad) { for (let dx = -rad; dx <= rad; dx++) for (let dz = -rad; dz <= rad; dz++) this.occ.add(`${Math.round(p[0] / 12) + dx},${Math.round(p[2] / 12) + dz}`); }
  _safe(what, fn) {  // um item com dado ruim não derruba o mapa inteiro: pula o item e avisa o vigia
    try { return fn(); } catch (e) { this.h.onError && this.h.onError(e, "3d:" + what); }
  }
  build(data) {
    data.hq = data.hq && data.hq.lat != null ? data.hq : { lat: -23.5505, lng: -46.6333 };
    for (const k of ["partners", "warehouses", "service_bases", "regions", "sectors", "agents", "markets"]) data[k] = data[k] || [];
    this.data = data; const r = this.r; r.clear(); this.objs.clear(); this.occ = new Set();
    this.labelsEl.innerHTML = ""; this.labels = [];
    r.add({ pos: [0, -1, 0], scale: [360 * K + 4000, 1, 180 * K + 4000], color: [0.02, 0.04, 0.10], tex: 1 });
    const act = Object.values(data.region_country || {}).filter(n => data.partners.some(p => (data.region_country || {})[p.region_code] === n)).sort().join("|");
    if (this.countries.length && act !== this._active) this._paint();
    const central = data.view === "central";
    const HQ = this.HQ = this._place(ll(data.hq.lat, data.hq.lng));
    const hq = r.add({ pos: HQ, scale: [16, 52, 16], color: [0.10, 0.14, 0.30], win: 1, edge: 1, winColor: [0.70, 0.55, 1.0], pick: { kind: "hq", id: "hq" } });
    r.add({ pos: add3(HQ, [0, 52, 0]), scale: [10, 4, 10], color: [0.37, 0.92, 0.83], emissive: 0.6 });
    r.add({ pos: add3(HQ, [0, 56, 0]), scale: [1.2, 22, 1.2], color: [0.65, 0.55, 1.0], emissive: 0.9 });
    for (const [y, s] of [[18, 26], [32, 23], [46, 20]]) r.add({ pos: add3(HQ, [0, y, 0]), scale: [s, 0.35, s], color: [0.37, 0.92, 0.83], emissive: 0.8, alpha: 0.28 });
    r.add({ pos: add3(HQ, [0, 0, 0]), scale: [36, 0.3, 36], color: [0.37, 0.92, 0.83], emissive: 0.4, alpha: 0.18 });  // sinal visível de longe
    this.objs.set("hq", hq); this._label("hq", `${esc(data.hq.name || "Administração central")}${data.hq.padrao ? " <small>· local padrão: informe em Cadastros → Sede</small>" : ""}`, add3(HQ, [0, 60, 0]), { kind: "hq", id: "hq" }, false, null, null, [0, 14000 * F]);
    for (const k of Object.keys(SECTOR_OFF)) this.occ.add(`${Math.round((HQ[0] + SECTOR_OFF[k][0]) / 18)},${Math.round((HQ[2] + SECTOR_OFF[k][2]) / 18)}`);
    if (central) for (const s of data.sectors) this._safe("setor", () => {
      const pos = add3(HQ, SECTOR_OFF[s.code]);
      const m = r.add({ pos, scale: [12, 12, 12], color: SECTOR_COLOR[s.code], win: 1, pick: { kind: "sector", id: s.code, data: s } });
      this.objs.set("sector:" + s.code, m); this._label("sector:" + s.code, `${s.name}<small> · ${esc(s.metric)}</small>`, add3(pos, [0, 15, 0]), { kind: "sector", id: s.code }, true);
    });
    // países com operação: rótulo visível de longe
    for (const reg of data.regions) {
      const n = (data.partners.filter(p => p.region_code === reg.code)).length; if (!n && !central) continue;
      const c = (data.country_center || {})[reg.code]; if (!c) continue;
      this._label("region:" + reg.code, `${esc(reg.name)} <small>${n} cliente(s)/parceiro(s) · ${reg.sales_enabled ? "vendas liberadas" : "vendas bloqueadas"}</small>`,
        ll(c[0], c[1]), { kind: "region", id: reg.code, data: reg }, false, null, "pais", [1800 * F, 14000 * F]);
    }
    for (const w of data.warehouses) this._safe("armazem", () => {
      const { pos: p0 } = this._coordOf(w.lat, w.lng, w.region_code), pos = this._place(p0);
      const m = r.add({ pos, scale: [24, 7, 13], color: [0.22, 0.26, 0.33], pick: { kind: "warehouse", id: w.id, data: w } });
      if (w.low) r.add({ pos: add3(pos, [0, 7, 0]), scale: [24, 0.5, 13], color: LEVEL_RGB.amarelo, emissive: 0.5, alpha: 0.7 });
      this.objs.set("warehouse:" + w.id, m);
      this._label("warehouse:" + w.id, `${esc(w.name)} <small>${w.stock.on_hand - w.stock.reserved} disp. · ${w.stock.reserved} reserv.</small>`, add3(pos, [0, 10, 0]), { kind: "warehouse", id: w.id }, true);
      r.lines.push({ a: add3(HQ, [0, 40, 0]), b: add3(pos, [0, 7, 0]), color: [0.37, 0.92, 0.83, 0.35] });
    });
    (data.service_bases || []).forEach(sb => this._safe("base", () => {
      // Base própria (filial): campus grande — torre, anexo, heliponto, antena e faixa luminosa
      const { pos: p0 } = this._coordOf(sb.lat, sb.lng, "BR"), pos = this._place(p0); sb._pos = pos;
      this._reserve(pos, 2);   // o campus ocupa mais espaço: clientes da mesma cidade ficam ao redor, não por cima
      const pick = { kind: "base", id: sb.id, data: sb }, G = [0.35, 1.0, 0.75];
      r.add({ pos, scale: [44, 0.35, 44], color: [0.05, 0.12, 0.14], grid: 1, alpha: 0.85 });
      r.add({ pos: add3(pos, [0, 0.36, 0]), scale: [46, 0.2, 46], color: G, emissive: 0.5, alpha: 0.18 });
      this._shadow(pos, 16, 14, 40);
      r.add({ pos, scale: [16, 40, 14], color: [0.08, 0.17, 0.24], win: 1, edge: 1, winColor: G, pick });
      r.add({ pos: add3(pos, [12, 0, 6]), scale: [14, 12, 16], color: [0.10, 0.20, 0.26], win: 1, winColor: [0.55, 0.95, 1.0], pick });
      r.add({ pos: add3(pos, [-11, 0, -7]), scale: [10, 18, 10], color: [0.09, 0.18, 0.25], win: 1, edge: 1, winColor: G, pick });
      r.add({ pos: add3(pos, [0, 34, 0]), scale: [16.4, 1.2, 14.4], color: G, emissive: 0.9 });              // faixa luminosa
      r.add({ pos: add3(pos, [0, 40, 0]), scale: [12, 0.5, 10], color: [0.15, 0.22, 0.28] });               // heliponto
      r.add({ pos: add3(pos, [0, 40.5, 0]), scale: [6, 0.15, 6], color: [0.95, 0.85, 0.35], emissive: 0.8 });
      r.add({ pos: add3(pos, [5, 40, 4]), scale: [0.5, 12, 0.5], color: G, emissive: 1.0 });                 // antena
      r.add({ pos: add3(pos, [5, 52, 4]), scale: [1.4, 1.4, 1.4], color: [1, 0.35, 0.35], emissive: 1.0 });
      this.objs.set("base:" + sb.id, { pos, home: pos, h: 40 });
      this._label("base:" + sb.id, `<span class="sw" style="background:#5ff5c0"></span>Base · ${esc(sb.name)} <small>${esc(sb.city || "")} · ${sb.tecnicos} técnico(s)${sb.pendentes ? ` · ${sb.pendentes} aguardando` : ""}</small>`,
        add3(pos, [0, 58, 0]), pick, false, null, null, [0, 5000 * F]);
      // casas dos freelancers ao redor da base (posição ilustrativa — não é rastreamento)
      (sb.freelancers || []).forEach((f, i, all) => {
        const ang = (i / Math.max(all.length, 1)) * Math.PI * 2 + 0.6, hp = add3(pos, [Math.cos(ang) * 38, 0, Math.sin(ang) * 38]);
        const fp = { kind: "freelancer", id: f.id, data: { ...f, base: sb.name } }, a = f.active ? 1 : 0.45;
        r.add({ pos: hp, scale: [11, 0.25, 11], color: [0.10, 0.30, 0.16], alpha: 0.9 * a });                 // jardim
        this._shadow(hp, 6, 6, 7);
        r.add({ pos: hp, scale: [6, 4, 6], color: [0.86, 0.80, 0.70], alpha: a, pick: fp });                  // casa
        r.add({ pos: add3(hp, [0, 4, 0]), scale: [6.8, 1, 6.8], color: [0.62, 0.20, 0.16], alpha: a, pick: fp });  // telhado em degraus
        r.add({ pos: add3(hp, [0, 5, 0]), scale: [4.8, 1, 4.8], color: [0.66, 0.22, 0.17], alpha: a, pick: fp });
        r.add({ pos: add3(hp, [0, 6, 0]), scale: [2.6, 0.9, 2.6], color: [0.70, 0.24, 0.18], alpha: a, pick: fp });
        r.add({ pos: add3(hp, [0, 0, 3.02]), scale: [1.3, 2.2, 0.1], color: [0.35, 0.22, 0.14], alpha: a });   // porta
        r.add({ pos: add3(hp, [1.8, 1.8, 3.02]), scale: [1.2, 1, 0.1], color: [1.0, 0.85, 0.45], emissive: f.active ? 0.9 : 0.1, alpha: a });
        if (f.abertas) r.add({ pos: add3(hp, [-4, 0, 0]), scale: [1, 1.5 + Math.min(8, f.abertas), 1], color: LEVEL_RGB.amarelo, emissive: 0.85 });
        r.lines.push({ a: add3(hp, [0, 2, 0]), b: add3(pos, [0, 2, 0]), color: [0.35, 1.0, 0.75, f.active ? 0.35 : 0.12] });
        this.objs.set("freelancer:" + f.id, { pos: hp });
        this._label("freelancer:" + f.id, `<span class="sw" style="background:${f.active ? "#ffd27a" : "#888"}"></span>${esc(f.name)} <small>${f.active ? `${f.abertas} aberta(s) · ${f.feitas_30d} feita(s) 30d` : "aguardando autorização"}</small>`,
          add3(hp, [0, 9, 0]), fp, true);
      });
    }));
    (data.markets || []).forEach(mk => this._safe("mercado", () => {
      // Mercado internacional: torre holográfica na capital (presença REMOTA — não é escritório físico)
      if (mk.lat == null) return;
      const pos = this._place(ll(mk.lat, mk.lng)), pick = { kind: "market", id: mk.id, data: mk }, on = mk.status === "ativo";
      const col = on ? [0.37, 0.92, 0.83] : [0.55, 0.65, 1.0], pr = mk.progresso || { total: 0, feitas: 0 };
      r.add({ pos, scale: [26, 0.3, 26], color: col, emissive: 0.6, alpha: 0.18 });
      r.add({ pos, scale: [8, 26, 8], color: col, emissive: 0.45, alpha: 0.35, edge: 1, winColor: col, pick });
      r.add({ pos: add3(pos, [0, 26, 0]), scale: [3, 3, 3], color: [0.98, 0.80, 0.30], emissive: 1.0, pick });
      if (pr.total) r.add({ pos: add3(pos, [6, 0, 0]), scale: [1.4, 1 + 20 * pr.feitas / pr.total, 1.4], color: LEVEL_RGB.verde, emissive: 0.9 });
      r.lines.push({ a: add3(HQ, [0, 40, 0]), b: add3(pos, [0, 26, 0]), color: [...col, 0.35] });
      this.objs.set("market:" + mk.id, { pos, home: pos, h: 26 });
      this._label("market:" + mk.id, `<span class="sw" style="background:#9fb3ff"></span>🌐 ${esc(mk.country)} <small>${esc(mk.marca || "")} · ${on ? "site no ar" : "expansão em andamento"} · remoto</small>`,
        add3(pos, [0, 32, 0]), pick, false, null, null, [0, 14000 * F]);
    }));
    for (const p of data.partners) this._safe("cliente", () => {
      p.units = p.units || []; p.status = p.status || { level: "cinza" };
      const u0 = p.units.find(u => u.lat != null) || p.units[0] || {};
      const { pos: p0, approx } = this._coordOf(u0.lat, u0.lng, p.region_code), pos = this._place(p0);
      // Cliente: começa pequeno e cresce de nível conforme os serviços são concluídos (fidelidade/cashback)
      const fid = p.fidelidade || { nivel: 1, pontos: 0, servicos: 0 }, nv = fid.nivel || 1;
      const lvl = p.status.level, wd = 4.5 + nv * 1.1, h = 6 + nv * 5 + Math.min(p.units.length, 4) * 1.5, visible = this._passes(p);
      if (visible) this._shadow(pos, wd, wd, h);
      const base = r.add({ pos, scale: [wd + 4, 0.4, wd + 4], color: LEVEL_RGB[lvl], emissive: 0.35, alpha: 0.55, visible });
      const ring = r.add({ pos, scale: [wd + 9, 0.2, wd + 9], color: LEVEL_RGB[lvl], emissive: 0.5, alpha: 0.2, visible });  // visível de longe
      const b = r.add({ pos, scale: [wd, h, wd], color: [0.10, 0.15, 0.27], win: 1, edge: 1, visible, pick: { kind: "partner", id: p.id, data: p } });
      r.add({ pos: add3(pos, [0, h + 1.6, 0]), scale: [wd + 0.6, 0.25, wd + 0.6], color: [0.37, 0.92, 0.83], emissive: 0.7, alpha: 0.35, visible });
      if (nv >= 4) r.add({ pos: add3(pos, [0, h, 0]), scale: [wd * 0.6, 1.2, wd * 0.6], color: [0.98, 0.80, 0.30], emissive: 0.9, visible });  // coroa dourada
      const beacon = r.add({ pos: add3(pos, [0, h, 0]), scale: [4, 1.6, 4], color: LEVEL_RGB[lvl], emissive: 0.9, visible, pick: { kind: "status", id: p.id, data: p } });
      p.units.forEach((u, j) => {
        const own = u.lat != null && u !== u0 ? this._place(ll(u.lat, u.lng)) : add3(pos, [7.5, 0, -3 + j * 4]);
        r.add({ pos: own, scale: [3, 3.5, 3], color: u.location_status === "confirmada" ? [0.25, 0.45, 0.6] : [0.22, 0.25, 0.3], visible,
          pick: { kind: "unit", id: u.id, data: { ...u, partner: p.name } } });
        const o = u.ops || {};  // operações: coluna verde = feitas em 30 dias; topo âmbar/vermelho = abertas/atrasadas
        if (o.feitas_30d) r.add({ pos: add3(own, [0, 3.5, 0]), scale: [1.1, Math.min(14, 1 + o.feitas_30d * 0.8), 1.1], color: LEVEL_RGB.verde, emissive: 0.8, alpha: 0.85, visible });
        if (o.abertas) r.add({ pos: add3(own, [-2.2, 0, 0]), scale: [1.3, 2 + Math.min(8, o.abertas), 1.3], color: o.atrasadas ? LEVEL_RGB.vermelho : LEVEL_RGB.amarelo, emissive: 0.85, visible });
        this.objs.set("unit:" + u.id, { pos: own });
        if (u.lat != null && u !== u0) {
          r.lines.push({ a: add3(pos, [0, 2, 0]), b: add3(own, [0, 2, 0]), color: [0.60, 0.80, 1.0, visible ? 0.35 : 0.05] });
          this._label("unit:" + u.id, `${esc(u.name)} <small>${esc(u.city || "")}</small>`, add3(own, [0, 6, 0]), { kind: "unit", id: u.id, data: { ...u, partner: p.name } }, true, () => this._passes(p));
        }
      });
      this.objs.set("partner:" + p.id, b); this.objs.set("partnerparts:" + p.id, [base, beacon, ring]);
      b.home = pos; b.h = h; b.approx = approx;
      const ab = p.units.reduce((a, u) => a + ((u.ops || {}).abertas || 0), 0), fe = p.units.reduce((a, u) => a + ((u.ops || {}).feitas_30d || 0), 0);
      const opsTxt = (ab || fe ? ` · ${ab} aberta(s) · ${fe} feita(s) 30d` : "") + ` · nível ${nv}${fid.pontos ? ` · ${fid.pontos} pts` : ""}`;
      this._label("partner:" + p.id, `<span class="sw" style="background:rgb(${LEVEL_RGB[lvl].map(x => x * 255 | 0)})"></span>${esc(p.name)} <small>· ${statusText(lvl)}${opsTxt}${approx ? " · local a definir" : ""}</small>`,
        add3(pos, [0, h + 4, 0]), { kind: "partner", id: p.id }, false, () => this._passes(p), null, [0, 4500 * F]);
      const wh = data.warehouses.find(w => w.region_code === p.region_code), whm = wh && this.objs.get("warehouse:" + wh.id);
      r.lines.push({ a: whm ? add3(whm.pos, [0, 7, 0]) : add3(HQ, [0, 40, 0]), b: add3(pos, [0, 2, 0]), color: [0.65, 0.55, 0.98, visible ? 0.35 : 0.05] });
    });
    for (const sb of data.service_bases || []) {
      if (!sb._pos) continue;
      for (const pid of new Set((sb.units || []).map(u => u.partner_id))) {
        const b = this.objs.get("partner:" + pid); if (b) r.lines.push({ a: add3(sb._pos, [0, 12, 0]), b: add3(b.home, [0, 3, 0]), color: [0.35, 1.0, 0.75, 0.45] });
      }
    }
    if (this.states) this._safe("estados", () => this.setStates(this.states));
    this._safe("agentes", () => this._buildAgents(data.agents));
    if (this.inside) this.enterBuilding(this.inside, true);
    r.dirty = true;
  }
  _buildAgents(agents) {
    for (const a of agents) {
      if (a.code && a.code.startsWith("ceo_")) { this._buildCEO(a); continue; }
      if (a.code && a.code.startsWith("base_")) { this._buildBaseCEO(a); continue; }
      if (a.code && a.code.startsWith("mkt_")) { this._buildMarketCEO(a); continue; }
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
  // CEO de IA: fica no topo do prédio do próprio parceiro; cor = status real da última execução.
  _buildCEO(a) {
    const pid = a.code.slice(4), b = this.objs.get("partner:" + pid); if (!b || !b.visible) return;
    const col = AGENT_RGB[a.status] || AGENT_RGB.disponivel, top = add3(b.home, [0, b.h + 1.85, 0]);
    const body = this.r.add({ pos: top, scale: [2.4, 4.2, 2.4], color: [0.95, 0.78, 0.30], emissive: a.status === "executando" ? 0.8 : 0.3, pick: { kind: "agent", id: a.id, data: a } });
    this.r.add({ pos: add3(top, [0, 4.3, 0]), scale: [1.9, 1.9, 1.9], color: [0.95, 0.96, 1.0], pick: { kind: "agent", id: a.id, data: a } });
    for (const [dx, dz, sx, sz] of [[0, 1.9, 4.2, 0.3], [0, -1.9, 4.2, 0.3], [1.9, 0, 0.3, 4.2], [-1.9, 0, 0.3, 4.2]])  // halo = status
      this.r.add({ pos: add3(top, [dx, 6.6, dz]), scale: [sx, 0.3, sz], color: col, emissive: 1.0 });
    this.objs.set("agent:" + a.id, body); this.objs.set("ceo:" + pid, body);
    this._label("agent:" + a.id, `<span class="sw" style="background:rgb(${col.map(x => x * 255 | 0)})"></span>${esc(a.name)} <small>· ${AGENT_LABEL[a.status] || a.status}</small>`,
      add3(top, [0, 9.5, 0]), { kind: "agent", id: a.id }, true, null, "agent ceo");
  }
  _buildMarketCEO(a) {
    const mk = this.objs.get("market:" + a.code.slice(4)); if (!mk) return;
    const col = AGENT_RGB[a.status] || AGENT_RGB.disponivel, top = add3(mk.pos, [0, 29, 0]), pk = { kind: "agent", id: a.id, data: a };
    const body = this.r.add({ pos: top, scale: [2.4, 4.2, 2.4], color: [0.95, 0.78, 0.30], emissive: a.status === "executando" ? 0.9 : 0.35, pick: pk });
    this.r.add({ pos: add3(top, [0, 4.3, 0]), scale: [1.9, 1.9, 1.9], color: [0.95, 0.96, 1.0], pick: pk });
    this.r.add({ pos: add3(top, [0, 6.8, 0]), scale: [4.4, 0.3, 4.4], color: col, emissive: 1.0, alpha: 0.85 });
    this.objs.set("agent:" + a.id, body);
  }
  // CEO da base: no heliponto da própria base; halo = status da última execução
  _buildBaseCEO(a) {
    const bs = this.objs.get("base:" + a.code.slice(5)); if (!bs) return;
    const col = AGENT_RGB[a.status] || AGENT_RGB.disponivel, top = add3(bs.pos, [-3, 40.6, -2]), pk = { kind: "agent", id: a.id, data: a };
    const body = this.r.add({ pos: top, scale: [2.6, 4.4, 2.6], color: [0.35, 1.0, 0.75], emissive: a.status === "executando" ? 0.9 : 0.35, pick: pk });
    this.r.add({ pos: add3(top, [0, 4.5, 0]), scale: [2, 2, 2], color: [0.95, 0.96, 1.0], pick: pk });
    this.r.add({ pos: add3(top, [0, 7.2, 0]), scale: [4.6, 0.3, 4.6], color: col, emissive: 1.0, alpha: 0.85 });
    this.objs.set("agent:" + a.id, body);
    this._label("agent:" + a.id, `<span class="sw" style="background:rgb(${col.map(x => x * 255 | 0)})"></span>${esc(a.name)} <small>· ${AGENT_LABEL[a.status] || a.status}</small>`,
      add3(top, [0, 10, 0]), { kind: "agent", id: a.id }, true, null, "agent ceo");
  }
  _agentTarget(a) {
    const idx = (this.data.agents.indexOf(a) % 4) * 2.4 - 3.6;
    if (a.current_partner_id) { const b = this.objs.get("partner:" + a.current_partner_id); if (b) return add3(b.home, [-7, 0, 6 + idx * 0.5]); }
    const home = add3(this.HQ, AGENT_HOME[a.code] || [0, 0, 28]);
    return a.enabled ? add3(home, [idx, 0, 9]) : add3(this.HQ, [idx * 1.5, 0, 40]);
  }
  _label(key, html, p, pick, small, visibleFn, cls, range) {
    const el = document.createElement("button"); el.className = "lbl" + (cls ? " " + cls : ""); el.innerHTML = html; el.type = "button";
    el.setAttribute("aria-label", el.textContent);
    el.addEventListener("click", ev => { ev.stopPropagation(); this.h.onSelect(pick.kind, pick.id, pick); });
    this.labelsEl.appendChild(el);
    const L = { key, el, p, small, visibleFn, cls, range: range || (small ? [0, 700] : [0, 4500 * F]) }; this.labels.push(L); return L;
  }
  // Rótulos sem sobreposição: os mais importantes ficam; os demais aparecem ao aproximar.
  _placeLabels() {
    const d = this.r.cam.dist, cand = [];
    for (const L of this.labels) {
      const vis = (!L.visibleFn || L.visibleFn()) && !(this.r.lite && L.cls === "agent") && d >= L.range[0] && d <= L.range[1];
      const s = vis ? this.r.project(L.p) : null;
      if (!s) { L.el.style.display = "none"; continue; }
      cand.push([L, s]);
    }
    cand.sort((a, b) => labelPrio(b[0]) - labelPrio(a[0]) || a[1].depth - b[1].depth);
    const placed = [];
    for (const [L, s] of cand) {
      if (L.w === undefined) { L.el.style.display = ""; L.w = L.el.offsetWidth; L.h = L.el.offsetHeight; }  // mede uma vez
      const r = [s.x - L.w / 2 - 2, s.y - L.h - 2, s.x + L.w / 2 + 2, s.y + 2];
      if (labelPrio(L) < 11 && placed.some(q => r[0] < q[2] && r[2] > q[0] && r[1] < q[3] && r[3] > q[1])) { L.el.style.display = "none"; continue; }
      placed.push(r);
      L.el.style.display = ""; L.el.style.left = s.x + "px"; L.el.style.top = s.y + "px"; L.el.style.zIndex = String(1000 - Math.round(s.depth * 500));
    }
  }
  _passes(p) { return this.filters.levels.has(p.status.level) && (!this.filters.region || this.filters.region === p.region_code); }
  countryByRegion(code) { const n = ((this.data && this.data.region_country) || {})[code]; return this.countries.find(c => c.name === n); }
  setFilters(f) { Object.assign(this.filters, f); if (this.data) this.build(this.data); }
  fly(target, dist, pitch = 0.75, yaw) {
    const asp = this.r.canvas.clientWidth / Math.max(1, this.r.canvas.clientHeight);
    if (asp < 1) dist *= Math.min(2.2, 0.95 / asp); // tela em pé (celular): afasta a câmera para caber
    if (this._camTw) this._camTw.kill();   // um voo novo substitui o anterior (nunca dois brigando pela câmera)
    this._camTw = to(this.r.cam, { tx: target[0], ty: target[1], tz: target[2], dist, pitch, ...(yaw !== undefined ? { yaw } : {}), duration: 1.1, ease: "power3.inOut",
      onUpdate: () => (this.r.dirty = true), onComplete: () => { this._camTw = null; this._detailSoon(); } });
  }
  // botões de navegação na tela
  zoomStep(f) { this.r.onInteract(); const c = this.r.canvas; this.r.zoomAt(f, c.clientWidth / 2, c.clientHeight / 2); }
  resetNorth() { this.fly([this.r.cam.tx, 0, this.r.cam.tz], this.r.cam.dist, this.r.cam.pitch, 0); }
  toggleTilt() { const top = this.r.cam.pitch > 1.3; this.fly([this.r.cam.tx, 0, this.r.cam.tz], this.r.cam.dist, top ? 0.75 : 1.5); return !top; }
  flyBox(b, pitch = 1.05) {
    const w = (b[2] - b[0]) * K, h = (b[3] - b[1]) * K;
    this.fly(ll((b[1] + b[3]) / 2, (b[0] + b[2]) / 2), Math.min(12000 * F, Math.max(160, Math.max(w, h) * 1.15)), pitch, 0);
  }
  focus(kind, id, extra) {
    if (kind === "global") { const c = this.HQ || [0, 0, 0]; return this.fly([c[0] + 30 * K, 0, c[2] - 25 * K], 9500 * F, 1.2, 0); }
    if (kind === "region") { const c = this.countryByRegion(id); if (c) return this.flyBox(c.bbox); const cc = (this.data.country_center || {})[id]; if (cc) this.fly(ll(cc[0], cc[1]), 2500 * F, 1.05, 0); return; }
    if (kind === "country") { const c = this.countries.find(x => x.name === id); if (c) this.flyBox(c.bbox); return; }
    if (kind === "state") { if (extra && extra.bbox) this.flyBox(extra.bbox, 0.95); return; }
    if (kind === "partner") { const b = this.objs.get("partner:" + id); if (b) this.fly(add3(b.home, [0, b.h / 2, 0]), 70, 0.45); return; }
    if (kind === "hq") return this.fly(add3(this.HQ, [0, 15, 0]), 130, 0.6);
    if (kind === "sector") return this.fly(add3(this.HQ, SECTOR_OFF[id]), 55, 0.6);
    if (kind === "base") { const m = this.objs.get("base:" + id); if (m) this.fly(add3(m.pos, [0, 14, 0]), 150, 0.6); return; }
    if (kind === "warehouse") { const m = this.objs.get("warehouse:" + id); if (m) this.fly(m.pos, 60, 0.6); }
    if (kind === "agent") { const m = this.objs.get("agent:" + id); if (m) this.fly(m.pos, 40, 0.5); }
    if (kind === "freelancer") { const m = this.objs.get("freelancer:" + id); if (m) this.fly(m.pos, 45, 0.6); }
    if (kind === "market") { const m = this.objs.get("market:" + id); if (m) this.fly(add3(m.pos, [0, 12, 0]), 160, 0.7); }
  }
  // "Entrar" no prédio: o volume fica translúcido e os andares se separam, cada um clicável.
  enterBuilding(pid, instant) {
    this.exitBuilding(true); this.inside = pid; const b = this.objs.get("partner:" + pid); if (!b) return;
    b.alpha = 0.12; this.floorMeshes = [];
    FLOORS.forEach(([code, name], i) => {
      const y = { v: instant || motion.reduced ? 2 + i * 7 : 0 };
      const fw = (b.scale ? b.scale[0] : 9) + 3, m = this.r.add({ pos: [b.home[0], y.v, b.home[2]], scale: [fw, 1.2, fw], color: [0.16, 0.26, 0.40], emissive: 0.15, pick: { kind: "floor", id: code, partner: pid } });
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
  // ---------------------------------------------------------------- detalhe da área em foco
  // Satélite (NASA, até ~500 m/pixel) ou ruas (OpenStreetMap) só da região que a câmera está olhando.
  setBasemap(mode) { this.basemap = mode; this._paint(); this.detKey = null; this._detailSoon(); }
  _detailSoon() { clearTimeout(this._detT); this._detT = setTimeout(() => this._detail().catch(e => {  // sem internet/imagem: só não mostra o detalhe
    if (e instanceof Error && !/fetch|network|load/i.test(e.message)) this.h.onError && this.h.onError(e, "3d:detalhe"); }), 700); }
  async _detail() {
    const cam = this.r.cam, spanDeg = cam.dist * 2.2 / K;
    if (!this.countries.length || spanDeg > 40) { if (this.r.detBox) this.r.clearDetail(); this.detKey = null; return; }
    const mode = this.basemap === "ruas" ? "ruas" : this.sat ? "satelite" : null; if (!mode) return;
    const levels = mode === "ruas" ? [32, 16, 8, 4, 2, 1] : [32, 16, 8, 4];
    let L = levels[0]; for (const l of levels) if (l >= spanDeg * 1.4) L = l;
    const lon = cam.tx / K, lat = -cam.tz / K, step = L / 2;
    const w = Math.max(-180, Math.round(lon / step) * step - L / 2), s = Math.max(-85, Math.round(lat / step) * step - L / 2);
    const box = [w, s, Math.min(180, w + L), Math.min(85, s + L)], key = mode + ":" + box.join(",");
    if (key === this.detKey) return; this.detKey = key;
    const cv = mode === "ruas" ? await this._streets(box) : await this._satBox(box);
    if (cv && this.detKey === key) this.r.setDetail(cv.canvas, cv.box);
  }
  async _satBox(b) {
    const r = await fetch(`/api/geo/imagem/regiao?bbox=${b.join(",")}`, { credentials: "same-origin" }); if (!r.ok || r.status === 204) return null;
    const box = (r.headers.get("X-Bbox") || b.join(",")).split(",").map(Number), url = URL.createObjectURL(await r.blob());
    try { const img = await loadImg(url); const S = this.r.lite ? 1024 : 2048, cv = document.createElement("canvas"); cv.width = cv.height = S;
      const g = cv.getContext("2d"); g.drawImage(img, 0, 0, S, S); g.fillStyle = "rgba(2,8,22,0.22)"; g.fillRect(0, 0, S, S); return { canvas: cv, box }; }
    finally { URL.revokeObjectURL(url); }
  }
  async _streets(b) {  // tiles OSM (Mercator) desenhados por faixa de latitude → encaixe exato no mapa equiretangular
    const [w, s, e, n] = b, t2lon = (x, z) => x / 2 ** z * 360 - 180, t2lat = (y, z) => { const k = Math.PI - 2 * Math.PI * y / 2 ** z; return 180 / Math.PI * Math.atan(Math.sinh(k)); };
    const lon2x = (lon, z) => Math.floor((lon + 180) / 360 * 2 ** z), lat2y = (lat, z) => { const r = lat * Math.PI / 180; return Math.floor((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * 2 ** z); };
    let z = Math.min(13, Math.max(3, Math.round(Math.log2(360 / (e - w))) + 2));
    while (z > 3 && (lon2x(e, z) - lon2x(w, z) + 1) * (lat2y(s, z) - lat2y(n, z) + 1) > 36) z--;
    const S = this.r.lite ? 1024 : 2048, cv = document.createElement("canvas"); cv.width = cv.height = S; const g = cv.getContext("2d");
    g.fillStyle = "#1b2433"; g.fillRect(0, 0, S, S);
    const jobs = [];
    for (let x = lon2x(w, z); x <= lon2x(e, z); x++) for (let y = lat2y(n, z); y <= lat2y(s, z); y++) jobs.push(loadImg(`/api/geo/tile/${z}/${x}/${y}`).then(img => {
      const x0 = (t2lon(x, z) - w) / (e - w) * S, x1 = (t2lon(x + 1, z) - w) / (e - w) * S, y0 = (n - t2lat(y, z)) / (n - s) * S, y1 = (n - t2lat(y + 1, z)) / (n - s) * S;
      g.drawImage(img, x0, y0, x1 - x0, y1 - y0); }).catch(() => {}));
    await Promise.all(jobs);
    g.fillStyle = "rgba(4,10,26,0.35)"; g.fillRect(0, 0, S, S);  // tom escuro: combina com a central e destaca os prédios
    return { canvas: cv, box: b };
  }
  pulseFor(ev) {
    if (ev.type === "ceo.contratou" && ev.payload) {  // linha do CEO até o agente contratado (evento real)
      const ceo = this.objs.get("ceo:" + ev.partner_id), ag = this.objs.get("agent:" + ev.entity_id);
      if (ceo && ag) this.r.pulse(add3(ceo.pos, [0, 4, 0]), add3(ag.pos, [0, 3, 0]), [0.95, 0.78, 0.30], 1.8);
      return;
    }
    if (ev.type.startsWith("ceo.") && ev.partner_id) {
      const ceo = this.objs.get("ceo:" + ev.partner_id); if (ceo) this.r.pulse(add3(this.HQ, [0, 56, 0]), add3(ceo.pos, [0, 5, 0]), [0.65, 0.55, 1.0], 1.6);
      return;
    }
    if (ev.type.startsWith("tarefa.") || ev.type.startsWith("pendencia.")) {  // operação na unidade: sai da base que atende
      const uid = (ev.payload || {}).unit_id, um = uid && this.objs.get("unit:" + uid);
      const sb = uid && (this.data.service_bases || []).find(x => (x.units || []).some(u => u.id === uid) && x._pos);
      if (um) { const done = ev.status === "concluida";
        return this.r.pulse(sb ? add3(sb._pos, [0, 12, 0]) : add3(um.pos, [0, 30, 0]), add3(um.pos, [0, 4, 0]), done ? LEVEL_RGB.verde : [0.38, 0.65, 1.0], done ? 1.6 : 1.2); }
    }
    if (!ev.partner_id) return; const b = this.objs.get("partner:" + ev.partner_id); if (!b) return;
    const color = ev.type.startsWith("pagamento") ? [0.20, 0.83, 0.60] : ev.type.startsWith("estoque") ? [0.98, 0.75, 0.14]
      : ev.type.startsWith("comissao") ? [0.65, 0.55, 0.98] : ev.type.startsWith("envio") ? [0.38, 0.65, 1.0] : [0.37, 0.92, 0.83];
    const p = this.data.partners.find(x => x.id === ev.partner_id) || {}, wh = this.data.warehouses.find(w => w.region_code === p.region_code);
    const whm = wh && this.objs.get("warehouse:" + wh.id);
    const color2 = ev.type.startsWith("whatsapp") ? [0.15, 0.85, 0.40] : color;
    const from = (ev.type.startsWith("estoque") || ev.type.startsWith("envio")) && whm ? add3(whm.pos, [0, 7, 0]) : add3(this.HQ, [0, 40, 0]);
    if (ev.type.startsWith("whatsapp")) return this.r.pulse(add3(b.home, [0, b.h + 30, 0]), add3(b.home, [0, b.h, 0]), color2, 1.2);
    this.r.pulse(from, add3(b.home, [0, b.h, 0]), color);
  }
}

export function statusText(l) {
  return { verde: "sem pendências críticas", amarelo: "atenção", vermelho: "problema crítico", cinza: "sem dados atualizados" }[l] || l;
}
