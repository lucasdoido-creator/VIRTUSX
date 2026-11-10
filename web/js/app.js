import { api, Live, fmtMoney, fmtTime, esc } from "./api.js";
import { motion, slideIn, slideOut, loadGsapIfAvailable } from "./anim.js";
import { Renderer } from "./gl.js";
import { World, FLOORS, LEVEL_RGB, AGENT_LABEL, statusText } from "./world.js";

const $ = (s, el = document) => el.querySelector(s);
const S = { me: null, world: null, view: "3d", tab: "visao", panel: null, live: null, ticker: [], renderer: null, w: null,
  lite: localStorage.getItem("modoLeve") === "1" || (localStorage.getItem("modoLeve") === null && innerWidth < 760) };
const can = p => S.me && (S.me.permissions.includes("*") || S.me.permissions.includes(p));
const ENV_TEXT = { demo: "DEMONSTRAÇÃO · dados sintéticos · nenhum pagamento, envio ou repasse é real",
  sandbox: "TESTES · integrações em sandbox · nenhum valor real", production: "PRODUÇÃO · dados e ações reais" };
const TASK_STATES = { a_fazer: "a fazer", em_andamento: "em andamento", aguardando_terceiro: "aguardando terceiro",
  aguardando_aprovacao: "aguardando aprovação", concluida: "concluída", cancelada: "cancelada" };
const ORDER_STATES = { aguardando_pagamento: "aguardando pagamento", pago: "pago", enviado: "enviado", pagamento_recusado: "pagamento recusado",
  cancelado: "cancelado", expirado: "expirado", devolvido: "devolvido" };

function toast(msg, err) { const t = document.createElement("div"); t.className = "toast" + (err ? " err" : ""); t.textContent = msg; document.body.appendChild(t); setTimeout(() => t.remove(), 4200); }
async function act(fn, okMsg) { try { const r = await fn(); if (okMsg) toast(okMsg); await refresh(); return r; } catch (e) { toast(e.message, true); throw e; } }
const locBadge = x => { const g = x.geo_source || "";   // precisão real da posição no mapa
  if (g === "informada manualmente") return badge("coordenadas informadas", "ok");
  if (g.startsWith("endereço")) return badge("endereço exato", "ok");
  if (g.startsWith("cidade")) return badge(x.address ? "centro da cidade · buscando endereço" : "centro da cidade (aprox.)", "warn");
  return badge("aguardando localização", "warn"); };
const st = l => `<span class="st ${l}"><i></i>${statusText(l)}</span>`;
const sim = x => x ? `<span class="badge sim">simulado</span>` : "";
const badge = (t, c = "") => `<span class="badge ${c}">${esc(t)}</span>`;

// ------------------------------------------------------------------ inicialização
async function boot() {
  try { S.me = await api("/api/me"); } catch { return showLogin(); }
  setEnv(S.me.environment); await start();
}
function setEnv(env) { const b = $("#envbar"); b.className = "envbar " + env; b.textContent = ENV_TEXT[env] || env; document.title = `[${env.toUpperCase()}] VIRTUSX`; }
async function showLogin() {
  let env = "demo";
  try { const h = await (await fetch("/api/health")).json(); env = h.environment; setEnv(env); } catch {}
  $("#login").classList.remove("hidden"); $("#app").classList.add("hidden");
  if (env !== "demo") $("#demoAccounts").classList.add("hidden");
  try {
    const st = (await (await fetch("/api/setup")).json()).data;
    if (st && st.needed) {
      $("#login form.f").classList.add("hidden"); const sf = $("#setupForm"); sf.classList.remove("hidden");
      sf.onsubmit = async e => { e.preventDefault(); const f = new FormData(sf);
        try { await api("/api/setup", { method: "POST", body: { name: f.get("name"), email: f.get("email"), password: f.get("password") } });
          await api("/api/auth/login", { method: "POST", body: { email: f.get("email"), password: f.get("password") } }); location.reload(); }
        catch (er) { toast(er.message, true); } };
    }
  } catch {}
  $("#login form.f").onsubmit = async e => {
    e.preventDefault(); const f = new FormData(e.target);
    try { await api("/api/auth/login", { method: "POST", body: { email: f.get("email"), password: f.get("password") } }); location.reload(); }
    catch (er) { $("#loginErr").textContent = er.message; }
  };
  $("#login").querySelectorAll("[data-demo]").forEach(b => b.onclick = () => { $("#login [name=email]").value = b.dataset.demo; $("#login [name=password]").value = "demo1234"; });
}
async function start() {
  try { S.build = (await (await fetch("/api/health")).json()).build; window.__build = S.build; } catch {}
  await loadGsapIfAvailable();
  $("#app").classList.remove("hidden");
  $("#who").textContent = `${S.me.name} · ${S.me.partner ? S.me.partner.name : "Central"}`;
  $("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); location.reload(); };
  $("#reduce").checked = motion.reduced; $("#reduce").onchange = e => { motion.reduced = e.target.checked; localStorage.setItem("reduzirAnimacoes", e.target.checked ? "1" : "0"); if (S.renderer) S.renderer.reduced = motion.reduced; };
  $("#lite").checked = S.lite; $("#lite").onchange = e => { localStorage.setItem("modoLeve", e.target.checked ? "1" : "0"); location.reload(); };
  document.querySelectorAll("#viewSeg button").forEach(b => b.onclick = () => setView(b.dataset.view));
  $("#closePanel").onclick = closePanel;
  document.addEventListener("keydown", e => { if (e.key === "Escape") closePanel(); });
  setupSearch(); setupFilters();
  if (can("agents.pause")) $("#pauseWrap").classList.remove("hidden");
  $("#pause").onchange = e => act(() => api("/api/agents/pause", { method: "POST", body: { paused: e.target.checked } }), e.target.checked ? "Agentes pausados" : "Agentes retomados");
  S.templates = await api("/api/task-templates").catch(() => ({}));
  if (S.me.role === "tecnico") return startTecnico();
  S.world = await api("/api/world");
  try {
    S.renderer = new Renderer($("#gl"), { lite: S.lite });
    window.__cam = () => ({ ...S.renderer.cam });   // leitura da câmera (testes automáticos)
    S.w = new World(S.renderer, $("#labels"), { onSelect: select, onError: (er, where) => window.__vigia && window.__vigia(er.message, "", 0, er.stack, where) });
    S.w.build(S.world);
    if (S.world.view === "parceiro" && S.world.partners[0]) S.w.focus("partner", S.world.partners[0].id); else S.w.focus("global");
    const bmSeg = $("#basemapSeg"), bmMark = () => bmSeg.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.bm === S.w.basemap)));
    bmSeg.querySelectorAll("button").forEach(b => b.onclick = () => { try { localStorage.setItem("mapaBase", b.dataset.bm); } catch {} S.w.setBasemap(b.dataset.bm); bmMark(); });
    bmMark();
    const nav = { in: () => S.w.zoomStep(0.6), out: () => S.w.zoomStep(1.6), north: () => S.w.resetNorth(), home: () => goWorld(),
      tilt: () => { const top = S.w.toggleTilt(); $("#navctl [data-nav=tilt]").textContent = top ? "3D" : "2D"; },
      help: () => $("#navhelp").classList.toggle("hidden") };
    $("#navctl").querySelectorAll("[data-nav]").forEach(b => b.onclick = () => nav[b.dataset.nav]());
    const lg = $("#legend"), lt = $("#lgTog"); let lgOpen = false; try { lgOpen = localStorage.getItem("legendaAberta") === "1"; } catch {}
    const lgSet = o => { lg.classList.toggle("compact", !o); lt.textContent = o ? "ocultar" : "ⓘ mais"; lt.setAttribute("aria-expanded", String(o)); try { localStorage.setItem("legendaAberta", o ? "1" : "0"); } catch {} };
    lgSet(lgOpen); lt.onclick = () => { lgOpen = !lgOpen; lgSet(lgOpen); };
    S.w.h.onImagery = (what, okImg) => { if (!okImg && what === "mundo" && S.w.basemap !== "vetor") toast("Imagem de satélite ainda não baixada (o servidor precisa de internet uma vez). Mostrando o mapa escuro.", true); };
    api("/api/geo/countries").then(g => { S.geo = g; if (g.available) S.w.setGeo(g); else toast("Mapa real ainda não baixado: o servidor precisa de internet uma vez.", true); }).catch(() => {});
  } catch (e) {
    window.__vigia && window.__vigia(e.message, "", 0, e.stack, "3d:inicio");
    $("#stage").innerHTML = `<div class="card" style="margin:60px auto;max-width:520px">O modo 3D não está disponível neste dispositivo (${esc(e.message)}). Todas as informações estão no modo tabelas.</div>`;
    setView("tabelas");
  }
  if (S.lite || innerWidth < 760) $("#legend").classList.add("hidden");
  renderCrumbs([["Mundo", goWorld]]);
  $("#cmdBtn").onclick = openCommands; $("#inboxBtn").onclick = () => { S.tab = "atendimento"; setView("tabelas"); };
  if (!can("comandos.run")) $("#cmdBtn").classList.add("hidden");
  if (!can("atendimento.read")) $("#inboxBtn").classList.add("hidden"); else updateUnread();
  document.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCommands(); } });
  updateHeader();
  if (can("sistema.saude")) { $("#saudeBtn").classList.remove("hidden"); $("#saudeBtn").onclick = openSaude; updateSaude(); setInterval(updateSaude, 60000); }
  S.live = new Live({ onEvent, onState: liveState, onPing: d => { if (d.build && S.build && d.build !== S.build) newVersion(); },
    onResync: () => { toast("Conexão restabelecida — dados sincronizados"); refresh(true); } });
  S.live.start(S.world.last_event_id);
}

// ------------------------------------------------------------------ versão nova e saúde do sistema
let reloadTimer = null;
function newVersion() {  // o servidor foi atualizado: a tela se atualiza sozinha (sem perder o que está sendo digitado)
  const bar = $("#updbar"); bar.classList.remove("hidden");
  const busy = () => { const a = document.activeElement; return a && /INPUT|TEXTAREA|SELECT/.test(a.tagName) && a.value; };
  bar.innerHTML = `Nova versão do sistema instalada. <button class="btn small" id="updNow">Atualizar agora</button>`;
  $("#updNow").onclick = () => location.reload();
  if (!reloadTimer) reloadTimer = setInterval(() => { if (!busy()) location.reload(); }, 8000);
}
async function updateSaude() {
  try { const h = await (await fetch("/api/health")).json(); const l = (h.saude && h.saude.level) || "cinza";
    const b = $("#saudeBtn"); b.className = "btn small saude " + l; b.title = "Saúde do sistema: " + statusText(l);
    if (h.build && S.build && h.build !== S.build) newVersion(); } catch {}
}
async function openSaude(agora) {
  const d = await api("/api/saude" + (agora === true ? "?agora=1" : ""));
  const lvl = l => `<span class="st ${l}"><i></i></span>`;
  openPanel({ noAuto: true, title: "Saúde do sistema", sub: `${st(d.level)} · verificado ${fmtTime(d.checked_at)} · versão ${esc(d.build)}`, reload: () => openSaude(),
    html: `<p class="muted">Verificações automáticas a cada 5 minutos. Item vermelho gera alerta e uma pendência crítica para a central.</p>
      <table class="t"><tbody>${d.checks.map(c => `<tr><td>${lvl(c.level)}</td><td><b>${esc(c.title)}</b><div class="muted">${esc(c.detail)}</div>${c.auto ? `<div class="muted">Automático: ${esc(c.auto)}</div>` : ""}</td></tr>`).join("")}</tbody></table>
      ${S.me.role === "admin_central" ? `<form class="f card" id="keyForm" autocomplete="off"><h4>Chave da Claude (reserva paga dos CEOs)</h4>
        <p class="muted">Cole a chave inteira (começa com <span class="mono">sk-ant-</span>; use o botão de copiar do console). Ela é testada sem custo,
        só é salva se a Anthropic aceitar, fica apenas neste computador (config.local.env) e nunca mais aparece inteira na tela.</p>
        <div class="row"><input name="key" type="password" autocomplete="new-password" spellcheck="false" placeholder="sk-ant-api03-…" style="flex:1;min-width:260px" aria-label="Chave da API da Claude">
        <button class="btn small primary">Salvar e testar</button><button type="button" class="btn small" id="keyDel">Remover chave</button></div></form>` : ""}
      <div class="row" style="margin:10px 0"><button class="btn small primary" id="sdNow">Verificar agora</button>
      ${d.client_errors.some(e => !e.resolved) ? `<button class="btn small" id="sdRes">Marcar erros de tela como resolvidos</button>` : ""}</div>
      <h4>Sentinela · monitor de segurança</h4><div id="sentBox" class="card"><p class="muted">Carregando…</p></div>
      <h4>Erros registrados na tela (navegador)</h4>
      ${d.client_errors.length ? d.client_errors.map(e => `<div class="card"><b>${esc(e.message)}</b> ${e.resolved ? badge("resolvido", "ok") : badge(e.count + "×", "warn")}
        <div class="muted mono">${esc(e.source || "")}${e.line ? ":" + e.line : ""} · ${esc(e.onde || "")} · tela ${esc(e.build || "?")} / servidor ${esc(e.server_build || "?")} · último ${fmtTime(e.last_at)}</div></div>`).join("") : `<p class="muted">Nenhum.</p>`}
      <h4>Cópias de segurança (data/backups)</h4>${d.backups.length ? d.backups.map(b => `<div class="muted mono">${esc(b.file)} · ${(b.bytes / 1e6).toFixed(1)} MB</div>`).join("") : `<p class="muted">Nenhuma ainda.</p>`}` });
  $("#sdNow").onclick = () => openSaude(true).then(updateSaude);
  api("/api/monitor").then(mo => { const el = $("#sentBox"); if (!el) return;
    const sev = { critica: "bad", alta: "bad", media: "warn", baixa: "", info: "" };
    el.innerHTML = `<div class="muted">${mo.ultimo ? esc(mo.ultimo.summary) + " · " + fmtTime(mo.ultimo.finished_at) : "Ainda não rodou (roda 1x por dia)."}</div>
      ${mo.achados.map(a => `<div style="margin:6px 0">${badge(a.severity, sev[a.severity])} <b>${esc(a.title)}</b><div class="muted">${esc(a.detail || "")}</div>
        ${a.proposal ? `<div><i>Correção proposta:</i> ${esc(a.proposal)}</div>` : ""}</div>`).join("") || `<p>Nenhum problema aberto.</p>`}
      <div class="muted">Chave própria: ${mo.chave.configurada ? esc(mo.chave.mascara) : "não configurada (usa a IA local)"} · gasto no mês US$ ${mo.gasto_mes_usd.toFixed(2)} de ${mo.teto_mes_usd.toFixed(2)}
        · GitHub para correções: ${mo.github ? "configurado" : "não configurado (correções ficam descritas aqui)"}</div>
      <div class="row" style="margin-top:6px">${can("agents.run") ? `<button class="btn small" id="sentRun">Rodar Sentinela agora</button>` : ""}</div>
      ${S.me.role === "admin_central" ? `<form class="f" id="monKeyForm" autocomplete="off"><div class="row"><input name="key" type="password" autocomplete="new-password" spellcheck="false"
        placeholder="chave separada do Sentinela (sk-ant-…)" style="flex:1;min-width:240px" aria-label="Chave da Claude do monitor"><button class="btn small primary">Salvar e testar</button></div></form>` : ""}`;
    const sr = $("#sentRun"); if (sr) sr.onclick = () => act(() => api("/api/agents/monitor/run", { method: "POST" }), "Sentinela executou").then(() => openSaude(true));
    const mk = $("#monKeyForm"); if (mk) mk.onsubmit = async e => { e.preventDefault(); const v = mk.key.value; mk.key.value = "";
      try { const r = await api("/api/sistema/chave-monitor", { method: "POST", body: { key: v } }); toast(`Chave do Sentinela salva (${r.chave}). Teto: US$ 5/mês.`); openSaude(true); }
      catch (er) { toast(er.message, true); } };
  }).catch(() => {});
  const kf = $("#keyForm"); if (kf) {
    kf.onsubmit = async e => { e.preventDefault(); const v = kf.key.value; kf.key.value = "";   // some do campo na hora
      try { const r = await api("/api/sistema/chave-claude", { method: "POST", body: { key: v } }); toast(`Chave aceita e salva (${r.chave}). Os CEOs já podem usar a Claude.`); openSaude(true); }
      catch (er) { toast(er.message, true); } };
    $("#keyDel").onclick = async () => { if (!confirm("Remover a chave da Claude? Os CEOs passam a usar só a IA grátis local.")) return;
      try { await api("/api/sistema/chave-claude", { method: "POST", body: { remove: true } }); toast("Chave removida"); openSaude(true); } catch (er) { toast(er.message, true); } };
  }
  const r = $("#sdRes"); if (r) r.onclick = async () => { await api("/api/saude/erros/resolver", { method: "POST" }); openSaude(true); updateSaude(); };
}

// Técnico/freelancer: sem mundo 3D nem dados de clientes; só as próprias tarefas, com checklist e evidências.
function startTecnico() {
  S.world = { partners: [], agents: [], regions: [], warehouses: [], sectors: [], service_bases: [] };
  $("#viewSeg").classList.add("hidden"); $("#search").classList.add("hidden"); $("#live").classList.add("hidden");
  $("#cmdBtn").classList.add("hidden"); $("#inboxBtn").classList.add("hidden");
  S.tab = "tarefas"; setView("tabelas");
  setInterval(() => { if (!S.panel) renderTables(true); }, 30000);
}

function setView(v) {
  S.view = v; document.querySelectorAll("#viewSeg button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.view === v)));
  $("#stageWrap").classList.toggle("hidden", v !== "3d"); $("#tables").classList.toggle("hidden", v !== "tabelas");
  if (v === "tabelas") renderTables(); else if (S.renderer) S.renderer.dirty = true;
}
function updateHeader() { $("#pause").checked = !!S.world.agents_paused; $("#pausedNote").classList.toggle("hidden", !S.world.agents_paused); }

// ------------------------------------------------------------------ tempo real
let refreshTimer = null;
function onEvent(ev) {
  S.ticker.unshift(ev); S.ticker = S.ticker.slice(0, 8);
  (window.__eventos = window.__eventos || []).push(ev.type); if (window.__eventos.length > 300) window.__eventos.shift();  // tipos recebidos (sem dados) — usado nos testes
  $("#ticker").innerHTML = `<span>Últimos eventos:</span>` + S.ticker.slice(0, 5).map(e =>
    `<span title="ocorrido ${fmtTime(e.occurred_at)} · recebido ${fmtTime(e.received_at)}"><b>${esc(e.type)}</b> ${esc(e.status)} · ${new Date(e.received_at).toLocaleTimeString("pt-BR")}</span>`).join("");
  if (S.w) S.w.pulseFor(ev);
  if (ev.type.startsWith("sistema.") && can("sistema.saude")) { updateSaude(); if (ev.type === "sistema.alerta") toast("Alerta do sistema: " + ((ev.payload || {}).verificacao || ""), true); }
  if (ev.type.startsWith("whatsapp") || ev.type.startsWith("canal")) { updateUnread(); if (S.view === "tabelas" && S.tab === "atendimento") renderTables(true); }
  if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = null; refresh(true); }, 800); // agrupa rajadas de eventos
}
function liveState(s, lostAt) {
  const p = $("#live"); p.className = "pill " + ({ ao_vivo: "live", reconectando: "late", atrasado: "late", desconectado: "off" }[s] || "");
  const since = lostAt ? ` · dados de ${lostAt.toLocaleTimeString("pt-BR")}` : "";
  p.querySelector("span").textContent = { conectando: "Conectando…", ao_vivo: "Ao vivo", reconectando: "Reconectando" + since,
    atrasado: "Dados atrasados" + since, desconectado: "Sem conexão" + since }[s];
  document.body.classList.toggle("stale", s !== "ao_vivo");
}
async function refresh(auto = false) {  // auto = disparado por evento: não apaga o que está sendo digitado
  const busy = sel => auto && (editing(sel) || (sel === "#pBody" && S.panel && S.panel.noAuto));
  try {
    if (S.me.role === "tecnico") {  // técnico não tem visão do mundo: só recarrega a tarefa e a lista
      if (S.panel && S.panel.reload && !busy("#pBody")) await reloadPanel();
      return busy("#tBody") ? null : renderTables(true, auto);
    }
    S.world = await api("/api/world"); updateHeader();
    if (S.w) S.w.build(S.world);
    if (S.panel && S.panel.reload && !busy("#pBody")) await reloadPanel();
    if (S.view === "tabelas" && !busy("#tBody")) renderTables(true, auto);
  } catch (e) { if (e.status === 401) location.reload(); }
}
// Recarrega o painel aberto e ESPERA terminar: o próximo passo (ex.: abrir a tarefa recém-criada)
// nunca é coberto por uma recarga antiga que chegou atrasada.
async function reloadPanel() {
  const target = S.panel; S.reloadTarget = target;   // recarga atrasada nunca reabre painel fechado nem cobre outro aberto depois
  try { await target.reload(); } catch (e) { if (e.status === 401) throw e; } finally { if (S.reloadTarget === target) S.reloadTarget = null; }
}
// Nunca redesenhar por cima do que a pessoa está digitando (cadastro, resposta de WhatsApp…):
// a área espera e é atualizada no próximo evento depois que o formulário for enviado ou limpo.
function editing(sel) {
  const box = $(sel); if (!box) return false;
  const a = document.activeElement;
  if (a && box.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName) && a.type !== "checkbox") return true;
  if (box.querySelector("details[open] form")) return true;   // formulário aberto pela pessoa (ex.: "Nova tarefa")
  if ([...box.querySelectorAll("form select")].some(sel => { const d = [...sel.options].find(o => o.defaultSelected) || sel.options[0]; return d && sel.value !== d.value; })) return true;
  return [...box.querySelectorAll("form input, form textarea")].some(i => !["checkbox", "hidden", "file", "radio"].includes(i.type) && i.value !== i.defaultValue);
}

// ------------------------------------------------------------------ navegação
function renderCrumbs(items) {
  $("#crumbs").innerHTML = ""; items.forEach(([t, fn], i) => { const b = document.createElement("button"); b.textContent = (i ? "› " : "") + t; b.onclick = fn; $("#crumbs").appendChild(b); });
}
function goWorld() { S.w && S.w.exitBuilding(); S.w && S.w.setStates(null); S.w && S.w.focus("global"); closePanel(); renderCrumbs([["Mundo", goWorld]]); }
const regionOfCountry = name => Object.entries(S.world.region_country || {}).find(([, n]) => n === name)?.[0];
const countryName = name => ((S.geo && S.geo.countries || []).find(c => c.name === name) || {}).name_pt || name;
function select(kind, id, pick) {
  const W = S.w; const p = (S.world.partners || []).find(x => x.id === id);
  if (kind === "region") { const n = (S.world.region_country || {})[id]; if (n) return select("country", n, {}); }
  if (kind === "country") { W && W.exitBuilding(); W && W.focus("country", id);
    renderCrumbs([["Mundo", goWorld], [countryName(id), () => select("country", id, {})]]); return openCountry(id); }
  if (kind === "state") { W && W.exitBuilding(); W && W.focus("state", id, pick.data && pick.data.bbox ? pick.data : null);
    renderCrumbs([["Mundo", goWorld], [countryName(pick.country), () => select("country", pick.country, {})], [id, () => select("state", id, pick)]]);
    return openState(pick.data, pick.country); }
  if (kind === "partner" || kind === "status") {
    if (W) { W.focus("partner", id); W.enterBuilding(id); }
    const cn = p && (S.world.region_country || {})[p.region_code];
    renderCrumbs([["Mundo", goWorld], [cn ? countryName(cn) : "País", () => cn && select("country", cn, {})], [p ? p.name : id, () => select("partner", id)]]);
    return openBuilding(id, kind === "status" ? "status" : "recepcao");
  }
  if (kind === "floor") { if (W) W.highlightFloor(id); return openBuilding(pick.partner || S.panel?.partner, id); }
  if (W) W.exitBuilding();
  if (W) W.focus(kind, id);
  if (kind === "hq") return openHQ();
  if (kind === "sector") return openSector(id);
  if (kind === "warehouse") return openWarehouse(id);
  if (kind === "agent") return openAgent(id);
  if (kind === "unit") return openUnit(pick.data);
  if (kind === "base") return openBase(pick.data || S.world.service_bases.find(b => b.id === id));
  if (kind === "freelancer") return openFreelancer(pick.data);
  if (kind === "market") return openMarket(id);
  if (kind === "order") return openOrder(id);
  if (kind === "task") return openTask(id);
}

// ------------------------------------------------------------------ painel lateral
function openPanel({ title, sub, html, tabs, tab, onTab, reload, partner, noAuto }) {
  const rt = S.reloadTarget;
  if (rt && S.panel !== rt && title === rt.title && (!S.panel || S.panel.title !== title)) return;   // resultado de recarga velha: descarta
  const el = $("#panel"); const wasOpen = !!S.panel;
  S.panel = { reload, partner, noAuto, title };   // noAuto: painel não recarrega a cada evento (ex.: Saúde, que tem 'Verificar agora')
  $("#pTitle").innerHTML = title; $("#pSub").innerHTML = sub || "";
  const tb = $("#pTabs"); tb.innerHTML = ""; tb.classList.toggle("hidden", !tabs);
  (tabs || []).forEach(([code, name]) => { const b = document.createElement("button"); b.textContent = name; b.setAttribute("role", "tab");
    b.setAttribute("aria-selected", String(code === tab)); b.onclick = () => onTab(code); tb.appendChild(b); });
  $("#pBody").innerHTML = html; el.classList.remove("hidden"); el.setAttribute("aria-hidden", "false");
  if (!wasOpen) slideIn(el); $("#pBody").scrollTop = 0;
}
function closePanel() { if (!S.panel) return; S.panel = null; const el = $("#panel"); slideOut(el, () => { el.classList.add("hidden"); el.setAttribute("aria-hidden", "true"); }); S.w && S.w.exitBuilding(); }

// ---------- prédio do parceiro
async function openBuilding(pid, floor = "recepcao") {
  const [b, ceoData, convs] = await Promise.all([api(`/api/partners/${pid}/building`), can("ventures.read") ? api(`/api/partners/${pid}/ceo`).catch(() => null) : null,
    can("atendimento.read") ? api(`/api/conversations?partner_id=${pid}`).catch(() => []) : []]);
  b.ceo = ceoData; b.convs = convs; const p = b.partner;
  const sub = `${st(b.status.level)} ${p.is_demo ? badge("DEMONSTRAÇÃO", "sim") : ""} ${badge(p.commercial_model === "central_commission" ? "Venda central + comissão" : "Revenda B2B (não operacional)")} · ${esc(p.region_name)}`;
  const tabs = [["status", "Sinalização"], ...FLOORS];
  const render = f => {
    if (S.w) S.w.highlightFloor(f);
    openPanel({ title: esc(p.name), sub, tabs, tab: f, onTab: render, partner: pid, reload: () => openBuilding(pid, f), html: FLOOR_RENDER[f](b) });
    bindFloor(f, b);
  };
  render(floor);
}
const VSTATUS = { recebida: "ideia recebida", pesquisando: "CEO pesquisando", em_execucao: "em execução", aguardando_proximo_ciclo: "etapa concluída · aguardando próximo ciclo" };
function brainLine(br) {
  if (!br) return "";
  const o = br.ollama.ok ? badge(`IA local ${br.ollama.model}: ativa`, "ok") : badge("IA local (Ollama): desligada", "warn");
  const c = br.claude.configured ? badge(`Claude ${br.claude.decision_model}: decisões`, "ok") : badge("Claude: sem chave", "");
  const sp = br.month_spend_usd !== undefined ? ` · Claude hoje US$ ${br.day_spend_usd.toFixed(4)}/${br.daily_cap_usd.toFixed(2)} · mês US$ ${br.month_spend_usd.toFixed(4)}/${br.monthly_cap_usd.toFixed(2)}` : "";
  return `<div class="row">${o} ${c}<span class="muted">${sp}</span></div>${br.ollama.ok ? "" : `<div class="muted">Sem IA local, o CEO usa plano-modelo e só fontes/medidas determinísticas. Para ativar grátis: instale o Ollama e rode <span class="mono">ollama pull ${esc(br.ollama.model)}</span>.</div>`}`;
}
const FLOOR_RENDER = {
  atendimento: b => { const cs = b.convs || [];
    return `<p class="muted">Conversas de WhatsApp deste cliente. A caixa completa fica em <b>Atendimento</b>.</p>
      ${cs.map(c => `<div class="card click" data-conv="${c.id}" style="cursor:pointer"><b>${esc(c.contact_name || c.wa_id)}</b> ${c.unread ? badge(c.unread + " nova(s)", "ok") : ""} ${badge(c.status)}
        <div class="muted">${c.last ? esc((c.last.direction === "in" ? "Cliente: " : "Equipe: ") + c.last.preview) : ""} · ${fmtTime(c.last_message_at)}</div></div>`).join("") || `<p class="muted">Nenhuma conversa ainda. Cadastre o telefone do cliente em Cadastros → Contatos.</p>`}`; },
  diretoria: b => { const d = b.ceo; if (!d) return `<p class="muted">Sem acesso à Diretoria.</p>`; const cur = b.partner.currency;
    const fmtPrev = f => f && f.ok ? `Previsão 30 dias: ${fmtMoney(f.previsao_minor, cur)} (faixa ${fmtMoney(f.faixa_minor[0], cur)}–${fmtMoney(f.faixa_minor[1], cur)}) · ${esc(f.metodo)}` : `Previsão: ${esc(f ? f.motivo : "—")}`;
    return `<div class="card"><h4>${d.ceo ? `<span class="avatar-dot" style="background:rgb(${AGENT_RGB_SAFE(d.ceo.status).join(",")})"></span>${esc(d.ceo.name)} · ${badge(AGENT_LABEL[d.ceo.status] || d.ceo.status)}` : "CEO"}</h4>
      <div class="muted">Desde ${fmtTime(d.ceo && d.ceo.status_since)} · próxima rodada ${fmtTime(d.ceo && d.ceo.next_run_at)} · contrata apenas agentes gratuitos da casa; gastos sempre passam por aprovação.</div>
      ${brainLine(d.brain)}
      ${can("ventures.write") ? `<div class="row" style="margin-top:6px"><button class="btn small" id="ceoRun">Rodar CEO agora</button></div>` : ""}</div>
      ${can("ventures.write") ? `<form class="f card" id="ideaForm"><label>Nova ideia para o CEO transformar em realidade<textarea name="idea" rows="3" required minlength="8" placeholder="Ex.: kit presente corporativo com café e caneca personalizada"></textarea></label>
        <label>Palavras-chave para pesquisa (opcional)<input name="keywords" placeholder="ex.: presente corporativo café"></label><button class="btn primary">Enviar ao CEO</button></form>` : ""}
      <h4>Ideias e projetos</h4>${d.ventures.map(v => { const a = v.analysis || {}; const pct = v.progress.total ? Math.round(100 * v.progress.done / v.progress.total) : 0;
        return `<div class="card"><h4>${esc(v.idea)} ${v.is_demo ? badge("demo", "sim") : ""}</h4>
        <div class="row">${badge(VSTATUS[v.status] || v.status, v.status === "em_execucao" ? "ok" : "")} ${v.plan_source ? badge(v.plan_source === "ia" ? "plano por IA" : "plano-modelo (sem IA)") : ""}
          <span class="muted">${v.progress.done}/${v.progress.total} tarefas · ${pct}%</span></div>
        <div style="height:6px;background:#1e293b;border-radius:4px;margin:6px 0"><div style="height:6px;width:${pct}%;background:var(--accent);border-radius:4px"></div></div>
        ${v.summary ? `<p>${esc(v.summary)}</p>` : ""}
        ${(a.oportunidades || []).length ? `<div><b>Oportunidades:</b> ${a.oportunidades.map(esc).join(" · ")}</div>` : ""}
        ${(a.riscos || []).length ? `<div><b>Riscos:</b> ${a.riscos.map(esc).join(" · ")}</div>` : ""}
        ${(a.perguntas_para_o_dono || []).length ? `<div><b>Perguntas para você:</b> ${a.perguntas_para_o_dono.map(esc).join(" · ")}</div>` : ""}
        ${a.previsao ? `<div class="muted">${fmtPrev(a.previsao)}</div>` : ""}
        ${v.staff.length ? `<div style="margin-top:6px"><b>Equipe contratada (grátis):</b> ${v.staff.map(s => `<button class="btn small" data-agent="${s.agent_id}" title="${esc(s.role)}">${esc(s.name)} · ${esc(AGENT_LABEL[s.status] || s.status)}</button>`).join(" ")}</div>` : ""}
        ${v.tasks.length ? `<ul class="timeline" style="margin-top:6px">${v.tasks.map(t => `<li><a href="#" data-task-open="${t.id}">${esc(t.title)}</a> ${badge(TASK_STATES[t.status], t.status === "concluida" ? "ok" : t.status === "aguardando_terceiro" ? "warn" : "")}</li>`).join("")}</ul>` : ""}</div>`; }).join("") || `<p class="muted">Nenhuma ideia ainda.</p>`}
      <h4>Briefings do CEO</h4>${d.briefings.map(x => `<div class="card"><h4>${esc(x.kind === "plano" ? "Plano" : "Briefing diário")} · ${esc(x.local_day)} ${badge(x.brain || "")}</h4>
        <p>${esc(x.summary || "")}</p>${(x.content.sinais || []).length ? `<ul>${x.content.sinais.map(s => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}
        ${x.content.previsao ? `<div class="muted">${fmtPrev(x.content.previsao)}</div>` : ""}${x.content.observacao ? `<div class="muted">${esc(x.content.observacao)}</div>` : ""}
        ${x.sources.length ? `<details><summary>Fontes (${x.sources.length})</summary><ul>${x.sources.map(s => `<li><a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a> <span class="muted">${esc(s.source)}</span></li>`).join("")}</ul></details>` : ""}</div>`).join("") || `<p class="muted">Sem briefings ainda.</p>`}`; },
  status: b => `<div class="card"><h4>${st(b.status.level)} ${esc(b.status.text)}</h4><div class="muted">Calculado em ${fmtTime(b.status.computed_at)} · último dado recebido: ${fmtTime(b.status.last_data_at)}</div></div>` +
    (b.status.reasons.length ? b.status.reasons.map(r => `<div class="card"><h4>${st(r.level)} ${esc(r.message)}</h4>
      <dl class="kv"><dt>Origem</dt><dd>${esc(r.source)}</dd><dt>Atualizado em</dt><dd>${fmtTime(r.updated_at)}</dd></dl>
      <div class="row" style="margin-top:6px"><button class="btn small" data-goto="${r.action.floor}" data-task="${r.action.task_id || ""}">${esc(r.action.label)}</button></div></div>`).join("")
      : `<p class="muted">Nenhum motivo registrado.</p>`),
  recepcao: b => { const p = b.partner, f = b.floors.recepcao;
    return `<dl class="kv"><dt>Modelo comercial</dt><dd>${p.commercial_model === "cliente_servico" ? `Cliente de serviços de TI · contrato ${p.contract_minor ? fmtMoney(p.contract_minor, p.currency) + "/mês" : "não cadastrado"}${p.segment ? " · " + esc(p.segment) : ""}` : p.commercial_model === "central_commission" ? `Venda pela central · comissão ${(p.commission_rate_bp / 100).toFixed(2)}%` : "Revenda B2B (preparada, não operacional)"}</dd>
      <dt>Vendedor no checkout</dt><dd>${esc(p.seller_entity)}</dd><dt>Responsável</dt><dd>${esc(p.contact_name || "—")}</dd>
      <dt>Fuso / moeda</dt><dd>${esc(p.timezone)} · ${esc(p.currency)}</dd><dt>Situação</dt><dd>${st(b.status.level)}</dd></dl>
      <h4>Unidades</h4>${f.units.map(u => `<div class="card"><b>${esc(u.name)}</b> — ${esc(u.city || "")}/${esc(u.state || "")} ${u.base_name ? badge("atendida por " + u.base_name) : ""} ${u.active === 0 ? badge("inativa", "warn") : ""} ${u.location_status === "confirmada"
        ? badge("localização confirmada", "ok") + `<div class="muted">${esc(u.location_source || "")}</div>` : badge("localização não confirmada", "warn") + `<div class="muted">Posição no 3D é ilustrativa.</div>`}</div>`).join("")}
      ${fidelidadeHtml(p.id)}
      <h4>Avisos</h4>${f.avisos.length ? `<ul>${f.avisos.map(a => `<li>${esc(a)}</li>`).join("")}</ul>` : `<p class="muted">Sem avisos.</p>`}
      <h4>Responsáveis</h4>${f.responsaveis.map(u => `<div>${esc(u.name)} <span class="muted">${esc(u.role)}</span></div>`).join("")}`; },
  comercial: b => { const f = b.floors.comercial, cur = b.partner.currency, w = can("storefronts.write");
    return `<h4>Páginas (vitrines)</h4>${f.storefronts.map(s => `<div class="card"><b>${esc(s.title)}</b> ${s.published ? badge("publicada", "ok") : badge("rascunho")}
        <div class="muted mono">/loja/${esc(s.slug)}</div><div class="row" style="margin-top:6px">
        ${s.published ? `<a class="btn small" href="/loja/${esc(s.slug)}" target="_blank" rel="noopener">Abrir loja</a>` : ""}
        ${w ? `<button class="btn small" data-pub="${s.id}" data-val="${s.published ? 0 : 1}">${s.published ? "Despublicar" : "Publicar"}</button>` : ""}</div></div>`).join("") || `<p class="muted">Nenhuma página criada.</p>`}
      ${b.partner.commercial_model === "cliente_servico" ? `<div class="card muted">Cliente de serviços: sem loja/vitrine. Use Operação (tarefas e checklists) e Financeiro (fechamento mensal).</div>` : ""}
      ${w && b.partner.commercial_model === "central_commission" ? `<details class="card" ${f.storefronts.length ? "" : "open"}><summary><b>Criar página a partir do template</b></summary>
        <form class="f" id="sfForm"><label>Endereço (slug)<input name="slug" required pattern="[a-z0-9][a-z0-9\\-]{1,48}[a-z0-9]" placeholder="minha-loja"></label>
        <label>Título<input name="title" required value="${esc(b.partner.name.replace(" (DEMO)", ""))}"></label>
        <label>Template<select name="template"><option value="vitrine-classica">Clássica (grade)</option><option value="vitrine-editorial">Editorial (lista)</option></select></label>
        <label>Chamada<input name="headline" value="Seleção oficial"></label><label>Cor de destaque<input name="accent" type="color" value="#0f766e"></label>
        <fieldset class="card"><legend>Produtos autorizados</legend>${f.authorized_products.map(a => `<label class="row"><input type="checkbox" name="p" value="${a.id}" ${a.available ? "checked" : ""}> ${esc(a.name)} · ${fmtMoney(a.price_minor, cur)} <span class="muted">(${a.available} disp.)</span></label>`).join("")}</fieldset>
        <label class="row"><input type="checkbox" name="publish" checked> Publicar agora</label><button class="btn primary">Criar página</button></form></details>` : ""}
      <h4>Pedidos</h4>${ordersTable(f.orders)}
      <h4>Metas</h4><p class="muted">${esc(f.metas.note)}</p>`; },
  operacao: b => { const f = b.floors.operacao;
    return `${can("tasks.write") ? `<details class="card"><summary><b>Nova tarefa</b></summary>${taskForm(b)}</details>` : ""}
      <h4>Tarefas</h4>${tasksTable(f.tasks)}
      <h4>Recorrências (executadas pelo servidor)</h4>${f.series.map(s => `<div class="card">${esc(s.title)} · ${esc(s.recurrence_rule)} · próxima: ${fmtTime(s.recurrence_next_at)}</div>`).join("") || `<p class="muted">Nenhuma.</p>`}`; },
  financeiro: b => { const f = b.floors.financeiro, cur = b.partner.currency;
    return `<div class="card muted">${esc(f.note)}</div>` + (f.model === "central_commission" ? `<dl class="kv">
      <dt>Vendas confirmadas</dt><dd>${fmtMoney(f.vendas_minor, cur)}</dd><dt>Taxas de pagamento</dt><dd>${fmtMoney(f.taxas_minor, cur)} <span class="muted">(custo da central)</span></dd>
      <dt>Comissões a receber</dt><dd>${fmtMoney(f.a_receber_minor, cur)}</dd><dt>Comissões pagas</dt><dd>${fmtMoney(f.pago_minor, cur)}</dd></dl>` : "") +
      `<h4>Fechamentos</h4><p class="muted">Conferência, aprovação e liquidação são estados separados. Aprovado não significa transferido.</p>
      ${can("closings.prepare") ? `<form class="f card" id="clForm"><div class="row"><select name="kind"><option value="vendas">Vendas</option><option value="comissoes">Comissões</option><option value="conciliacao_pagamentos">Conciliação de pagamentos</option><option value="servicos" ${b.partner.commercial_model === "cliente_servico" ? "selected" : ""}>Serviços (mensal do cliente)</option></select>
        <select name="period_type"><option value="diario">Diário</option><option value="semanal">Semanal</option><option value="mensal">Mensal</option></select>
        <input type="date" name="local_day" required value="${new Date().toLocaleDateString("sv-SE", { timeZone: b.partner.timezone })}"><button class="btn small primary">Preparar</button></div>
        <div class="muted">Período no fuso ${esc(b.partner.timezone)}</div></form>` : ""}
      ${f.closings.map(closingCard).join("") || `<p class="muted">Nenhum fechamento.</p>`}`; },
  pendencias: b => { const f = b.floors.pendencias;
    return `${f.items.map(t => `<div class="card"><h4>${esc(t.title)} ${t.overdue ? badge("atrasada", "bad") : ""} ${badge(t.priority)}</h4>
      <div class="muted">${esc(TASK_STATES[t.status])} · ${esc(t.pendency_type || t.kind)} · prazo ${fmtTime(t.due_at)}</div>
      <div class="row" style="margin-top:6px"><button class="btn small" data-task-open="${t.id}">Abrir e resolver</button></div></div>`).join("") || `<p class="muted">Sem pendências.</p>`}
      <h4>Falhas de integração</h4>${f.integration_failures.map(a => `<div class="card">${esc(a.name)} — ${esc(a.last_error || "")}</div>`).join("") || `<p class="muted">Nenhuma.</p>`}`; },
  aplicativos: b => b.floors.aplicativos.map(a => `<div class="card"><h4>${esc(a.name)} ${badge(a.kind_label, a.kind === "planejada" ? "" : a.kind === "link_externo" ? "warn" : "ok")}</h4>
      <dl class="kv"><dt>Status</dt><dd>${badge(a.status, a.status === "erro" ? "bad" : a.status === "desconectado" ? "warn" : "")}</dd>
      <dt>Última sincronização</dt><dd>${a.kind === "link_externo" || a.kind === "planejada" ? "não se aplica" : fmtTime(a.last_sync_at)}</dd>
      ${a.last_error ? `<dt>Erro</dt><dd>${esc(a.last_error)}</dd>` : ""}${a.note ? `<dt>Observação</dt><dd>${esc(a.note)}</dd>` : ""}</dl>
      ${a.url ? `<a class="btn small" href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">Abrir link externo</a>` : ""}</div>`).join(""),
  equipe: b => { const f = b.floors.equipe;
    return `<h4>Pessoas do parceiro</h4>${f.pessoas.map(u => `<div class="card">${esc(u.name)} <span class="muted">${esc(u.role)}</span></div>`).join("")}
      <h4>Agentes</h4><p class="muted">Status reais da última execução registrada.</p>${f.todos_agentes.map(agentCard).join("")}`; },
};
function bindFloor(f, b) {
  const body = $("#pBody");
  body.querySelectorAll("[data-goto]").forEach(x => x.onclick = () => x.dataset.task ? openTask(x.dataset.task) : openBuilding(b.partner.id, x.dataset.goto));
  body.querySelectorAll("[data-task-open]").forEach(x => x.onclick = () => openTask(x.dataset.taskOpen));
  body.querySelectorAll("[data-order]").forEach(x => x.onclick = () => openOrder(x.dataset.order));
  body.querySelectorAll("[data-agent]").forEach(x => x.onclick = () => openAgent(x.dataset.agent));
  body.querySelectorAll("[data-conv]").forEach(x => x.onclick = () => { closePanel(); S.convSel = x.dataset.conv; S.tab = "atendimento"; setView("tabelas"); });
  body.querySelectorAll("[data-pub]").forEach(x => x.onclick = () => act(() => api(`/api/storefronts/${x.dataset.pub}`, { method: "PATCH", body: { published: x.dataset.val === "1" } }), "Página atualizada"));
  bindClosingActions(body);
  const sf = $("#sfForm"); if (sf) sf.onsubmit = async e => { e.preventDefault(); const d = new FormData(sf);
    const r = await act(() => api(`/api/partners/${b.partner.id}/storefronts`, { method: "POST", body: { slug: d.get("slug"), title: d.get("title"), template: d.get("template"),
      config: { headline: d.get("headline"), accent: d.get("accent") }, product_ids: d.getAll("p") } }), "Página criada");
    if (d.get("publish")) await act(() => api(`/api/storefronts/${r.id}`, { method: "PATCH", body: { published: true } }), "Página publicada"); };
  const tf = $("#taskForm"); if (tf) tf.onsubmit = async e => { e.preventDefault(); const d = new FormData(tf); const [atype, aid] = (d.get("assignee") || ":").split(":");
    if (!d.get("title") && !d.get("template")) return toast("Informe um título ou escolha um modelo de checklist", true);
    await act(() => api("/api/tasks", { method: "POST", body: { title: d.get("title") || null, template: d.get("template") || null, description: d.get("description"), partner_id: b.partner.id, unit_id: d.get("unit_id") || null,
      assignee_type: atype || null, assignee_id: aid || null, priority: d.get("priority"), due_at: d.get("due") ? new Date(d.get("due")).toISOString() : null,
      recurrence_rule: d.get("recurrence") || null, kind: d.get("kind") } }), "Tarefa criada"); };
  const idf = $("#ideaForm"); if (idf) idf.onsubmit = async e => { e.preventDefault(); const d = new FormData(idf);
    await act(() => api(`/api/partners/${b.partner.id}/ideas`, { method: "POST", body: { idea: d.get("idea"), keywords: d.get("keywords") } }), "Ideia enviada: o CEO começa em instantes"); };
  const cr = $("#ceoRun"); if (cr) cr.onclick = () => { cr.disabled = true; cr.textContent = "CEO trabalhando…";
    act(() => api(`/api/partners/${b.partner.id}/ceo/run`, { method: "POST" }), "Rodada do CEO registrada").catch(() => {}); };
  const cf = $("#clForm"); if (cf) cf.onsubmit = async e => { e.preventDefault(); const d = new FormData(cf);
    await act(() => api("/api/closings", { method: "POST", body: { kind: d.get("kind"), partner_id: b.partner.id, period_type: d.get("period_type"), local_day: d.get("local_day") } }), "Fechamento preparado"); };
}
function taskForm(b) {
  const people = [...b.floors.equipe.pessoas, ...(b.floors.equipe.central || [])];
  return `<form class="f" id="taskForm"><label>Título (opcional com modelo)<input name="title" maxlength="200"></label><label>Descrição<textarea name="description" rows="2"></textarea></label>
    <label>Modelo de checklist<select name="template"><option value="">— nenhum —</option>${Object.entries(S.templates || {}).map(([k, t]) => `<option value="${k}">${esc(t.title)} (${t.items.length} itens)</option>`).join("")}</select></label>
    <div class="row"><label>Tipo<select name="kind"><option value="tarefa">Tarefa</option><option value="pendencia">Pendência</option></select></label>
    <label>Unidade<select name="unit_id"><option value="">—</option>${b.floors.recepcao.units.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select></label>
    <label>Prioridade<select name="priority"><option>baixa</option><option selected>media</option><option>alta</option><option>critica</option></select></label></div>
    <label>Responsável<select name="assignee"><option value=":">—</option><optgroup label="Pessoas">${people.map(u => `<option value="humano:${u.id}">${esc(u.name)}</option>`).join("")}</optgroup>
    <optgroup label="Agentes">${b.floors.equipe.todos_agentes.filter(a => a.enabled).map(a => `<option value="agente:${a.id}">${esc(a.name)}</option>`).join("")}</optgroup></select></label>
    <div class="row"><label>Prazo<input type="datetime-local" name="due"></label><label>Recorrência<select name="recurrence"><option value="">nenhuma</option><option value="diaria">diária</option><option value="semanal">semanal</option><option value="mensal">mensal</option></select></label></div>
    <button class="btn primary">Criar</button></form>`;
}
function ordersTable(rows) {
  if (!rows.length) return `<p class="muted">Nenhum pedido.</p>`;
  return `<table class="t"><thead><tr><th>Código</th><th>Status</th><th>Total</th><th>Cliente</th><th>Criado</th></tr></thead><tbody>${rows.map(o =>
    `<tr class="click" data-order="${o.id}"><td class="mono">${esc(o.code)} ${o.is_demo ? badge("demo", "sim") : ""}</td><td>${esc(ORDER_STATES[o.status] || o.status)}</td><td>${fmtMoney(o.total_minor, o.currency)}</td><td>${esc(o.customer_name)}</td><td>${fmtTime(o.created_at)}</td></tr>`).join("")}</tbody></table>`;
}
function tasksTable(rows) {
  if (!rows.length) return `<p class="muted">Nenhuma tarefa.</p>`;
  return `<table class="t"><thead><tr><th>Tarefa</th><th>Status</th><th>Responsável</th><th>Prazo</th></tr></thead><tbody>${rows.map(t =>
    `<tr class="click" data-task-open="${t.id}"><td>${esc(t.title)} ${t.overdue ? badge("atrasada", "bad") : ""}</td><td>${esc(TASK_STATES[t.status])}</td><td>${t.assignee_type === "agente" ? "🤖 " : ""}${esc(t.assignee_name || "—")}</td><td>${fmtTime(t.due_at)}</td></tr>`).join("")}</tbody></table>`;
}
function closingCard(c) {
  const t = c.totals, cur = c.currency; const flow = ["preparado", "conferido", "aprovado", "encerrado"];
  const next = { preparado: "Conferir", conferido: "Aprovar", aprovado: "Encerrar" }[c.status];
  const totals = Object.entries(t).map(([k, v]) => `<dt>${esc(k.replace(/_minor$/, "").replace(/_/g, " "))}</dt><dd>${k.endsWith("_minor") ? fmtMoney(v, cur) : esc(v)}</dd>`).join("");
  return `<div class="card"><h4>${esc({ vendas: "Vendas", comissoes: "Comissões", conciliacao_pagamentos: "Conciliação de pagamentos", servicos: "Serviços do mês" }[c.kind])} · ${esc(c.local_label)} · rev. ${c.revision}
    ${c.is_demo ? badge("teste/demo", "sim") : ""} ${c.superseded_by_id ? badge("substituída", "warn") : ""}</h4>
    <div class="row">${flow.map(s => badge(s, s === c.status ? "ok" : "")).join(" → ")} · liquidação: ${badge({ nao_aplicavel: "não se aplica", pendente: "pendente (sem transferência)", liquidado: "liquidado" }[c.settlement_status] || c.settlement_status, c.settlement_status === "liquidado" ? "ok" : "")}</div>
    <dl class="kv" style="margin-top:6px">${totals}</dl>
    ${c.divergences.length ? `<div class="card"><b>Divergências</b><ul>${c.divergences.map(d => `<li>${esc(d.detalhe)} ${d.pedido ? `<span class="mono">${esc(d.pedido)}</span>` : ""}</li>`).join("")}</ul></div>` : ""}
    <div class="muted">Preparado por ${esc(c.prepared_by)} ${c.reviewed_by ? `· conferido por ${esc(c.reviewed_by)}` : ""} ${c.approved_by ? `· aprovado por ${esc(c.approved_by)}` : ""} · fuso ${esc(c.timezone)}</div>
    ${c.payouts.map(p => `<div class="muted">Repasse ${fmtMoney(p.amount_minor, p.currency)} · ref. ${esc(p.reference)} ${sim(p.is_simulated)}</div>`).join("")}
    ${c.superseded_by_id ? "" : `<div class="row" style="margin-top:6px">
      ${next ? `<button class="btn small" data-cl-adv="${c.id}" data-div="${c.divergences.length}">${next}</button>` : ""}
      ${["aprovado", "encerrado"].includes(c.status) && c.settlement_status === "pendente" && can("closings.settle") ? `<button class="btn small" data-cl-settle="${c.id}">Registrar liquidação</button>` : ""}
      ${["aprovado", "encerrado"].includes(c.status) && can("closings.reopen") && c.settlement_status !== "liquidado" ? `<button class="btn small" data-cl-reopen="${c.id}">Reabrir (nova revisão)</button>` : ""}</div>`}</div>`;
}
function bindClosingActions(root) {
  root.querySelectorAll("[data-cl-adv]").forEach(x => x.onclick = () => { const note = x.dataset.div !== "0" ? prompt("Há divergências. Justificativa:") || "" : "";
    act(() => api(`/api/closings/${x.dataset.clAdv}/advance`, { method: "POST", body: { note } }), "Fechamento atualizado"); });
  root.querySelectorAll("[data-cl-settle]").forEach(x => x.onclick = () => { const ref = prompt(S.me.environment === "demo" ? "Referência (demo: sem transferência real)" : "Referência da transferência bancária");
    if (ref !== null) act(() => api(`/api/closings/${x.dataset.clSettle}/settle`, { method: "POST", body: { reference: ref } }), "Liquidação registrada"); });
  root.querySelectorAll("[data-cl-reopen]").forEach(x => x.onclick = () => { const r = prompt("Motivo da reabertura:");
    if (r) act(() => api(`/api/closings/${x.dataset.clReopen}/reopen`, { method: "POST", body: { reason: r } }), "Nova revisão criada"); });
}
function agentCard(a) {
  const lr = a.last_run;
  return `<div class="card click" data-agent="${a.id}" style="cursor:pointer"><h4><span class="avatar-dot" style="background:rgb(${(AGENT_RGB_SAFE(a.status)).join(",")})"></span>${esc(a.name)} · ${esc(a.role)}</h4>
    <div>${badge(AGENT_LABEL[a.status] || a.status)} desde ${fmtTime(a.status_since)}</div>
    ${a.current_task ? `<div>Tarefa atual: ${esc(a.current_task.title)}</div>` : ""}
    ${lr ? `<div class="muted">Último resultado: ${esc(lr.summary || "")}</div>` : `<div class="muted">Sem execuções registradas.</div>`}</div>`;
}
const AGENT_RGB_SAFE = s => ({ disponivel: [94, 234, 212], executando: [96, 165, 250], aguardando_integracao: [251, 191, 36], aguardando_decisao: [167, 139, 250],
  falhou: [248, 113, 113], concluido: [52, 211, 153], desativado: [100, 116, 139] }[s] || [148, 163, 184]);

// ---------- outros painéis
async function openAgent(id) {
  const [all, runs, aTasks] = await Promise.all([api("/api/agents"), api(`/api/agents/${id}/runs`), can("tasks.read") ? api(`/api/tasks?assignee_id=${id}`).catch(() => []) : []]);
  const a = all.find(x => x.id === id); const c = a.config;
  openPanel({ title: esc(a.name), sub: `${esc(a.role)} · ${badge(AGENT_LABEL[a.status] || a.status)}`, reload: () => openAgent(id), html: `
    <dl class="kv"><dt>Status</dt><dd>${esc(AGENT_LABEL[a.status] || a.status)} desde ${fmtTime(a.status_since)}</dd>
    <dt>Tarefa atual</dt><dd>${a.current_task ? esc(a.current_task.title) : "—"}</dd><dt>Última atualização confirmada</dt><dd>${fmtTime(a.last_confirmed_at)}</dd>
    <dt>Próxima execução</dt><dd>${fmtTime(a.next_run_at)}</dd><dt>Custo no mês</dt><dd>${fmtMoney(a.month_spend_minor, a.budget_currency)} de ${fmtMoney(a.budget_month_minor, a.budget_currency)} <span class="muted">(rotinas determinísticas não consomem IA)</span></dd></dl>
    ${can("agents.run") && a.enabled ? `<div class="row" style="margin:8px 0"><button class="btn primary small" id="runNow">Executar agora</button></div>` : ""}
    ${can("tasks.write") && a.enabled ? agentOpForm(a) : ""}
    <h4>Tarefas deste agente (${aTasks.length})</h4>
    ${aTasks.length ? aTasks.slice(0, 12).map(t => `<div class="card click" data-task="${t.id}"><b>${esc(t.title)}</b> ${badge(TASK_STATES[t.status] || t.status, t.status === "concluida" ? "ok" : "")}
      <div class="muted">${esc(t.partner_id ? partnerName(t.partner_id) : "sem cliente")} · prazo ${fmtTime(t.due_at)} · criada ${fmtTime(t.created_at)}</div></div>`).join("") : `<p class="muted">Nenhuma tarefa atribuída.</p>`}
    <details class="card"><summary><b>Definição: ferramentas, dados, limites</b></summary><dl class="kv">
      <dt>Ferramentas</dt><dd>${(c.ferramentas || []).map(esc).join(", ")}</dd><dt>Dados permitidos</dt><dd>${(c.dados_permitidos || []).map(esc).join(", ")}</dd>
      <dt>Dados proibidos</dt><dd>${(c.dados_proibidos || []).map(esc).join(", ")}</dd><dt>Limites</dt><dd>${esc(c.limites)}</dd>
      <dt>Gatilhos</dt><dd>${(c.gatilhos || []).map(esc).join(", ")}</dd><dt>Frequência</dt><dd>${esc(c.frequencia)}</dd>
      <dt>Orçamento</dt><dd>${esc(c.orcamento)}</dd><dt>Encaminhamento humano</dt><dd>${esc(c.encaminhamento_humano)}</dd></dl></details>
    <h4>Execuções (resumos e registros de ferramentas)</h4>
    ${runs.map(r => `<div class="card"><h4>${badge(AGENT_LABEL[r.status] || r.status)} ${fmtTime(r.started_at)} · ${esc(r.trigger)}</h4><div>${esc(r.summary || "")}</div>
      <div class="muted">Custo: ${r.cost_known ? fmtMoney(r.cost_minor, r.cost_currency) : "desconhecido"} · tokens ${r.tokens_in}/${r.tokens_out}</div>
      <details><summary>Ferramentas (${r.tools.length})</summary><ul class="timeline">${r.tools.map(t => `<li><b>${esc(t.tool)}</b> ${t.ok ? "" : badge("falhou", "bad")}<div class="muted">${esc(t.input_summary)} → ${esc(t.output_summary)}</div></li>`).join("")}</ul></details>
      <details><summary>Evidências</summary><pre class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(r.evidence, null, 1))}</pre></details></div>`).join("") || `<p class="muted">Sem execuções.</p>`}` });
  const b = $("#runNow"); if (b) b.onclick = () => act(() => api(`/api/agents/${a.code}/run`, { method: "POST" }), "Execução registrada");
  $("#pBody").querySelectorAll("[data-task]").forEach(el => el.onclick = () => openTask(el.dataset.task));
  bindAgentOp(a);
}
// ------------------------------------------------------------------ operação manual para um agente
// Vira uma tarefa atribuída ao agente (auditada, com prazo e cliente); "executar agora" dispara a rotina na hora.
// O resultado volta como nota na própria tarefa. Agentes nunca recebem permissões novas por causa do texto.
const AGENT_OPS = {
  pesquisa: [["Pesquisar mercado", "Ex.: tendências de suporte de TI para clínicas em Campinas"], ["Monitorar concorrentes", "Nomes ou segmento a acompanhar"]],
  conteudo: [["Escrever texto para cliente", "Ex.: comunicado de manutenção preventiva no sábado"], ["Posts para redes sociais", "Tema e tom"]],
  analise: [["Analisar conta do cliente", "O que quer entender (atrasos, volume de chamados…)"]],
  financeiro: [["Projeção do contrato", "Período ou dúvida financeira"]],
  coordenador: [["Revisar prazos e pendências", "Foco da revisão (cliente, base…)"]],
  catalogo: [["Revisar catálogo", "Produtos ou ideia a conferir"]],
  estoque: [["Conferir estoque", "Itens ou armazém"]],
};
const whoLabel = a => { const m = /^agent:(.+)$/.exec(a || ""); const ag = m && (S.world.agents || []).find(x => x.id === m[1]);
  return ag ? "Agente " + ag.name : (a || ""); };
const partnerName = pid => ((S.world.partners || []).find(p => p.id === pid) || {}).name || pid;
function agentOpForm(a) {
  const ops = AGENT_OPS[a.code] || (a.code.startsWith("ceo_") ? [["Ideia ou análise para o CEO", "Descreva"]] : [["Tarefa livre", ""]]);
  const own = a.code.startsWith("ceo_") ? a.code.slice(4) : null;
  return `<form class="f card" id="agOp"><h4>Nova operação manual para ${esc(a.name.split(" · ")[0])}</h4>
    <div class="row"><label>Operação<select name="op">${ops.map(([t], i) => `<option value="${i}">${esc(t)}</option>`).join("")}<option value="livre">Outra (livre)</option></select></label>
    <label>Cliente<select name="partner_id"><option value="">— nenhum —</option>${(S.world.partners || []).map(p => `<option value="${p.id}" ${p.id === own ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select></label>
    <label>Prazo<input type="datetime-local" name="due"></label><label>Prioridade<select name="priority"><option value="media">média</option><option value="alta">alta</option><option value="baixa">baixa</option><option value="critica">crítica</option></select></label></div>
    <label>Título<input name="title" required maxlength="160" value="${esc(ops[0][0])}"></label>
    <label>Instruções<textarea name="description" rows="3" placeholder="${esc(ops[0][1])}"></textarea></label>
    <label class="row"><input type="checkbox" name="now" checked> executar agora (senão, entra na fila do agente)</label>
    <button class="btn primary small">Criar operação</button></form>`;
}
function bindAgentOp(a) {
  const f = $("#agOp"); if (!f) return;
  const ops = AGENT_OPS[a.code] || [];
  f.op.onchange = () => { const o = ops[+f.op.value]; if (o) { f.title.value = o[0]; f.description.placeholder = o[1]; } else { f.title.value = ""; f.description.placeholder = ""; } };
  f.onsubmit = async e => { e.preventDefault(); const d = new FormData(f);
    if (a.code.startsWith("ceo_")) {  // CEO: a operação vira uma ideia do cliente, que ele estuda e divide com a equipe
      const pid = a.code.slice(4), idea = [d.get("title"), d.get("description")].filter(Boolean).join(". ");
      await act(() => api(`/api/partners/${pid}/ideas`, { method: "POST", body: { idea } }), "Enviado ao CEO");
      if (d.get("now")) await act(() => api(`/api/partners/${pid}/ceo/run`, { method: "POST" }), "CEO trabalhando").catch(() => {});
      return; }
    const t = await act(() => api("/api/tasks", { method: "POST", body: { title: d.get("title"), description: d.get("description") || null, partner_id: d.get("partner_id") || null,
      priority: d.get("priority"), due_at: d.get("due") ? new Date(d.get("due")).toISOString() : null, assignee_type: "agente", assignee_id: a.id, kind: "tarefa", source: "manual" } }), "Operação criada");
    if (d.get("now") && can("agents.run")) await act(() => api(`/api/agents/${a.code}/run`, { method: "POST" }), "Agente executando — o resultado aparece na tarefa").catch(() => {});
    if (t && t.id) openTask(t.id); };
}
async function openOrder(id) {
  const o = await api(`/api/orders/${id}`);
  openPanel({ title: `Pedido <span class="mono">${esc(o.code)}</span>`, sub: `${esc(ORDER_STATES[o.status] || o.status)} ${o.is_demo ? badge("demonstração", "sim") : ""}`, reload: () => openOrder(id), html: `
    <dl class="kv"><dt>Vendedor</dt><dd>${esc(o.seller_of_record)}</dd><dt>Modelo</dt><dd>${esc(o.sales_model)}</dd><dt>Total</dt><dd>${fmtMoney(o.total_minor, o.currency)} (frete ${fmtMoney(o.shipping_minor, o.currency)})</dd>
    <dt>Cliente</dt><dd>${esc(o.customer_name)} · ${esc(o.customer_email)}</dd></dl>
    <div class="row" style="margin:8px 0">
      ${o.status === "pago" && can("shipments.create") ? `<button class="btn primary small" data-a="ship">Registrar envio</button>` : ""}
      ${["aguardando_pagamento", "pago"].includes(o.status) && can("orders.cancel") ? `<button class="btn danger small" data-a="cancel">Cancelar pedido</button>` : ""}
      ${o.status === "enviado" && can("returns.create") && !o.returns.length ? `<button class="btn small" data-a="return">Abrir devolução</button>` : ""}
      ${o.returns.filter(r => r.status === "aguardando_recebimento").map(r => can("returns.create") ? `<button class="btn small" data-recv="${r.id}">Registrar recebimento da devolução</button>` : "").join("")}</div>
    <h4>Itens</h4>${o.items.map(i => `<div>${i.qty}× ${esc(i.name)} — ${fmtMoney(i.line_total_minor, o.currency)}</div>`).join("")}
    <h4>Reservas</h4>${o.reservations.map(r => `<div>${esc(r.product_id)} ×${r.qty} · ${badge(r.status)} ${r.close_reason ? esc(r.close_reason) : ""}</div>`).join("")}
    <h4>Pagamentos</h4>${o.payments.map(p => `<div>${esc(p.provider)} · ${badge(p.status)} ${sim(p.is_simulated)} · taxa ${fmtMoney(p.fee_minor, p.currency)}</div>`).join("")}
    <h4>Envio</h4>${o.shipment ? `<div>${esc(o.shipment.tracking_code)} · ${badge(o.shipment.status)} ${sim(o.shipment.is_simulated)}</div>` : `<p class="muted">Não registrado.</p>`}
    <h4>Comissão</h4>${o.commissions.map(c => `<div>${esc(c.kind)} · ${fmtMoney(c.amount_minor, c.currency)} (${(c.rate_bp / 100).toFixed(2)}% de ${fmtMoney(c.base_minor, c.currency)}) · ${badge(c.status)}</div>`).join("") || `<p class="muted">Ainda não apurada (apuração ocorre no envio).</p>`}
    ${o.refunds.length ? `<h4>Reembolsos</h4>${o.refunds.map(r => `<div>${fmtMoney(r.amount_minor, r.currency)} · ${badge(r.status)} · ${esc(r.reason)}</div>`).join("")}` : ""}
    <h4>Linha do tempo (ocorrido / recebido)</h4><ul class="timeline">${o.timeline.map(e => `<li><b>${esc(e.type)}</b> ${badge(e.status)}<div class="muted">ocorrido ${fmtTime(e.occurred_at)} · recebido ${fmtTime(e.received_at)} · origem ${esc(e.source)}</div></li>`).join("")}</ul>` });
  const body = $("#pBody");
  body.querySelectorAll("[data-a]").forEach(b => b.onclick = async () => {
    if (b.dataset.a === "ship") return act(() => api(`/api/orders/${o.id}/ship`, { method: "POST" }), "Envio registrado e comissão apurada");
    if (b.dataset.a === "cancel") { const reason = prompt("Motivo do cancelamento:"); if (!reason) return;
      const r = await act(() => api(`/api/orders/${o.id}/cancel`, { method: "POST", body: { reason } }));
      toast(`Cancelado. ${r.efeitos_externos.join(" ")} ${r.reembolso ? "Reembolso: " + r.reembolso.modo : ""}`); return; }
    if (b.dataset.a === "return") { const reason = prompt("Motivo da devolução:"); if (reason) act(() => api(`/api/orders/${o.id}/return`, { method: "POST", body: { reason } }), "Devolução aberta"); }
  });
  body.querySelectorAll("[data-recv]").forEach(b => b.onclick = () => act(() => api(`/api/returns/${b.dataset.recv}/receive`, { method: "POST" }), "Recebida; reembolso enviado para aprovação"));
}
async function openTask(id) {
  const t = await api(`/api/tasks/${id}`);
  const agentsList = S.world.agents.filter(a => a.enabled);
  openPanel({ title: esc(t.title), sub: `${esc(TASK_STATES[t.status])} · ${badge(t.priority)} ${t.overdue ? badge("atrasada", "bad") : ""} ${t.is_demo ? badge("demonstração", "sim") : ""}`, reload: () => openTask(id), html: `
    <p>${esc(t.description || "")}</p>
    ${t.checklist ? `<div class="card"><h4>Checklist ${t.checklist.filter(i => i.done).length}/${t.checklist.length}</h4>${t.checklist.map((i, n) =>
      `<label class="row" style="margin:3px 0"><input type="checkbox" data-ck="${n}" ${i.done ? "checked" : ""} ${["concluida", "cancelada"].includes(t.status) || !can("tasks.write") ? "disabled" : ""}>
       <span>${esc(i.item)}</span>${i.done ? `<span class="muted"> · ${esc(i.by_name || i.by || "")} ${fmtTime(i.at)}</span>` : ""}</label>`).join("")}
      <div class="muted">A tarefa só pode ser concluída com todos os itens marcados.</div></div>` : ""}
    <dl class="kv"><dt>Tipo</dt><dd>${esc(t.kind)} ${t.pendency_type ? "· " + esc(t.pendency_type) : ""}</dd><dt>Responsável</dt><dd>${esc(t.assignee_name || "—")} ${t.assignee_type === "agente" ? badge("agente de IA") : ""}</dd>
    <dt>Prazo</dt><dd>${fmtTime(t.due_at)}</dd><dt>Origem</dt><dd>${esc(t.source || "")}</dd><dt>Dependências</dt><dd>${t.depends_on.map(esc).join(", ") || "—"}</dd>
    <dt>Concluída por</dt><dd>${t.completed_by ? `${esc(t.completed_by_name || t.completed_by)} em ${fmtTime(t.completed_at)}` : "—"}</dd></dl>
    ${can("tasks.write") ? `<form class="f card" id="tEdit"><div class="row"><label>Status<select name="status">${Object.entries(TASK_STATES).map(([k, v]) => `<option value="${k}" ${k === t.status ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="${S.me.role === "tecnico" ? "hidden" : ""}">Atribuir a agente<select name="agent"><option value="">— manter —</option>${agentsList.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join("")}</select></label></div>
      <label>Comentário<textarea name="note" rows="2"></textarea></label><button class="btn primary small">Salvar</button></form>
      <form class="f card" id="tFile"><label>Anexar documento (PDF, imagem, CSV, TXT, DOCX, XLSX · até 10 MB)<input type="file" name="file" required></label>
      <label class="row"><input type="checkbox" name="ev"> Evidência de conclusão</label><button class="btn small">Enviar anexo</button></form>` : ""}
    <h4>Anexos</h4>${t.attachments.map(a => `<div><a href="/api/attachments/${a.id}">${esc(a.filename)}</a> ${a.is_evidence ? badge("evidência", "ok") : ""} <span class="muted mono">sha256 ${a.sha256.slice(0, 12)}… · ${esc(a.uploaded_by)}</span></div>`).join("") || `<p class="muted">Nenhum.</p>`}
    <h4>Comentários</h4>${t.comments.map(c => `<div class="card"><div class="muted">${esc(whoLabel(c.author))} · ${fmtTime(c.created_at)}</div><div style="white-space:pre-wrap">${esc(c.body)}</div></div>`).join("") || `<p class="muted">Nenhum.</p>`}
    <h4>Histórico</h4><ul class="timeline">${t.history.map(h => `<li>${esc(h.field)}: ${esc(h.old ?? "")} → ${esc(h.new ?? "")}<div class="muted">${esc(whoLabel(h.actor))} · ${fmtTime(h.at)}</div></li>`).join("")}</ul>` });
  $("#pBody").querySelectorAll("[data-ck]").forEach(x => x.onchange = () =>
    api(`/api/tasks/${id}/checklist`, { method: "POST", body: { index: +x.dataset.ck, done: x.checked } }).then(() => openTask(id)).catch(er => { toast(er.message, true); openTask(id); }));
  const f = $("#tEdit"); if (f) f.onsubmit = e => { e.preventDefault(); const d = new FormData(f); const body = { status: d.get("status") };
    if (d.get("note")) body.note = d.get("note"); if (d.get("agent")) { body.assignee_type = "agente"; body.assignee_id = d.get("agent"); }
    act(() => api(`/api/tasks/${id}`, { method: "PATCH", body }), "Tarefa atualizada"); };
  const ff = $("#tFile"); if (ff) ff.onsubmit = e => { e.preventDefault(); const file = ff.file.files[0]; if (!file) return;
    if (file.size > 10 * 1024 * 1024) return toast("Arquivo acima de 10 MB", true);
    const rd = new FileReader(); rd.onload = () => act(() => api(`/api/tasks/${id}/attachments`, { method: "POST", body: { filename: file.name, mime: file.type || "application/octet-stream",
      content_base64: String(rd.result).split(",")[1], is_evidence: ff.ev.checked } }), "Anexo registrado"); rd.readAsDataURL(file); };
}
function openRegion(code) {
  const r = S.world.regions.find(x => x.code === code); const ps = S.world.partners.filter(p => p.region_code === code);
  openPanel({ title: esc(r.name), sub: `${esc(r.currency)} · ${esc(r.locale)} · ${esc(r.timezone)}`, html: `
    <dl class="kv"><dt>Vendas</dt><dd>${r.sales_enabled ? badge("liberadas", "ok") : badge("bloqueadas", "bad")} ${esc(r.sales_status_note || "")}</dd>
    <dt>Entidade vendedora</dt><dd>${esc(r.seller_entity)}</dd><dt>Regras fiscais</dt><dd>${esc(r.tax_status_note || "")}</dd></dl>
    <h4>Parceiros</h4>${ps.map(p => `<div class="card click" data-p="${p.id}" style="cursor:pointer">${st(p.status.level)} <b>${esc(p.name)}</b>
      <div class="muted">${p.units.map(u => `${esc(u.name)} (${esc(u.city || "")}/${esc(u.state || "")} · ${u.location_status === "confirmada" ? "local confirmado" : "local não confirmado"})`).join(" · ")}</div></div>`).join("")}` });
  $("#pBody").querySelectorAll("[data-p]").forEach(x => x.onclick = () => select("partner", x.dataset.p));
}
function openBase(b) {
  if (!b) return;
  const clients = [...new Set(b.units.map(u => u.partner_id))].map(id => S.world.partners.find(p => p.id === id)).filter(Boolean);
  openPanel({ title: `Base · ${esc(b.name)}`, sub: `${esc(b.city || "")}/${esc(b.state || "")} ${b.location_status === "confirmada" ? badge("endereço confirmado", "ok") : badge("posição ilustrativa")}`,
    html: `<dl class="kv"><dt>Técnicos ativos</dt><dd>${b.tecnicos}</dd><dt>Aguardando autorização</dt><dd>${b.pendentes}</dd><dt>Unidades atendidas</dt><dd>${b.units.length}</dd></dl>
    <h4>Clientes atendidos</h4>${clients.map(p => `<div class="card click" data-p="${p.id}" style="cursor:pointer">${st(p.status.level)} ${esc(p.name)}</div>`).join("") || `<p class="muted">Nenhuma unidade vinculada ainda.</p>`}
    <h4>CEO da base</h4><div class="card" id="baseCeo"><p class="muted">Carregando…</p></div>
    ${can("cadastros.read") ? `<button class="btn small" id="goCad">Abrir cadastros</button>` : ""}`, reload: () => openBase((S.world.service_bases || []).find(x => x.id === b.id) || b) });
  if (can("cadastros.read")) api(`/api/bases/${b.id}/ceo`).then(g => { const el = $("#baseCeo"); if (!el) return; const d = g.dados || {};
    el.innerHTML = `${g.resumo ? `<div>${esc(g.resumo)}</div><div class="muted">última volta ${fmtTime(g.em)}</div>` : `<p class="muted">Ainda não rodou (roda a cada 30 min).</p>`}
      ${(d.carga || []).length ? `<h4>Carga por técnico</h4>${d.carga.map(x => `<div>${esc(x.tecnico)} · ${x.abertas} aberta(s)</div>`).join("")}` : ""}
      ${(d.por_unidade || []).length ? `<h4>Por unidade</h4><table class="t"><tbody>${d.por_unidade.map(x => `<tr><td>${esc(x.cliente)} · ${esc(x.unidade)}</td><td>${x.abertas} aberta(s)</td><td>${x.atrasadas ? badge(x.atrasadas + " atrasada(s)", "bad") : ""}</td></tr>`).join("")}</tbody></table>` : ""}
      <div class="row" style="margin-top:8px">${can("agents.run") ? `<button class="btn small primary" id="ceoBaseRun">Executar CEO agora</button>` : ""}
      <button class="btn small" id="ceoBaseAppr">Aprovações</button>
      ${can("policies.write") ? `<button class="btn small" id="ceoBaseAuto" title="Cria/altera a política tarefas.atribuir">Distribuição: alternar automática ↔ com aprovação</button>` : ""}</div>
      <p class="muted">Organiza as tarefas das unidades desta base: preventiva mensal, distribuição por carga (com sua aprovação, ou automática se você criar a política "tarefas.atribuir"), atrasos encaminhados.</p>`;
    const rb = $("#ceoBaseRun"); if (rb) rb.onclick = () => act(() => api(`/api/agents/base_${b.id}/run`, { method: "POST" }), "CEO da base executou");
    $("#ceoBaseAppr").onclick = () => { closePanel(); S.tab = "aprovacoes"; setView("tabelas"); };
    const au = $("#ceoBaseAuto"); if (au) au.onclick = async () => { const pol = (await api("/api/policies")).policies.find(x => x.action === "tarefas.atribuir");
      const mode = pol && pol.mode === "automatico" ? "revisao" : "automatico";
      await act(() => api("/api/policies/tarefas.atribuir", { method: "PUT", body: { mode } }), mode === "automatico" ? "O CEO das bases agora distribui sozinho (por carga)" : "Distribuição volta a pedir sua aprovação"); };
  }).catch(() => {});
  $("#pBody").querySelectorAll("[data-p]").forEach(x => x.onclick = () => select("partner", x.dataset.p));
  const g = $("#goCad"); if (g) g.onclick = () => { closePanel(); S.tab = "cadastros"; setView("tabelas"); };
}

// ------------------------------------------------------------------ expansão internacional
async function expandBox(country) {
  const box = $("#expBox"); if (!box) return; box.dataset.country = country;
  let d; try { d = await api(`/api/paises/${encodeURIComponent(country)}`); } catch { return; }
  if (d.mercado_id) { box.innerHTML = `<div class="card click" id="goMkt">🌐 <b>Mercado ativo neste país</b> — abrir painel da expansão</div>`;
    box.querySelector("#goMkt").onclick = () => openMarket(d.mercado_id); return; }
  const i = d.info || {};
  if (!can("mercados.write")) { box.innerHTML = ""; return; }
  box.innerHTML = `<details class="card"><summary><b>🌐 Expandir para este país</b> <span class="muted">(CEO de IA + agentes por setor)</span></summary>
    <p class="muted">Presença <b>remota</b>: site no idioma e moeda do país, anúncios e plataformas locais — sem endereço ou IP falso.
    ${i.iso2 ? `Idioma ${esc(i.lang)} · moeda ${esc(i.currency)} · dados: ${esc(i.data_law)}.` : "País fora do catálogo: informe idioma e moeda."}</p>
    <form class="f" id="mktForm"><div class="row"><label>Marca<input name="brand" required maxlength="80"></label>
      <label>Idioma<input name="lang" value="${esc(i.lang || "")}" placeholder="ex.: de-DE" required size="7"></label>
      <label>Moeda<input name="currency" value="${esc(i.currency || "")}" placeholder="EUR" required size="4" maxlength="3"></label>
      <label>Preço inicial (moeda local, opcional)<input name="price" size="8"></label></div>
      <label>O que você vende/oferece<textarea name="offer" rows="2" required minlength="8"></textarea></label>
      <div class="row"><label>Público<input name="audience" placeholder="ex.: pequenas empresas"></label><label>E-mail de contato<input name="email" type="email"></label>
      <label>WhatsApp<input name="whatsapp"></label><label>Teto de anúncios/mês (planejamento)<input name="ads_cap" size="8"></label></div>
      <button class="btn primary small">Criar mercado e ativar CEO</button></form></details>`;
  // procura o formulário dentro da própria caixa (o painel pode ter sido redesenhado)
  box.querySelector("#mktForm").onsubmit = async e => { e.preventDefault(); const f = new FormData(e.target), b = Object.fromEntries(f.entries());
    b.country = country; b.iso2 = i.iso2 || null;
    const m = await act(() => api("/api/mercados", { method: "POST", body: b }), "Mercado criado — o CEO já começou a pesquisar e dividir o trabalho");
    if (m) openMarket(m.id); };
}
const MKT_ST = { planejando: "planejando", em_execucao: "em execução", ativo: "site no ar", pausado: "pausado", encerrado: "encerrado" };
const SETOR_LBL = { revisao_juridica: "Revisão por advogado (sua)", juridico: "Jurídico", localizacao: "Site / localização", marketing: "Anúncios", vendas: "Plataformas", financeiro: "Financeiro", estrategia: "Estratégia" };
async function openMarket(id) {
  const m = await api(`/api/mercados/${id}`);
  const latest = k => m.itens.find(x => x.kind === k);
  const est = latest("estrategia"), leg = latest("juridico"), ads = latest("marketing"), pl = latest("vendas"), fin = latest("financeiro");
  const li = a => (a || []).map(x => `<li>${esc(x)}</li>`).join("");
  const pub = m.publicacao;
  openPanel({ title: `🌐 ${esc(m.country)} · ${esc(m.config.marca)}`, sub: `${badge(MKT_ST[m.status] || m.status, m.status === "ativo" ? "ok" : "")} ${badge("presença remota")} · ${esc(m.lang)} · ${esc(m.currency)}`,
    reload: () => openMarket(id), html: `
    <div class="bar" style="margin:6px 0"><i style="width:${m.progresso.total ? Math.round(100 * m.progresso.feitas / m.progresso.total) : 0}%"></i></div>
    <div class="muted">${m.progresso.feitas}/${m.progresso.total} setor(es) entregue(s) pelos agentes</div>
    <div class="row" style="margin:8px 0">${can("agents.run") ? `<button class="btn small primary" id="mkRun">Executar CEO agora</button>` : ""}
      <a class="btn small" href="/m/${esc(m.slug)}" target="_blank" rel="noopener">${m.site_published ? "Abrir site publicado" : "Ver prévia do site"}</a>
      ${pub ? badge("publicação: " + (pub.status === "aguardando_aprovacao" ? "aguardando sua aprovação" : pub.status), pub.status === "executada" ? "ok" : "warn") : ""}
      ${pub && pub.status === "aguardando_aprovacao" ? `<button class="btn small" id="mkAppr">Aprovações</button>` : ""}</div>
    ${m.site_published ? `<p class="muted">Endereço: <span class="mono">${esc(m.url)}</span>. Para o Google indexar, o servidor precisa de um endereço público (domínio ou túnel HTTPS) — configure PUBLIC_BASE_URL.</p>` : ""}
    <h4>Setores</h4><table class="t"><tbody>${m.tarefas.map(t => `<tr class="click" data-task="${t.id}"><td>${esc(SETOR_LBL[t.related_entity.split(":")[2]] || t.related_entity.split(":")[2])}</td>
      <td>${esc(t.title)}</td><td>${badge(TASK_STATES[t.status] || t.status, t.status === "concluida" ? "ok" : "")}</td></tr>`).join("") || `<tr><td class="muted">O CEO ainda está planejando…</td></tr>`}</tbody></table>
    ${est ? `<h4>Estratégia <span class="muted">(${esc(est.body.fonte)})</span></h4><p>${esc(est.body.resumo)}</p>
      ${est.body.riscos && est.body.riscos.length ? `<b>Riscos</b><ul>${li(est.body.riscos)}</ul>` : ""}
      ${(est.body.noticias || []).length ? `<details><summary>Notícias do país (${est.body.noticias.length})</summary><ul>${est.body.noticias.map(n => `<li>${esc(n.titulo)} <span class="muted">${esc(n.fonte)}</span></li>`).join("")}</ul></details>` : ""}` : ""}
    ${leg ? `<h4>Jurídico</h4><p class="muted">${esc(leg.body.aviso)}</p><table class="t"><tbody>${leg.body.itens.map(x => `<tr><td><b>${esc(x.tema)}</b></td><td>${esc(x.regra)}</td><td>${esc(x.acao)}</td></tr>`).join("")}</tbody></table>` : ""}
    ${ads ? `<h4>Anúncios (rascunho · ${esc(ads.body.fonte)})</h4><div class="card"><b>Google</b><ul>${li(ads.body.google.titulos)}</ul><div class="muted">${ads.body.google.descricoes.map(esc).join(" · ")}</div>
      <b>Meta</b><div>${esc(ads.body.meta.titulo)} — ${esc(ads.body.meta.texto)}</div><div class="muted">${esc(ads.body.verba)}</div></div>` : ""}
    ${pl ? `<h4>Plataformas de venda</h4><ul>${pl.body.plataformas.map(x => `<li>${esc(x.nome)}</li>`).join("")}</ul><p class="muted">${esc(pl.body.aviso)}</p>` : ""}
    ${fin ? `<h4>Financeiro</h4><dl class="kv"><dt>Moeda</dt><dd>${esc(fin.body.moeda)}</dd><dt>Preço local</dt><dd>${esc(fin.body.preco_local)}</dd>
      <dt>Imposto</dt><dd>${esc(fin.body.imposto)}</dd><dt>Pagamentos comuns</dt><dd>${esc(fin.body.pagamentos_comuns)}</dd></dl><p class="muted">${esc(fin.body.aviso)}</p>` : ""}` });
  $("#pBody").querySelectorAll("[data-task]").forEach(el => el.onclick = () => openTask(el.dataset.task));
  const r = $("#mkRun"); if (r) r.onclick = () => act(() => api(`/api/agents/mkt_${id}/run`, { method: "POST" }), "CEO do mercado executou");
  const ap = $("#mkAppr"); if (ap) ap.onclick = () => { closePanel(); S.tab = "aprovacoes"; setView("tabelas"); };
}
function fidelidadeHtml(pid) {
  const p = (S.world.partners || []).find(x => x.id === pid); const f = p && p.fidelidade; if (!f) return "";
  const pct = f.proximo_nivel_em == null ? 100 : Math.round(100 * f.servicos / (f.servicos + f.proximo_nivel_em));
  return `<h4>Fidelidade · cashback</h4><div class="card"><b>Nível ${f.nivel}</b> · ${f.servicos} serviço(s) concluído(s) · <b>${f.pontos} pontos</b>
    <div class="bar" style="margin:6px 0"><i style="width:${pct}%"></i></div>
    <div class="muted">${f.proximo_nivel_em == null ? "Nível máximo." : `Faltam ${f.proximo_nivel_em} serviço(s) para o nível ${f.nivel + 1} (o prédio cresce).`}
    Pontos são registro de fidelidade: não movimentam dinheiro; resgate é decisão da central.</div></div>`;
}
function openFreelancer(f) {
  openPanel({ title: esc(f.name), sub: `Freelancer · base ${esc(f.base || "")} ${f.active ? badge("autorizado", "ok") : badge("aguardando autorização", "warn")}`,
    html: `<dl class="kv"><dt>Tarefas abertas</dt><dd>${f.abertas}</dd><dt>Concluídas em 30 dias</dt><dd>${f.feitas_30d}</dd>
      <dt>Posição no mapa</dt><dd>Ilustrativa, ao lado da base (não é localização real nem rastreamento).</dd></dl>
      <div class="row" style="margin:8px 0"><button class="btn small" id="flTasks">Ver tarefas dele(a)</button>${can("cadastros.read") ? `<button class="btn small" id="flCad">Abrir cadastro</button>` : ""}</div>` });
  $("#flTasks").onclick = () => { closePanel(); S.tab = "tarefas"; setView("tabelas"); setTimeout(() => { $("#tFilter").value = f.name.split(" ·")[0]; renderTables(); }, 50); };
  const c = $("#flCad"); if (c) c.onclick = () => { closePanel(); S.tab = "cadastros"; setView("tabelas"); };
}
function openUnit(u) {
  openPanel({ title: esc(u.name), sub: esc(u.partner), html: `<dl class="kv"><dt>Cidade/estado</dt><dd>${esc(u.city || "—")}/${esc(u.state || "—")} · ${esc(u.country)}</dd>
    <dt>Localização</dt><dd>${u.location_status === "confirmada" ? badge("endereço confirmado", "ok") + " " + esc(u.location_source || "") : badge("endereço não confirmado", "warn")}</dd>
    <dt>Posição no mapa</dt><dd>${u.lat != null ? `${u.lat.toFixed(4)}, ${u.lng.toFixed(4)} · ${esc(u.geo_source || "")}${(u.geo_source || "").startsWith("cidade") ? " (aproximada, centro da cidade)" : ""}` : "ainda não localizada (aguardando geocodificação)"}</dd></dl>` });
}
async function openWarehouse(id) {
  const w = S.world.warehouses.find(x => x.id === id); const inv = (await api("/api/inventory")).filter(i => i.warehouse === w.name);
  openPanel({ title: esc(w.name), sub: `${esc(w.city)} · ${esc(w.region_code)} ${badge("posição ilustrativa")}`, reload: () => openWarehouse(id), html: `<table class="t"><thead><tr><th>SKU</th><th>Saldo</th><th>Reservado</th><th>Disponível</th><th>Ponto</th></tr></thead><tbody>
    ${inv.map(i => `<tr><td>${esc(i.sku)}<div class="muted">${esc(i.product)}</div></td><td>${i.on_hand}</td><td>${i.reserved}</td><td>${i.available < i.reorder_point ? `<b style="color:var(--warn)">${i.available}</b>` : i.available}</td><td>${i.reorder_point}</td></tr>`).join("")}</tbody></table>` });
}
async function openHQ() {
  const [appr, br] = await Promise.all([can("approvals.read") ? api("/api/approvals") : [], api("/api/brain").catch(() => null)]);
  openPanel({ title: "Administração central", sub: S.world.agents_paused ? badge("agentes pausados", "bad") : "", reload: openHQ, html:
    `<div class="card"><h4>Cérebro de IA (misto, menor custo)</h4>${brainLine(br)}<div class="muted">Sempre grátis primeiro: rotina só com IA local · decisões tentam a IA local e só sobem para o Claude se ela falhar, dentro dos tetos diário e mensal · sem IA → regras determinísticas.</div></div>` + approvalsHtml(appr) +
    `<h4>Agentes</h4>${S.world.agents.map(agentCard).join("")}` });
  bindApprovals($("#pBody")); $("#pBody").querySelectorAll("[data-agent]").forEach(x => x.onclick = () => openAgent(x.dataset.agent));
}
function approvalsHtml(rows) {
  const pend = rows.filter(r => r.status === "aguardando_aprovacao");
  return `<h4>Aprovações pendentes (${pend.length})</h4>${pend.map(r => `<div class="card"><h4>${esc(r.action)} ${r.amount_minor != null ? fmtMoney(r.amount_minor, r.currency) : ""}</h4>
    <div class="muted">${esc(r.reason || "")} · solicitado por ${esc(r.requested_by_type)}:${esc(r.requested_by)} em ${fmtTime(r.created_at)}</div>
    <pre class="mono" style="white-space:pre-wrap">${esc(JSON.stringify(r.payload))}</pre>
    ${can("approvals.decide") ? `<div class="row"><button class="btn small primary" data-ap="${r.id}" data-v="1">Aprovar</button><button class="btn small danger" data-ap="${r.id}" data-v="0">Rejeitar</button></div>` : ""}</div>`).join("") || `<p class="muted">Nenhuma.</p>`}`;
}
function bindApprovals(root) {
  root.querySelectorAll("[data-ap]").forEach(b => b.onclick = async () => {
    const r = await act(() => api(`/api/approvals/${b.dataset.ap}/decide`, { method: "POST", body: { approve: b.dataset.v === "1", note: "" } }));
    toast(`Solicitação ${r.status}${r.status === "aguardando_integracao" ? ": aprovada, mas sem integração para executar externamente" : ""}`);
  });
}
async function openSector(code) {
  const s = S.world.sectors.find(x => x.code === code);
  let html = `<p class="muted">${esc(s.metric)}</p>`;
  if (code === "logistica") { const os = await api("/api/orders?status=pago"); html += `<h4>Pedidos pagos aguardando envio</h4>${ordersTable(os)}`; }
  if (code === "financeiro") { html += approvalsHtml(await api("/api/approvals")); }
  if (code === "catalogo") { const inv = await api("/api/inventory"); html += `<h4>Itens por região</h4><table class="t"><tbody>${inv.map(i => `<tr><td>${esc(i.region_code)}</td><td>${esc(i.sku)}</td><td>${esc(i.product)}</td></tr>`).join("")}</tbody></table>`; }
  if (code === "atendimento") html += `<div class="card">Módulo de atendimento: <b>funcionalidade planejada</b> (Etapa 3). O agente de atendimento está desativado.</div>`;
  openPanel({ title: `Setor · ${esc(s.name)}`, sub: badge("posição ilustrativa"), html, reload: () => openSector(code) });
  bindApprovals($("#pBody")); $("#pBody").querySelectorAll("[data-order]").forEach(x => x.onclick = () => openOrder(x.dataset.order));
}

// ------------------------------------------------------------------ modo tabelas
const TABLE_TABS = [["quadro", "Quadro de tarefas"], ["atendimento", "Atendimento"], ["cadastros", "Cadastros"], ["visao", "Visão geral"], ["pedidos", "Pedidos"], ["tarefas", "Tarefas e pendências"], ["agentes", "Agentes"], ["fechamentos", "Fechamentos"],
  ["aprovacoes", "Aprovações"], ["estoque", "Estoque"], ["eventos", "Eventos"], ["webhooks", "Webhooks"], ["auditoria", "Auditoria"], ["politicas", "Políticas"]];
async function renderTables(soft, auto = false) {
  // Só a chamada mais recente desenha (uma resposta lenta e antiga nunca cobre dados novos);
  // atualização automática desiste se a pessoa começou a digitar enquanto os dados chegavam.
  const seq = S.tSeq = (S.tSeq || 0) + 1, STALE = { stale: true };
  const fresh = () => seq === S.tSeq && !(auto && editing("#tBody"));
  const get = async (url, opts) => { const d = await api(url, opts); if (!fresh()) throw STALE; return d; };
  const tabs = S.me.role === "tecnico" ? [["tarefas", "Minhas tarefas"]] :
    TABLE_TABS.filter(([k]) => (k === "atendimento" ? can("atendimento.read") : (S.me.is_central && (k !== "cadastros" || can("cadastros.read")))
      || ["quadro", "visao", "pedidos", "tarefas", "agentes", "fechamentos", "eventos"].includes(k)));
  $("#tTabs").innerHTML = tabs.map(([k, v]) => `<button role="tab" aria-selected="${k === S.tab}" data-t="${k}">${v}</button>`).join("");
  $("#tTabs").querySelectorAll("button").forEach(b => b.onclick = () => { S.tab = b.dataset.t; $("#tFilter").value = ""; renderTables(); });
  const body = $("#tBody"); if (!soft) body.innerHTML = `<p class="muted">Carregando…</p>`;
  const q = $("#tFilter").value.trim().toLowerCase();
  const grid = (cols, rows, onClick) => {
    const f = rows.filter(r => !q || JSON.stringify(r).toLowerCase().includes(q));
    body.innerHTML = `<table class="t"><thead><tr>${cols.map(c => `<th data-k="${c[0]}">${c[1]}</th>`).join("")}</tr></thead><tbody>${f.map((r, i) =>
      `<tr class="${onClick ? "click" : ""}" data-i="${rows.indexOf(r)}">${cols.map(c => `<td>${c[2] ? c[2](r) : esc(r[c[0]])}</td>`).join("")}</tr>`).join("")}</tbody></table>
      ${f.length ? "" : `<p class="muted">Nada encontrado.</p>`}`;
    body.querySelectorAll("th").forEach(th => th.onclick = () => { const k = th.dataset.k; rows.sort((a, b) => String(a[k] ?? "").localeCompare(String(b[k] ?? ""), "pt-BR", { numeric: true })); grid(cols, rows, onClick); });
    if (onClick) body.querySelectorAll("tr[data-i]").forEach(tr => tr.onclick = () => onClick(rows[+tr.dataset.i]));
  };
  try {
    if (S.tab === "cadastros") return renderCadastros(body, fresh);
    if (S.tab === "quadro") return renderKanban(body, fresh);
    if (S.tab === "atendimento") return renderInbox(body, fresh);
    if (S.tab === "visao") grid([["name", "Parceiro"], ["region", "Região"], ["status", "Sinalização", r => st(r.status)], ["model", "Modelo"], ["overdue_tasks", "Tarefas atrasadas"],
      ["pending_closings", "Fechamentos pendentes"], ["failed_integrations", "Integrações com falha"], ["sales", "Vendas confirmadas"], ["is_demo", "Ambiente", r => r.is_demo ? badge("demo", "sim") : ""]],
      await get("/api/overview"), r => select("partner", r.id));
    if (S.tab === "pedidos") grid([["code", "Código", r => `<span class="mono">${esc(r.code)}</span>`], ["status", "Status", r => esc(ORDER_STATES[r.status] || r.status)], ["partner_id", "Parceiro"],
      ["region_code", "Região"], ["total_minor", "Total", r => fmtMoney(r.total_minor, r.currency)], ["customer_name", "Cliente"], ["created_at", "Criado", r => fmtTime(r.created_at)]],
      await get("/api/orders"), r => openOrder(r.id));
    if (S.tab === "tarefas") grid([["title", "Título", r => esc(r.title) + (r.overdue ? " " + badge("atrasada", "bad") : "")], ["kind", "Tipo"], ["status", "Status", r => esc(TASK_STATES[r.status])],
      ["priority", "Prioridade"], ["partner_id", "Cliente/parceiro"], ["assignee_name", "Responsável"], ["due_at", "Prazo", r => fmtTime(r.due_at)]], await get("/api/tasks"), r => openTask(r.id));
    if (S.tab === "agentes") grid([["name", "Agente"], ["role", "Função"], ["status", "Status", r => badge(AGENT_LABEL[r.status] || r.status)], ["status_since", "Desde", r => fmtTime(r.status_since)],
      ["current_task", "Tarefa atual", r => esc(r.current_task ? r.current_task.title : "—")], ["last_confirmed_at", "Última confirmação", r => fmtTime(r.last_confirmed_at)],
      ["last_run", "Último resultado", r => esc(r.last_run ? r.last_run.summary : "—")], ["month_spend_minor", "Custo mês", r => fmtMoney(r.month_spend_minor, r.budget_currency)]],
      await get("/api/agents"), r => openAgent(r.id));
    if (S.tab === "fechamentos") { const rows = await get("/api/closings"); body.innerHTML = rows.map(closingCard).join("") || `<p class="muted">Nenhum.</p>`; bindClosingActions(body); }
    if (S.tab === "aprovacoes") { const rows = await get("/api/approvals"); body.innerHTML = approvalsHtml(rows) + `<h4>Histórico</h4>`;
      const hist = document.createElement("div"); body.appendChild(hist); bindApprovals(body);
      hist.innerHTML = `<table class="t"><tbody>${rows.filter(r => r.status !== "aguardando_aprovacao").map(r => `<tr><td>${esc(r.action)}</td><td>${badge(r.status)}</td><td>${esc(r.decided_by || "")}</td><td>${fmtTime(r.created_at)}</td><td class="mono">${esc(JSON.stringify(r.effects || ""))}</td></tr>`).join("")}</tbody></table>`; }
    if (S.tab === "estoque") grid([["region_code", "Região"], ["warehouse", "Armazém"], ["sku", "SKU"], ["product", "Produto"], ["on_hand", "Saldo"], ["reserved", "Reservado"],
      ["available", "Disponível", r => r.available < r.reorder_point ? `<b style="color:var(--warn)">${r.available}</b>` : r.available], ["reorder_point", "Ponto"]], await get("/api/inventory"));
    if (S.tab === "eventos") grid([["id", "#"], ["type", "Tipo"], ["status", "Status"], ["source", "Origem"], ["entity_type", "Entidade", r => `${esc(r.entity_type || "")} <span class="mono">${esc(r.entity_id || "")}</span>`],
      ["correlation_id", "Correlação", r => `<span class="mono">${esc(r.correlation_id || "")}</span>`], ["occurred_at", "Ocorrido", r => fmtTime(r.occurred_at)], ["received_at", "Recebido", r => fmtTime(r.received_at)]],
      (await get("/api/events?after=0&limit=1000")).events.reverse());
    if (S.tab === "webhooks") grid([["event_type", "Evento"], ["status", "Status"], ["attempts", "Tentativas"], ["occurred_at", "Ocorrido", r => fmtTime(r.occurred_at)], ["received_at", "Recebido", r => fmtTime(r.received_at)],
      ["last_error", "Erro"], ["id", "Ações", r => `${r.status === "dead_letter" ? `<button class="btn small" data-rq="${r.id}">Reprocessar</button>` : ""}${S.me.environment === "demo" ? ` <button class="btn small" data-rp="${r.id}" title="Reenvia o mesmo evento para testar duplicidade">Reenviar (teste)</button>` : ""}`]],
      await get("/api/webhooks/inbox"));
    if (S.tab === "auditoria") grid([["id", "#"], ["at", "Quando", r => fmtTime(r.at)], ["actor_type", "Ator", r => `${esc(r.actor_type)}:${esc(r.actor_id)}`], ["action", "Ação"],
      ["entity_type", "Entidade", r => `${esc(r.entity_type || "")} <span class="mono">${esc(r.entity_id || "")}</span>`], ["after_json", "Depois", r => `<span class="mono">${esc((r.after_json || "").slice(0, 120))}</span>`]], await get("/api/audit"));
    if (S.tab === "politicas") { const d = await get("/api/policies");
      body.innerHTML = `<p class="muted">Sem política cadastrada, a ação é preparada para revisão — nunca executada.</p><table class="t"><thead><tr><th>Ação</th><th>Modo</th><th>Limite</th><th>Aprovador</th><th>Descrição</th></tr></thead><tbody>
      ${d.policies.map(p => `<tr><td class="mono">${esc(p.action)}</td><td>${badge(p.mode)}</td><td>${p.limit_minor != null ? fmtMoney(p.limit_minor, p.currency) : "—"}</td><td>${esc(p.approver_role || "—")}</td><td>${esc(p.description || "")}</td></tr>`).join("")}
      ${d.actions_without_policy.map(a => `<tr><td class="mono">${esc(a.action)}</td><td>${badge("sem política → revisão", "warn")}</td><td>—</td><td>—</td><td>${esc(a.description)}</td></tr>`).join("")}</tbody></table>`; }
    body.querySelectorAll("[data-rq]").forEach(b => b.onclick = e => { e.stopPropagation(); act(() => api(`/api/webhooks/inbox/${b.dataset.rq}/requeue`, { method: "POST" }), "Reprocessado"); });
    body.querySelectorAll("[data-rp]").forEach(b => b.onclick = async e => { e.stopPropagation(); const r = await act(() => api("/api/demo/replay-webhook", { method: "POST", body: { inbox_id: b.dataset.rp } })); toast("Resultado: " + r.status); });
  } catch (e) { if (e === STALE) return; if (!e.status && !/fetch|network|load failed/i.test(e.message) && window.__vigia) window.__vigia(e.message, "", 0, e.stack, "tabela:" + S.tab);  // erro de programa (não de regra de negócio) vai para o vigia
    body.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ cadastros (operação real)
const ROLE_LABEL = { admin_central: "Administrador", operacoes_central: "Operações", financeiro_central: "Financeiro",
  parceiro_admin: "Cliente (gestor)", parceiro_operador: "Cliente (operador)", tecnico: "Técnico / freelancer" };
async function renderCadastros(body, fresh) {
  const [d, chs] = await Promise.all([api("/api/cadastros"), can("atendimento.read") ? api("/api/channels").catch(() => ({ contacts: [] })) : { contacts: [] }]);
  if (fresh && !fresh()) return;
  const w = can("cadastros.write");
  const opt = (arr, sel, lbl) => arr.map(x => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(lbl(x))}</option>`).join("");
  const pend = d.users.filter(u => u.role === "tecnico" && !u.active);
  body.innerHTML = `
  ${pend.length ? `<div class="card"><h4>Freelancers aguardando sua autorização (${pend.length})</h4>${pend.map(u => `<div class="row">${esc(u.name)} · ${esc(u.email)} · base ${esc(u.base_name || "—")}
    ${w ? `<button class="btn small primary" data-auth="${u.id}">Autorizar</button>` : ""}</div>`).join("")}</div>` : ""}
  <div class="card"><h4>Sede · administração central</h4>
    <p class="muted">Posição atual no mapa: ${d.sede.padrao ? badge("padrão — informe a sua sede", "warn") : `${esc(d.sede.city || "")} ${locBadge(d.sede)}`}</p>
    ${w ? `<form class="f" data-form="sede"><div class="row"><label>Nome<input name="name" value="${esc(d.sede.name || "Administração central")}"></label>
      <label>Cidade<input name="city" required value="${esc(d.sede.city || "")}"></label><label>UF<input name="state" maxlength="2" size="3" value="${esc(d.sede.state || "SP")}"></label>
      <label>Endereço (rua e número)<input name="address" value="${esc(d.sede.address || "")}"></label>
      <label>Coordenadas (opcional)<input name="coords" placeholder="-22.37, -46.94" value="${d.sede.geo_source === "informada manualmente" ? `${d.sede.lat}, ${d.sede.lng}` : ""}"></label>
      <button class="btn primary small">Salvar sede</button></div></form>` : ""}</div>
  <div class="card"><h4>Bases / filiais (${d.bases.length})</h4>
    <table class="t"><thead><tr><th>Nome</th><th>Cidade</th><th>Localização</th><th>Situação</th><th></th></tr></thead><tbody>${d.bases.map(b => `<tr><td>${esc(b.name)}</td><td>${esc(b.city)}/${esc(b.state || "")}</td>
      <td>${locBadge(b)}</td><td>${b.active ? "ativa" : badge("inativa", "warn")}</td>
      <td>${w ? `<button class="btn small" data-edit="base" data-id="${b.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w ? `<form class="f" data-form="base"><input type="hidden" name="id"><div class="row"><label>Nome<input name="name" required></label><label>Cidade<input name="city" required></label>
      <label>UF<input name="state" maxlength="2" value="SP" size="3"></label><label>Endereço<input name="address"></label>
      <label>Coordenadas (opcional)<input name="coords" placeholder="-23.55, -46.63"></label>
      <label class="row"><input type="checkbox" name="location_confirmed"> endereço confirmado</label><label class="row"><input type="checkbox" name="active" checked> ativa</label>
      <button class="btn primary small">Salvar base</button></div></form>` : ""}</div>
  <div class="card"><h4>Clientes (${d.clients.length})</h4>
    <table class="t"><thead><tr><th>Cliente</th><th>Segmento</th><th>Contrato/mês</th><th>Contato</th><th>Situação</th><th></th></tr></thead><tbody>${d.clients.map(c => `<tr><td>${esc(c.name)}</td><td>${esc(c.segment || "")}</td>
      <td>${c.contract_minor != null ? fmtMoney(c.contract_minor, "BRL") : "—"}</td><td>${esc(c.contact_name || "")}</td><td>${esc(c.status)}</td>
      <td>${w ? `<button class="btn small" data-edit="client" data-id="${c.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w ? `<form class="f" data-form="client"><input type="hidden" name="id"><div class="row"><label>Nome<input name="name" required></label><label>Razão social<input name="legal_name"></label>
      <label>Segmento<input name="segment" placeholder="ex.: clínica, escritório, escola"></label><label>Contrato mensal (R$)<input name="contract" inputmode="decimal" placeholder="1.500,00"></label>
      <label>Contato<input name="contact_name"></label><label class="row"><input type="checkbox" name="active" checked> ativo</label></div>
      <label>Observações<textarea name="notes" rows="2"></textarea></label><button class="btn primary small">Salvar cliente</button></form>` : ""}</div>
  <div class="card"><h4>Unidades dos clientes (${d.units.length})</h4>
    <table class="t"><thead><tr><th>Cliente</th><th>Unidade</th><th>Cidade</th><th>Atendida por</th><th>Localização</th><th></th></tr></thead><tbody>${d.units.map(u => `<tr><td>${esc(u.partner_name)}</td><td>${esc(u.name)}${u.active ? "" : " " + badge("inativa", "warn")}</td>
      <td>${esc(u.city || "")}/${esc(u.state || "")}</td><td>${esc(u.base_name || "—")}</td><td>${locBadge(u)}</td>
      <td>${w ? `<button class="btn small" data-edit="unit" data-id="${u.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w && d.clients.length ? `<form class="f" data-form="unit"><input type="hidden" name="id"><div class="row"><label>Cliente<select name="client_id">${opt(d.clients, null, c => c.name)}</select></label>
      <label>Nome da unidade<input name="name" required></label><label>Cidade<input name="city" required></label><label>UF<input name="state" maxlength="2" value="SP" size="3"></label>
      <label>Endereço<input name="address"></label><label>Coordenadas (opcional)<input name="coords" placeholder="-23.55, -46.63"></label><label>Base que atende<select name="base_id"><option value="">—</option>${opt(d.bases.filter(b => b.active), null, b => b.name)}</select></label>
      <label class="row"><input type="checkbox" name="location_confirmed"> endereço confirmado</label><label class="row"><input type="checkbox" name="active" checked> ativa</label>
      <button class="btn primary small">Salvar unidade</button></div></form>` : w ? `<p class="muted">Cadastre um cliente primeiro.</p>` : ""}</div>
  <div class="card"><h4>Contatos dos clientes · WhatsApp (${chs.contacts.length})</h4>
    <p class="muted">Mensagens que chegam desses números entram direto na conversa do cliente certo.</p>
    <table class="t"><thead><tr><th>Cliente</th><th>Nome</th><th>Telefone</th><th>Função</th></tr></thead><tbody>${chs.contacts.map(c => `<tr><td>${esc(c.partner_name)}</td><td>${esc(c.name)}</td><td class="mono">+${esc(c.phone)}</td><td>${esc(c.role || "")}</td></tr>`).join("")}</tbody></table>
    ${w && d.clients.length ? `<form class="f" data-form="contact"><div class="row"><label>Cliente<select name="partner_id">${opt(d.clients, null, c => c.name)}</select></label>
      <label>Nome<input name="name" required></label><label>Telefone (com DDD)<input name="phone" required placeholder="11 91234-5678"></label><label>Função<input name="role" placeholder="gestor, recepção…"></label>
      <button class="btn primary small">Salvar contato</button></div></form>` : ""}</div>
  <div class="card"><h4>Equipe e acessos (${d.users.length})</h4>
    <table class="t"><thead><tr><th>Nome</th><th>E-mail</th><th>Papel</th><th>Vínculo</th><th>Situação</th><th></th></tr></thead><tbody>${d.users.map(u => `<tr><td>${esc(u.name)}</td><td>${esc(u.email)}</td>
      <td>${esc(ROLE_LABEL[u.role] || u.role)}</td><td>${esc(u.partner_name || u.base_name || "central")}</td><td>${u.active ? "ativo" : badge(u.role === "tecnico" ? "aguardando autorização" : "inativo", "warn")}</td>
      <td>${w ? `<button class="btn small" data-edit="user" data-id="${u.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w ? `<form class="f" data-form="user"><input type="hidden" name="id"><div class="row"><label>Nome<input name="name" required></label><label>E-mail<input name="email" type="email" required></label>
      <label>Papel<select name="role">${d.roles.map(r => `<option value="${r}">${esc(ROLE_LABEL[r] || r)}</option>`).join("")}</select></label>
      <label>Cliente (p/ acesso de cliente)<select name="partner_id"><option value="">—</option>${opt(d.clients, null, c => c.name)}</select></label>
      <label>Base (p/ técnico)<select name="base_id"><option value="">—</option>${opt(d.bases, null, b => b.name)}</select></label>
      <label>Senha (mín. 10; em edição, só se for trocar)<input name="password" type="password" minlength="10" autocomplete="new-password"></label>
      <label class="row"><input type="checkbox" name="active" checked> ativo/autorizado</label><button class="btn primary small">Salvar acesso</button></div></form>` : ""}</div>`;
  const ROUTE = { base: "bases", client: "clientes", unit: "unidades", user: "usuarios", sede: "sede" };
  const ctf = body.querySelector("form[data-form=contact]"); if (ctf) { ctf.removeAttribute("data-form"); ctf.onsubmit = async e => { e.preventDefault(); const f = new FormData(ctf);
    await act(() => api("/api/contacts", { method: "POST", body: { partner_id: f.get("partner_id"), name: f.get("name"), phone: f.get("phone"), role: f.get("role") } }), "Contato salvo"); renderTables(); }; }
  const SRC = { base: d.bases, client: d.clients, unit: d.units, user: d.users };
  body.querySelectorAll("form[data-form]").forEach(f => f.onsubmit = async e => {
    e.preventDefault(); const k = f.dataset.form, fd = new FormData(f), data = {};
    for (const [n, v] of fd.entries()) if (n !== "id") data[n] = v;
    for (const cb of f.querySelectorAll("input[type=checkbox]")) data[cb.name] = cb.checked;
    if (k === "user") data.authorize_now = data.active;
    const id = fd.get("id");
    await act(() => api(`/api/cadastros/${ROUTE[k]}${id ? "/" + id : ""}`, { method: id ? "PUT" : "POST", body: data }), "Cadastro salvo");
    renderTables();
  });
  body.querySelectorAll("[data-edit]").forEach(bt => bt.onclick = () => {
    const k = bt.dataset.edit, x = SRC[k].find(r => r.id === bt.dataset.id), f = body.querySelector(`form[data-form=${k}]`); if (!x || !f) return;
    const val = { ...x, contract: x.contract_minor != null ? (x.contract_minor / 100).toFixed(2).replace(".", ",") : "", client_id: x.partner_id,
      location_confirmed: x.location_status === "confirmada", active: k === "client" ? x.status === "ativo" : !!x.active,
      coords: x.geo_source === "informada manualmente" && x.lat != null ? `${x.lat}, ${x.lng}` : "" };  // editar não apaga coordenadas informadas
    for (const el of f.elements) { if (!el.name || !(el.name in val) && el.name !== "id") continue;
      if (el.type === "checkbox") el.checked = !!val[el.name]; else if (el.name !== "password") el.value = val[el.name] ?? ""; }
    f.querySelector("[name=id]").value = x.id; f.scrollIntoView({ behavior: motion.reduced ? "auto" : "smooth", block: "center" }); f.querySelector("button").textContent = "Salvar alterações";
  });
  body.querySelectorAll("[data-auth]").forEach(bt => bt.onclick = async () => { const u = d.users.find(x => x.id === bt.dataset.auth);
    await act(() => api(`/api/cadastros/usuarios/${u.id}`, { method: "PUT", body: { name: u.name, email: u.email, role: u.role, base_id: u.base_id, active: true } }), "Freelancer autorizado");
    renderTables(); });
}

// ------------------------------------------------------------------ país e estado no mapa real
async function openCountry(name) {
  const c = ((S.geo && S.geo.countries) || []).find(x => x.name === name) || {};
  const reg = regionOfCountry(name), ps = (S.world.partners || []).filter(p => p.region_code === reg);
  const bases = reg === "BR" ? (S.world.service_bases || []) : [], whs = (S.world.warehouses || []).filter(w => w.region_code === reg);
  const r = (S.world.regions || []).find(x => x.code === reg);
  const head = `${reg ? `<dl class="kv"><dt>Região operacional</dt><dd>${esc(r ? r.name : reg)} · ${r && r.sales_enabled ? badge("vendas liberadas", "ok") : badge("vendas bloqueadas", "warn")}</dd>
      <dt>Moeda / fuso</dt><dd>${esc(r ? r.currency + " · " + r.timezone : "")}</dd></dl>` : `<p class="muted">Sem operação neste país.</p>`}
    <h4>Clientes e parceiros (${ps.length})</h4>${ps.map(p => `<div class="card click" data-p="${p.id}" style="cursor:pointer">${st(p.status.level)} <b>${esc(p.name)}</b>
      <div class="muted">${p.units.map(u => `${esc(u.name)} (${esc(u.city || "")}/${esc(u.state || "")})`).join(" · ")}</div></div>`).join("") || `<p class="muted">Nenhum.</p>`}
    ${bases.length ? `<h4>Bases / filiais (${bases.length})</h4>${bases.map(b => `<div class="card click" data-b="${b.id}" style="cursor:pointer">Base · ${esc(b.name)} <span class="muted">${esc(b.city || "")}/${esc(b.state || "")}</span></div>`).join("")}` : ""}
    ${whs.length ? `<h4>Armazéns</h4>${whs.map(w => `<div>${esc(w.name)} · ${esc(w.city || "")}</div>`).join("")}` : ""}`;
  let first = true;
  const render = sts => {
    // desenhos seguintes (estados chegando) só se o painel do país ainda estiver aberto — nunca cobre outro painel
    if (!first && (!S.panel || S.panel.title !== esc(c.name_pt || name))) return;
    first = false;
    const keep = document.querySelector("#expBox");   // 2º desenho (quando os estados chegam) mantém o formulário já aberto/preenchido
    const reuse = keep && keep.dataset.country === name && keep.childElementCount && !$("#panel").classList.contains("hidden");
    openPanel({ title: esc(c.name_pt || name), sub: badge("mapa real · Natural Earth"), html: head + `<div id="expBox"></div><h4>Estados / províncias</h4>${sts}`, reload: null });
    if (reuse) $("#expBox").replaceWith(keep); else if (can("mercados.read")) expandBox(name);
    $("#pBody").querySelectorAll("[data-p]").forEach(x => x.onclick = () => select("partner", x.dataset.p));
    $("#pBody").querySelectorAll("[data-b]").forEach(x => x.onclick = () => select("base", x.dataset.b, { data: S.world.service_bases.find(b => b.id === x.dataset.b) }));
    $("#pBody").querySelectorAll("[data-st]").forEach(x => x.onclick = () => { const s = S.w.states.states.find(z => z.name === x.dataset.st); select("state", s.name, { data: s, country: name }); });
  };
  render(`<p class="muted">Carregando…</p>`);
  const load = async () => {
    const d = await api(`/api/geo/states?country=${encodeURIComponent(name)}`).catch(() => null);
    if (!S.panel) return;
    if (!d || d.status !== "pronto") { render(`<p class="muted">${d && d.status === "erro" ? "Falha ao baixar estados: " + esc(d.error || "") : "Baixando o mapa de estados de todos os países (uma única vez, ~40 MB)…"}</p>`);
      if (!d || d.status !== "erro") setTimeout(load, 5000); return; }
    S.w && S.w.setStates(d);
    const units = ps.flatMap(p => p.units.map(u => ({ ...u, partner: p.name })));
    render(d.states.length ? `<div class="row" style="flex-wrap:wrap;gap:4px">${d.states.slice().sort((a, b) => a.name.localeCompare(b.name, "pt-BR")).map(s => {
      const n = units.filter(u => (u.state || "").toUpperCase() === (s.code || "").toUpperCase()).length;
      return `<button class="btn small" data-st="${esc(s.name)}">${esc(s.name)}${n ? ` · ${n}` : ""}</button>`; }).join("")}</div>` : `<p class="muted">Sem subdivisões no mapa para este país.</p>`);
  };
  load();
}
function openState(s, country) {
  if (!s) return;
  const reg = regionOfCountry(country), code = (s.code || "").toUpperCase();
  const ps = (S.world.partners || []).filter(p => p.region_code === reg);
  const units = ps.flatMap(p => p.units.filter(u => (u.state || "").toUpperCase() === code).map(u => ({ ...u, partner: p })));
  const bases = (S.world.service_bases || []).filter(b => (b.state || "").toUpperCase() === code && reg === "BR");
  openPanel({ title: esc(s.name), sub: `${esc(countryName(country))} ${s.type ? "· " + esc(s.type) : ""}`, html: `
    <h4>Unidades de clientes (${units.length})</h4>${units.map(u => `<div class="card click" data-p="${u.partner.id}" style="cursor:pointer">${st(u.partner.status.level)} <b>${esc(u.partner.name)}</b> · ${esc(u.name)} <span class="muted">${esc(u.city || "")}</span></div>`).join("") || `<p class="muted">Nenhuma.</p>`}
    <h4>Bases (${bases.length})</h4>${bases.map(b => `<div>Base · ${esc(b.name)} <span class="muted">${esc(b.city || "")}</span></div>`).join("") || `<p class="muted">Nenhuma.</p>`}` });
  $("#pBody").querySelectorAll("[data-p]").forEach(x => x.onclick = () => select("partner", x.dataset.p));
}

// ------------------------------------------------------------------ central de comandos
function openCommands() {
  openPanel({ title: "⌘ Central de comandos", sub: "Escreva em português. Nada é executado sem a sua confirmação.", html: `
    <form class="f" id="cmdForm"><input id="cmdIn" name="text" autocomplete="off" placeholder="ex.: chamado Clínica Vida unidade Matriz: impressora não imprime" style="font-size:15px">
      <button class="btn primary">Interpretar</button></form>
    <div id="cmdRes" class="cmdres"></div>
    <details class="card" open><summary>Exemplos</summary><ul>${["chamado <cliente> unidade <unidade> responsável <pessoa> prazo amanhã: <descrição>",
      "preventiva <cliente> unidade <unidade> prazo 15/12", "tarefa <cliente>: <o que fazer> prazo em 3 dias", "rodar agente pesquisa", "rodar ceo <cliente>",
      "ideia <cliente>: <ideia para o CEO>", "fechamento <cliente> 11/2026", "whatsapp <cliente>: <mensagem>", "abrir Brasil", "pausar agentes"]
      .map(x => `<li><a href="#" data-ex="${esc(x)}">${esc(x)}</a></li>`).join("")}</ul></details>
    <h4>Histórico desta sessão</h4><div id="cmdHist">${(S.cmdHist || []).map(h => `<div class="muted">${esc(h)}</div>`).join("") || `<p class="muted">Vazio.</p>`}</div>` });
  const inp = $("#cmdIn"); inp.focus();
  $("#pBody").querySelectorAll("[data-ex]").forEach(a => a.onclick = e => { e.preventDefault(); inp.value = a.dataset.ex; inp.focus(); });
  $("#cmdForm").onsubmit = async e => {
    e.preventDefault(); const text = inp.value.trim(); if (!text) return; const out = $("#cmdRes");
    out.innerHTML = `<p class="muted">Interpretando…</p>`;
    try {
      const p = await api("/api/commands/parse", { method: "POST", body: { text } });
      if (!p.requires_confirmation) { S.cmdHist = [`✓ ${p.summary}`, ...(S.cmdHist || [])].slice(0, 20); return runOpen(p.args.query); }
      out.innerHTML = `<div class="card"><h4>${esc(p.summary)}</h4><div class="muted">${p.source === "ia" ? "Interpretado pela IA grátis — confira antes de confirmar." : "Interpretado por regra."}</div>
        <div class="row" style="margin-top:6px"><button class="btn primary small" id="cmdOk">Confirmar e executar</button><button class="btn small" id="cmdNo">Cancelar</button></div></div>`;
      $("#cmdNo").onclick = () => (out.innerHTML = "");
      $("#cmdOk").onclick = async () => {
        $("#cmdOk").disabled = true;
        try { const r = await api("/api/commands/execute", { method: "POST", body: { action: p.action, args: p.args } });
          S.cmdHist = [`✓ ${p.summary}`, ...(S.cmdHist || [])].slice(0, 20); toast("Executado: " + p.summary);
          out.innerHTML = `<div class="card"><b>Feito.</b> ${r.result.task_id ? `<a href="#" id="cmdTask">Abrir tarefa</a>` : esc(JSON.stringify(r.result))}</div>`;
          const t = $("#cmdTask"); if (t) t.onclick = ev => { ev.preventDefault(); openTask(r.result.task_id); };
          inp.value = ""; refresh(); }
        catch (er) { S.cmdHist = [`✗ ${p.summary}: ${er.message}`, ...(S.cmdHist || [])].slice(0, 20); out.innerHTML = `<div class="card" style="border-color:#7f1d1d">${esc(er.message)}</div>`; }
      };
    } catch (er) { out.innerHTML = `<div class="card" style="border-color:#7f1d1d">${esc(er.message)}</div>`; }
  };
}
function runOpen(q) {
  const t = q.toLowerCase(); setView("3d");
  const p = (S.world.partners || []).find(x => x.name.toLowerCase().includes(t)); if (p) return select("partner", p.id);
  const c = ((S.geo && S.geo.countries) || []).find(x => x.name_pt.toLowerCase() === t || x.name.toLowerCase() === t); if (c) return select("country", c.name, {});
  const b = (S.world.service_bases || []).find(x => x.name.toLowerCase().includes(t)); if (b) return select("base", b.id, { data: b });
  toast(`Não encontrei “${q}”`, true);
}

// ------------------------------------------------------------------ quadro de tarefas (arrastar entre colunas)
async function renderKanban(body, fresh) {
  const tasks = await api("/api/tasks"); if (fresh && !fresh()) return; const q = ($("#tFilter").value || "").toLowerCase();
  const cols = ["a_fazer", "em_andamento", "aguardando_terceiro", "aguardando_aprovacao", "concluida", "cancelada"];
  const partners = Object.fromEntries((S.world.partners || []).map(p => [p.id, p.name]));
  const list = tasks.filter(t => !q || (t.title + " " + (t.assignee_name || "") + " " + (partners[t.partner_id] || "")).toLowerCase().includes(q));
  body.innerHTML = `<div class="row" style="padding-top:10px">${can("comandos.run") ? `<button class="btn small primary" id="kNew">+ Nova tarefa por comando</button>` : ""}
    <span class="muted">Arraste os cartões entre as colunas para mudar o status. Regras (checklist, dependências, aprovação) continuam valendo.</span></div>
    <div class="kanban">${cols.map(c => `<div class="kcol" data-col="${c}"><h4>${esc(TASK_STATES[c])} · ${list.filter(t => t.status === c).length}</h4>
      ${list.filter(t => t.status === c).slice(0, 60).map(t => `<div class="kcard" draggable="${can("tasks.write")}" data-id="${t.id}">
        <b>${esc(t.title)}</b>${t.overdue ? badge("atrasada", "bad") + " " : ""}${badge(t.priority)}
        <div class="muted">${esc(partners[t.partner_id] || "central")} · ${esc(t.assignee_name || "sem responsável")}${t.due_at ? " · " + fmtTime(t.due_at) : ""}</div></div>`).join("")}</div>`).join("")}</div>`;
  const kn = $("#kNew"); if (kn) kn.onclick = openCommands;
  body.querySelectorAll(".kcard").forEach(card => {
    card.onclick = () => openTask(card.dataset.id);
    card.ondragstart = e => { e.dataTransfer.setData("text/plain", card.dataset.id); };
  });
  body.querySelectorAll(".kcol").forEach(col => {
    col.ondragover = e => { e.preventDefault(); col.classList.add("over"); };
    col.ondragleave = () => col.classList.remove("over");
    col.ondrop = async e => { e.preventDefault(); col.classList.remove("over"); const id = e.dataTransfer.getData("text/plain");
      try { await api(`/api/tasks/${id}`, { method: "PATCH", body: { status: col.dataset.col } }); toast("Status atualizado"); }
      catch (er) { toast(er.message, true); } renderKanban(body); };
  });
}

// ------------------------------------------------------------------ atendimento (WhatsApp + canais internos)
async function updateUnread() {
  try { const cs = await api("/api/conversations"); const n = cs.reduce((a, c) => a + (c.unread || 0), 0);
    const el = $("#unread"); el.textContent = n; el.classList.toggle("hidden", !n); el.classList.toggle("ok", !!n); } catch {}
}
async function renderInbox(body, fresh) {
  const [ov, convs] = await Promise.all([api("/api/channels"), api("/api/conversations")]);
  if (fresh && !fresh()) return;
  const sel = S.convSel && convs.find(c => c.id === S.convSel) ? S.convSel : (convs[0] && convs[0].id);
  const wa = ov.whatsapp;
  body.innerHTML = `<div class="card" style="margin-top:10px"><b>WhatsApp Business</b> ${wa.configured ? badge("conectado à API oficial", "ok")
      : wa.demo_simulation ? badge("demonstração: envio simulado", "sim") : badge("não configurado", "warn")}
    ${wa.configured ? "" : `<span class="muted"> Faltam no config.local.env: ${wa.missing.map(esc).join(", ")}. Webhook: <span class="mono">${esc(wa.webhook_path)}</span> (ver docs).</span>`}
    ${wa.demo_simulation && can("atendimento.send") ? `<form class="row" id="simForm" style="margin-top:6px"><input name="phone" placeholder="telefone do cliente" class="btn" style="width:160px">
      <input name="text" placeholder="mensagem que o cliente mandaria" class="btn" style="flex:1"><button class="btn small">Simular mensagem recebida</button></form>` : ""}
    ${can("cadastros.write") ? `<form class="row" id="chForm" style="margin-top:6px"><input name="name" placeholder="novo canal interno (ex.: Equipe Base Centro)" class="btn" style="flex:1"><button class="btn small">Criar canal interno</button></form>` : ""}</div>
  <div class="inbox"><div class="list">${convs.map(c => `<button data-c="${c.id}" aria-current="${c.id === sel}">
      <b>${c.channel_kind === "interno" ? "# " : ""}${esc(c.contact_name || c.wa_id || "")}</b> ${c.unread ? badge(c.unread, "ok") : ""}${c.wa_id ? `<span class="muted mono"> +${esc(c.wa_id)}</span>` : ""}
      <div class="muted">${esc(c.partner_name || (c.channel_kind === "interno" ? "canal interno" : "cliente não identificado"))} · ${esc(c.status)}${c.channel_kind === "whatsapp" ? (c.window_open ? " · janela aberta" : " · fora da janela 24h") : ""}</div>
      <div class="muted">${c.last ? esc(c.last.preview) : ""}</div></button>`).join("") || `<p class="muted" style="padding:10px">Nenhuma conversa.</p>`}</div>
    <div class="chat" id="chat"><p class="muted" style="padding:12px">Selecione uma conversa.</p></div></div>`;
  body.querySelectorAll("[data-c]").forEach(b => b.onclick = () => { S.convSel = b.dataset.c; renderInbox(body); });
  const sf = $("#simForm"); if (sf) sf.onsubmit = async e => { e.preventDefault(); const f = new FormData(sf);
    await act(() => api("/api/demo/whatsapp-inbound", { method: "POST", body: { phone: f.get("phone"), text: f.get("text") } }), "Mensagem simulada recebida"); };
  const cf = $("#chForm"); if (cf) cf.onsubmit = async e => { e.preventDefault(); await act(() => api("/api/channels", { method: "POST", body: { name: new FormData(cf).get("name") } }), "Canal criado"); renderInbox(body); };
  if (sel) renderChat(sel);
}
async function renderChat(id) {
  const c = await api(`/api/conversations/${id}`); const box = $("#chat"); if (!box) return;
  const isWa = c.channel_kind === "whatsapp", send = can("atendimento.send");
  box.innerHTML = `<div style="padding:8px 12px;border-bottom:1px solid var(--line)"><b>${esc(c.contact_name || c.wa_id || c.channel_name)}</b>
      <span class="muted">${esc(c.partner_name || "")}${isWa ? ` · ${c.window_open ? "janela de 24h aberta até " + fmtTime(c.window_closes_at) : "fora da janela de 24h (só modelo aprovado)"}` : " · canal interno"}</span>
      ${send && isWa ? `<div class="row" style="margin-top:6px">
        ${!c.partner_id && can("cadastros.write") ? `<select id="lnkP" class="btn small">${(S.world.partners || []).map(p => `<option value="${p.id}">${esc(p.name)}</option>`).join("")}</select><button class="btn small" id="lnk">Vincular ao cliente</button>` : ""}
        ${c.partner_id ? `<select id="tkU" class="btn small"><option value="">unidade…</option>${c.units.map(u => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select><button class="btn small" id="tk">Abrir chamado</button>` : ""}
        <button class="btn small" id="sug">Sugerir resposta (IA grátis)</button>
        <button class="btn small" id="cls">${c.status === "encerrada" ? "Reabrir" : "Encerrar"}</button></div>` : ""}</div>
    <div class="msgs" id="msgs">${c.messages.map(m => `<div class="bubble ${m.direction}">${esc(m.body)}<small>${esc(m.author || "")} · ${fmtTime(m.created_at)}${m.direction === "out" ? " · " + esc(m.status) : ""}${m.is_simulated ? " · simulado" : ""}${m.error ? " · " + esc(m.error) : ""}</small></div>`).join("")}</div>
    ${send ? `<form id="sendForm"><textarea name="text" placeholder="${isWa && !c.window_open ? "Fora da janela de 24h: aguarde o cliente escrever ou use um modelo aprovado" : "Escreva a resposta…"}" ${isWa && !c.window_open ? "disabled" : ""}></textarea>
      <button class="btn primary" ${isWa && !c.window_open ? "disabled" : ""}>Enviar</button></form>` : `<p class="muted" style="padding:8px">Somente leitura.</p>`}`;
  const m = $("#msgs"); m.scrollTop = m.scrollHeight;
  const f = $("#sendForm"); if (f) f.onsubmit = async e => { e.preventDefault(); const t = f.text.value.trim(); if (!t) return;
    await act(() => api(`/api/conversations/${id}/send`, { method: "POST", body: { text: t } }), "Mensagem enviada"); updateUnread(); renderChat(id); };
  const sug = $("#sug"); if (sug) sug.onclick = async () => { sug.disabled = true; sug.textContent = "Pensando…";
    try { const r = await api(`/api/conversations/${id}/suggest`, { method: "POST" }); f.text.value = r.draft; toast("Rascunho pronto: revise antes de enviar"); }
    catch (er) { toast(er.message, true); } sug.disabled = false; sug.textContent = "Sugerir resposta (IA grátis)"; };
  const tk = $("#tk"); if (tk) tk.onclick = async () => { const r = await act(() => api(`/api/conversations/${id}/ticket`, { method: "POST", body: { unit_id: $("#tkU").value } }), "Chamado aberto"); renderChat(id); openTask(r.task_id); };
  const lnk = $("#lnk"); if (lnk) lnk.onclick = async () => { await act(() => api(`/api/conversations/${id}/link`, { method: "POST", body: { partner_id: $("#lnkP").value } }), "Conversa vinculada e contato cadastrado"); renderChat(id); };
  const cls = $("#cls"); if (cls) cls.onclick = async () => { await act(() => api(`/api/conversations/${id}/status`, { method: "POST", body: { status: c.status === "encerrada" ? "aberta" : "encerrada" } })); renderChat(id); };
}

// ------------------------------------------------------------------ busca e filtros
function setupSearch() {
  const inp = $("#q"), res = $("#qres"); let timer;
  $("#tFilter").oninput = () => { clearTimeout(timer); timer = setTimeout(() => renderTables(true), 250); };
  inp.oninput = () => { clearTimeout(timer); timer = setTimeout(async () => {
    const q = inp.value.trim().toLowerCase(); if (!q) { res.classList.add("hidden"); return; }
    const items = [];
    for (const c of (S.geo && S.geo.countries) || []) if ((c.name_pt + " " + c.name).toLowerCase().includes(q)) items.push(["country", c.name, `País · ${c.name_pt}`, c]);
    for (const st of (S.w && S.w.states ? S.w.states.states : [])) if (st.name.toLowerCase().includes(q)) items.push(["state", st.name, `Estado · ${st.name}`, st, S.w.states.country]);
    for (const p of S.world.partners) { if (p.name.toLowerCase().includes(q)) items.push(["partner", p.id, `Parceiro · ${p.name} · ${statusText(p.status.level)}`]);
      for (const u of p.units) if ((u.name + " " + (u.city || "") + " " + (u.state || "")).toLowerCase().includes(q)) items.push(["unit", u.id, `Unidade · ${u.name} (${u.city}/${u.state})`, { ...u, partner: p.name }]); }
    for (const mk of S.world.markets || []) if ((mk.country + " " + (mk.marca || "")).toLowerCase().includes(q)) items.push(["market", mk.id, `Mercado · ${mk.country} · ${mk.marca || ""}`]);
    for (const b of S.world.service_bases || []) { if ((b.name + " " + (b.city || "")).toLowerCase().includes(q)) items.push(["base", b.id, `Base · ${b.name} (${b.city || ""})`, b]);
      for (const f of b.freelancers || []) if (f.name.toLowerCase().includes(q)) items.push(["freelancer", f.id, `Freelancer · ${f.name}`, { ...f, base: b.name }]); }
    for (const a of S.world.agents) if ((a.name + a.role).toLowerCase().includes(q)) items.push(["agent", a.id, `Agente · ${a.name}`]);
    for (const w of S.world.warehouses) if ((w.name + w.city).toLowerCase().includes(q)) items.push(["warehouse", w.id, `Armazém · ${w.name}`]);
    if (q.length >= 3) { try { (await api(`/api/orders?q=${encodeURIComponent(q)}`)).slice(0, 5).forEach(o => items.push(["order", o.id, `Pedido · ${o.code} · ${ORDER_STATES[o.status]}`]));
      (await api(`/api/tasks?q=${encodeURIComponent(q)}`)).slice(0, 5).forEach(t => items.push(["task", t.id, `Tarefa · ${t.title}`])); } catch {} }
    res.innerHTML = items.slice(0, 14).map((it, i) => `<button data-i="${i}">${esc(it[2])}</button>`).join("") || `<div class="muted" style="padding:8px 12px">Nada encontrado</div>`;
    res.classList.remove("hidden");
    res.querySelectorAll("button").forEach(b => b.onclick = () => { const it = items[+b.dataset.i]; res.classList.add("hidden"); inp.value = "";
      if (S.view !== "3d" && !["order", "task"].includes(it[0])) setView("3d"); select(it[0], it[1], { data: it[3], country: it[4] }); });
  }, 200); };
  document.addEventListener("click", e => { if (!$("#search").contains(e.target)) res.classList.add("hidden"); });
}
function setupFilters() {
  const wrap = $("#filters");
  wrap.innerHTML = Object.keys(LEVEL_RGB).map(l => `<button class="chip" aria-pressed="true" data-l="${l}"><span class="st ${l}" style="border:0;padding:0"><i></i>${statusText(l)}</span></button>`).join("") +
    `<select class="chip" id="fRegion" aria-label="Filtrar região"><option value="">Todas as regiões</option><option value="BR">Brasil</option><option value="PT">Portugal</option><option value="US">EUA</option></select>`;
  wrap.querySelectorAll("[data-l]").forEach(b => b.onclick = () => { const on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", String(on));
    const levels = new Set([...wrap.querySelectorAll("[data-l]")].filter(x => x.getAttribute("aria-pressed") === "true").map(x => x.dataset.l)); S.w && S.w.setFilters({ levels }); });
  $("#fRegion").onchange = e => { S.w && S.w.setFilters({ region: e.target.value || null }); if (e.target.value && S.w) S.w.focus("region", e.target.value); };
}

boot();
