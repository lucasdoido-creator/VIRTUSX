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
    S.w = new World(S.renderer, $("#labels"), { onSelect: select });
    S.w.build(S.world);
    if (S.world.view === "parceiro" && S.world.partners[0]) S.w.focus("partner", S.world.partners[0].id); else S.w.focus("global");
  } catch (e) {
    $("#stage").innerHTML = `<div class="card" style="margin:60px auto;max-width:520px">O modo 3D não está disponível neste dispositivo (${esc(e.message)}). Todas as informações estão no modo tabelas.</div>`;
    setView("tabelas");
  }
  if (S.lite || innerWidth < 760) $("#legend").classList.add("hidden");
  renderCrumbs([["Global", () => { S.w && S.w.exitBuilding(); S.w && S.w.focus("global"); closePanel(); }]]);
  updateHeader();
  S.live = new Live({ onEvent, onState: liveState, onResync: () => { toast("Conexão restabelecida — dados sincronizados"); refresh(); } });
  S.live.start(S.world.last_event_id);
}

// Técnico/freelancer: sem mundo 3D nem dados de clientes; só as próprias tarefas, com checklist e evidências.
function startTecnico() {
  S.world = { partners: [], agents: [], regions: [], warehouses: [], sectors: [], service_bases: [] };
  $("#viewSeg").classList.add("hidden"); $("#search").classList.add("hidden"); $("#live").classList.add("hidden");
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
  $("#ticker").innerHTML = `<span>Últimos eventos:</span>` + S.ticker.slice(0, 5).map(e =>
    `<span title="ocorrido ${fmtTime(e.occurred_at)} · recebido ${fmtTime(e.received_at)}"><b>${esc(e.type)}</b> ${esc(e.status)} · ${new Date(e.received_at).toLocaleTimeString("pt-BR")}</span>`).join("");
  if (S.w) S.w.pulseFor(ev);
  if (!refreshTimer) refreshTimer = setTimeout(() => { refreshTimer = null; refresh(); }, 800); // agrupa rajadas de eventos
}
function liveState(s, lostAt) {
  const p = $("#live"); p.className = "pill " + ({ ao_vivo: "live", reconectando: "late", atrasado: "late", desconectado: "off" }[s] || "");
  const since = lostAt ? ` · dados de ${lostAt.toLocaleTimeString("pt-BR")}` : "";
  p.querySelector("span").textContent = { conectando: "Conectando…", ao_vivo: "Ao vivo", reconectando: "Reconectando" + since,
    atrasado: "Dados atrasados" + since, desconectado: "Sem conexão" + since }[s];
  document.body.classList.toggle("stale", s !== "ao_vivo");
}
async function refresh() {
  try {
    if (S.me.role === "tecnico") {  // técnico não tem visão do mundo: só recarrega a tarefa e a lista
      if (S.panel && S.panel.reload) S.panel.reload();
      return renderTables(true);
    }
    S.world = await api("/api/world"); updateHeader();
    if (S.w) S.w.build(S.world);
    if (S.panel && S.panel.reload) S.panel.reload();
    if (S.view === "tabelas") renderTables(true);
  } catch (e) { if (e.status === 401) location.reload(); }
}

// ------------------------------------------------------------------ navegação
function renderCrumbs(items) {
  $("#crumbs").innerHTML = ""; items.forEach(([t, fn], i) => { const b = document.createElement("button"); b.textContent = (i ? "› " : "") + t; b.onclick = fn; $("#crumbs").appendChild(b); });
}
function select(kind, id, pick) {
  const W = S.w; const p = (S.world.partners || []).find(x => x.id === id);
  if (kind === "partner" || kind === "status") {
    if (W) { W.focus("partner", id); W.enterBuilding(id); }
    const reg = p && S.world.regions.find(r => r.code === p.region_code);
    renderCrumbs([["Global", () => { W && W.exitBuilding(); W && W.focus("global"); closePanel(); }],
      [reg ? reg.name : "Região", () => select("region", p.region_code)], [p ? p.name : id, () => select("partner", id)]]);
    return openBuilding(id, kind === "status" ? "status" : "recepcao");
  }
  if (kind === "floor") { if (W) W.highlightFloor(id); return openBuilding(pick.partner || S.panel?.partner, id); }
  if (W) W.exitBuilding();
  if (kind === "region") { W && W.focus("region", id); const reg = S.world.regions.find(r => r.code === id);
    renderCrumbs([["Global", () => { W && W.focus("global"); closePanel(); }], [reg.name, () => select("region", id)]]); return openRegion(id); }
  if (W) W.focus(kind, id);
  if (kind === "hq") return openHQ();
  if (kind === "sector") return openSector(id);
  if (kind === "warehouse") return openWarehouse(id);
  if (kind === "agent") return openAgent(id);
  if (kind === "unit") return openUnit(pick.data);
  if (kind === "base") return openBase(pick.data || S.world.service_bases.find(b => b.id === id));
  if (kind === "order") return openOrder(id);
  if (kind === "task") return openTask(id);
}

// ------------------------------------------------------------------ painel lateral
function openPanel({ title, sub, html, tabs, tab, onTab, reload, partner }) {
  const el = $("#panel"); const wasOpen = !!S.panel;
  S.panel = { reload, partner };
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
  const [b, ceoData] = await Promise.all([api(`/api/partners/${pid}/building`), can("ventures.read") ? api(`/api/partners/${pid}/ceo`).catch(() => null) : null]);
  b.ceo = ceoData; const p = b.partner;
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
  const a = (await api("/api/agents")).find(x => x.id === id); const runs = await api(`/api/agents/${id}/runs`); const c = a.config;
  openPanel({ title: esc(a.name), sub: `${esc(a.role)} · ${badge(AGENT_LABEL[a.status] || a.status)}`, reload: () => openAgent(id), html: `
    <dl class="kv"><dt>Status</dt><dd>${esc(AGENT_LABEL[a.status] || a.status)} desde ${fmtTime(a.status_since)}</dd>
    <dt>Tarefa atual</dt><dd>${a.current_task ? esc(a.current_task.title) : "—"}</dd><dt>Última atualização confirmada</dt><dd>${fmtTime(a.last_confirmed_at)}</dd>
    <dt>Próxima execução</dt><dd>${fmtTime(a.next_run_at)}</dd><dt>Custo no mês</dt><dd>${fmtMoney(a.month_spend_minor, a.budget_currency)} de ${fmtMoney(a.budget_month_minor, a.budget_currency)} <span class="muted">(rotinas determinísticas não consomem IA)</span></dd></dl>
    ${can("agents.run") && a.enabled ? `<div class="row" style="margin:8px 0"><button class="btn primary small" id="runNow">Executar agora</button></div>` : ""}
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
    <h4>Comentários</h4>${t.comments.map(c => `<div class="card"><div class="muted">${esc(c.author)} · ${fmtTime(c.created_at)}</div><div style="white-space:pre-wrap">${esc(c.body)}</div></div>`).join("") || `<p class="muted">Nenhum.</p>`}
    <h4>Histórico</h4><ul class="timeline">${t.history.map(h => `<li>${esc(h.field)}: ${esc(h.old ?? "")} → ${esc(h.new ?? "")}<div class="muted">${esc(h.actor)} · ${fmtTime(h.at)}</div></li>`).join("")}</ul>` });
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
    ${can("cadastros.read") ? `<button class="btn small" id="goCad">Abrir cadastros</button>` : ""}` });
  $("#pBody").querySelectorAll("[data-p]").forEach(x => x.onclick = () => select("partner", x.dataset.p));
  const g = $("#goCad"); if (g) g.onclick = () => { closePanel(); S.tab = "cadastros"; setView("tabelas"); };
}

function openUnit(u) {
  openPanel({ title: esc(u.name), sub: esc(u.partner), html: `<dl class="kv"><dt>Cidade/estado</dt><dd>${esc(u.city || "—")}/${esc(u.state || "—")} · ${esc(u.country)}</dd>
    <dt>Localização</dt><dd>${u.location_status === "confirmada" ? badge("confirmada", "ok") + " " + esc(u.location_source || "") : badge("não confirmada", "warn") + " A posição no ambiente 3D é apenas ilustrativa."}</dd></dl>` });
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
const TABLE_TABS = [["cadastros", "Cadastros"], ["visao", "Visão geral"], ["pedidos", "Pedidos"], ["tarefas", "Tarefas e pendências"], ["agentes", "Agentes"], ["fechamentos", "Fechamentos"],
  ["aprovacoes", "Aprovações"], ["estoque", "Estoque"], ["eventos", "Eventos"], ["webhooks", "Webhooks"], ["auditoria", "Auditoria"], ["politicas", "Políticas"]];
async function renderTables(soft) {
  const tabs = S.me.role === "tecnico" ? [["tarefas", "Minhas tarefas"]] :
    TABLE_TABS.filter(([k]) => (S.me.is_central && (k !== "cadastros" || can("cadastros.read"))) || ["visao", "pedidos", "tarefas", "agentes", "fechamentos", "eventos"].includes(k));
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
    if (S.tab === "cadastros") return renderCadastros(body);
    if (S.tab === "visao") grid([["name", "Parceiro"], ["region", "Região"], ["status", "Sinalização", r => st(r.status)], ["model", "Modelo"], ["overdue_tasks", "Tarefas atrasadas"],
      ["pending_closings", "Fechamentos pendentes"], ["failed_integrations", "Integrações com falha"], ["sales", "Vendas confirmadas"], ["is_demo", "Ambiente", r => r.is_demo ? badge("demo", "sim") : ""]],
      await api("/api/overview"), r => select("partner", r.id));
    if (S.tab === "pedidos") grid([["code", "Código", r => `<span class="mono">${esc(r.code)}</span>`], ["status", "Status", r => esc(ORDER_STATES[r.status] || r.status)], ["partner_id", "Parceiro"],
      ["region_code", "Região"], ["total_minor", "Total", r => fmtMoney(r.total_minor, r.currency)], ["customer_name", "Cliente"], ["created_at", "Criado", r => fmtTime(r.created_at)]],
      await api("/api/orders"), r => openOrder(r.id));
    if (S.tab === "tarefas") grid([["title", "Título", r => esc(r.title) + (r.overdue ? " " + badge("atrasada", "bad") : "")], ["kind", "Tipo"], ["status", "Status", r => esc(TASK_STATES[r.status])],
      ["priority", "Prioridade"], ["partner_id", "Cliente/parceiro"], ["assignee_name", "Responsável"], ["due_at", "Prazo", r => fmtTime(r.due_at)]], await api("/api/tasks"), r => openTask(r.id));
    if (S.tab === "agentes") grid([["name", "Agente"], ["role", "Função"], ["status", "Status", r => badge(AGENT_LABEL[r.status] || r.status)], ["status_since", "Desde", r => fmtTime(r.status_since)],
      ["current_task", "Tarefa atual", r => esc(r.current_task ? r.current_task.title : "—")], ["last_confirmed_at", "Última confirmação", r => fmtTime(r.last_confirmed_at)],
      ["last_run", "Último resultado", r => esc(r.last_run ? r.last_run.summary : "—")], ["month_spend_minor", "Custo mês", r => fmtMoney(r.month_spend_minor, r.budget_currency)]],
      await api("/api/agents"), r => openAgent(r.id));
    if (S.tab === "fechamentos") { const rows = await api("/api/closings"); body.innerHTML = rows.map(closingCard).join("") || `<p class="muted">Nenhum.</p>`; bindClosingActions(body); }
    if (S.tab === "aprovacoes") { const rows = await api("/api/approvals"); body.innerHTML = approvalsHtml(rows) + `<h4>Histórico</h4>`;
      const hist = document.createElement("div"); body.appendChild(hist); bindApprovals(body);
      hist.innerHTML = `<table class="t"><tbody>${rows.filter(r => r.status !== "aguardando_aprovacao").map(r => `<tr><td>${esc(r.action)}</td><td>${badge(r.status)}</td><td>${esc(r.decided_by || "")}</td><td>${fmtTime(r.created_at)}</td><td class="mono">${esc(JSON.stringify(r.effects || ""))}</td></tr>`).join("")}</tbody></table>`; }
    if (S.tab === "estoque") grid([["region_code", "Região"], ["warehouse", "Armazém"], ["sku", "SKU"], ["product", "Produto"], ["on_hand", "Saldo"], ["reserved", "Reservado"],
      ["available", "Disponível", r => r.available < r.reorder_point ? `<b style="color:var(--warn)">${r.available}</b>` : r.available], ["reorder_point", "Ponto"]], await api("/api/inventory"));
    if (S.tab === "eventos") grid([["id", "#"], ["type", "Tipo"], ["status", "Status"], ["source", "Origem"], ["entity_type", "Entidade", r => `${esc(r.entity_type || "")} <span class="mono">${esc(r.entity_id || "")}</span>`],
      ["correlation_id", "Correlação", r => `<span class="mono">${esc(r.correlation_id || "")}</span>`], ["occurred_at", "Ocorrido", r => fmtTime(r.occurred_at)], ["received_at", "Recebido", r => fmtTime(r.received_at)]],
      (await api("/api/events?after=0&limit=1000")).events.reverse());
    if (S.tab === "webhooks") grid([["event_type", "Evento"], ["status", "Status"], ["attempts", "Tentativas"], ["occurred_at", "Ocorrido", r => fmtTime(r.occurred_at)], ["received_at", "Recebido", r => fmtTime(r.received_at)],
      ["last_error", "Erro"], ["id", "Ações", r => `${r.status === "dead_letter" ? `<button class="btn small" data-rq="${r.id}">Reprocessar</button>` : ""}${S.me.environment === "demo" ? ` <button class="btn small" data-rp="${r.id}" title="Reenvia o mesmo evento para testar duplicidade">Reenviar (teste)</button>` : ""}`]],
      await api("/api/webhooks/inbox"));
    if (S.tab === "auditoria") grid([["id", "#"], ["at", "Quando", r => fmtTime(r.at)], ["actor_type", "Ator", r => `${esc(r.actor_type)}:${esc(r.actor_id)}`], ["action", "Ação"],
      ["entity_type", "Entidade", r => `${esc(r.entity_type || "")} <span class="mono">${esc(r.entity_id || "")}</span>`], ["after_json", "Depois", r => `<span class="mono">${esc((r.after_json || "").slice(0, 120))}</span>`]], await api("/api/audit"));
    if (S.tab === "politicas") { const d = await api("/api/policies");
      body.innerHTML = `<p class="muted">Sem política cadastrada, a ação é preparada para revisão — nunca executada.</p><table class="t"><thead><tr><th>Ação</th><th>Modo</th><th>Limite</th><th>Aprovador</th><th>Descrição</th></tr></thead><tbody>
      ${d.policies.map(p => `<tr><td class="mono">${esc(p.action)}</td><td>${badge(p.mode)}</td><td>${p.limit_minor != null ? fmtMoney(p.limit_minor, p.currency) : "—"}</td><td>${esc(p.approver_role || "—")}</td><td>${esc(p.description || "")}</td></tr>`).join("")}
      ${d.actions_without_policy.map(a => `<tr><td class="mono">${esc(a.action)}</td><td>${badge("sem política → revisão", "warn")}</td><td>—</td><td>—</td><td>${esc(a.description)}</td></tr>`).join("")}</tbody></table>`; }
    body.querySelectorAll("[data-rq]").forEach(b => b.onclick = e => { e.stopPropagation(); act(() => api(`/api/webhooks/inbox/${b.dataset.rq}/requeue`, { method: "POST" }), "Reprocessado"); });
    body.querySelectorAll("[data-rp]").forEach(b => b.onclick = async e => { e.stopPropagation(); const r = await act(() => api("/api/demo/replay-webhook", { method: "POST", body: { inbox_id: b.dataset.rp } })); toast("Resultado: " + r.status); });
  } catch (e) { body.innerHTML = `<p class="muted">${esc(e.message)}</p>`; }
}

// ------------------------------------------------------------------ cadastros (operação real)
const ROLE_LABEL = { admin_central: "Administrador", operacoes_central: "Operações", financeiro_central: "Financeiro",
  parceiro_admin: "Cliente (gestor)", parceiro_operador: "Cliente (operador)", tecnico: "Técnico / freelancer" };
async function renderCadastros(body) {
  const d = await api("/api/cadastros"); const w = can("cadastros.write");
  const opt = (arr, sel, lbl) => arr.map(x => `<option value="${x.id}" ${x.id === sel ? "selected" : ""}>${esc(lbl(x))}</option>`).join("");
  const pend = d.users.filter(u => u.role === "tecnico" && !u.active);
  body.innerHTML = `
  ${pend.length ? `<div class="card"><h4>Freelancers aguardando sua autorização (${pend.length})</h4>${pend.map(u => `<div class="row">${esc(u.name)} · ${esc(u.email)} · base ${esc(u.base_name || "—")}
    ${w ? `<button class="btn small primary" data-auth="${u.id}">Autorizar</button>` : ""}</div>`).join("")}</div>` : ""}
  <div class="card"><h4>Bases / filiais (${d.bases.length})</h4>
    <table class="t"><thead><tr><th>Nome</th><th>Cidade</th><th>Localização</th><th>Situação</th><th></th></tr></thead><tbody>${d.bases.map(b => `<tr><td>${esc(b.name)}</td><td>${esc(b.city)}/${esc(b.state || "")}</td>
      <td>${b.location_status === "confirmada" ? badge("confirmada", "ok") : badge("não confirmada", "warn")}</td><td>${b.active ? "ativa" : badge("inativa", "warn")}</td>
      <td>${w ? `<button class="btn small" data-edit="base" data-id="${b.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w ? `<form class="f" data-form="base"><input type="hidden" name="id"><div class="row"><label>Nome<input name="name" required></label><label>Cidade<input name="city" required></label>
      <label>UF<input name="state" maxlength="2" value="SP" size="3"></label><label>Endereço<input name="address"></label>
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
      <td>${esc(u.city || "")}/${esc(u.state || "")}</td><td>${esc(u.base_name || "—")}</td><td>${u.location_status === "confirmada" ? badge("confirmada", "ok") : badge("não confirmada", "warn")}</td>
      <td>${w ? `<button class="btn small" data-edit="unit" data-id="${u.id}">Editar</button>` : ""}</td></tr>`).join("")}</tbody></table>
    ${w && d.clients.length ? `<form class="f" data-form="unit"><input type="hidden" name="id"><div class="row"><label>Cliente<select name="client_id">${opt(d.clients, null, c => c.name)}</select></label>
      <label>Nome da unidade<input name="name" required></label><label>Cidade<input name="city" required></label><label>UF<input name="state" maxlength="2" value="SP" size="3"></label>
      <label>Endereço<input name="address"></label><label>Base que atende<select name="base_id"><option value="">—</option>${opt(d.bases.filter(b => b.active), null, b => b.name)}</select></label>
      <label class="row"><input type="checkbox" name="location_confirmed"> endereço confirmado</label><label class="row"><input type="checkbox" name="active" checked> ativa</label>
      <button class="btn primary small">Salvar unidade</button></div></form>` : w ? `<p class="muted">Cadastre um cliente primeiro.</p>` : ""}</div>
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
  const ROUTE = { base: "bases", client: "clientes", unit: "unidades", user: "usuarios" };
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
      location_confirmed: x.location_status === "confirmada", active: k === "client" ? x.status === "ativo" : !!x.active };
    for (const el of f.elements) { if (!el.name || !(el.name in val) && el.name !== "id") continue;
      if (el.type === "checkbox") el.checked = !!val[el.name]; else if (el.name !== "password") el.value = val[el.name] ?? ""; }
    f.querySelector("[name=id]").value = x.id; f.scrollIntoView({ behavior: motion.reduced ? "auto" : "smooth", block: "center" }); f.querySelector("button").textContent = "Salvar alterações";
  });
  body.querySelectorAll("[data-auth]").forEach(bt => bt.onclick = async () => { const u = d.users.find(x => x.id === bt.dataset.auth);
    await act(() => api(`/api/cadastros/usuarios/${u.id}`, { method: "PUT", body: { name: u.name, email: u.email, role: u.role, base_id: u.base_id, active: true } }), "Freelancer autorizado");
    renderTables(); });
}

// ------------------------------------------------------------------ busca e filtros
function setupSearch() {
  const inp = $("#q"), res = $("#qres"); let timer;
  $("#tFilter").oninput = () => { clearTimeout(timer); timer = setTimeout(() => renderTables(true), 250); };
  inp.oninput = () => { clearTimeout(timer); timer = setTimeout(async () => {
    const q = inp.value.trim().toLowerCase(); if (!q) { res.classList.add("hidden"); return; }
    const items = [];
    for (const r of S.world.regions) if (r.name.toLowerCase().includes(q)) items.push(["region", r.code, `País · ${r.name}`]);
    for (const p of S.world.partners) { if (p.name.toLowerCase().includes(q)) items.push(["partner", p.id, `Parceiro · ${p.name} · ${statusText(p.status.level)}`]);
      for (const u of p.units) if ((u.name + " " + (u.city || "") + " " + (u.state || "")).toLowerCase().includes(q)) items.push(["unit", u.id, `Unidade · ${u.name} (${u.city}/${u.state})`, { ...u, partner: p.name }]); }
    for (const a of S.world.agents) if ((a.name + a.role).toLowerCase().includes(q)) items.push(["agent", a.id, `Agente · ${a.name}`]);
    for (const w of S.world.warehouses) if ((w.name + w.city).toLowerCase().includes(q)) items.push(["warehouse", w.id, `Armazém · ${w.name}`]);
    if (q.length >= 3) { try { (await api(`/api/orders?q=${encodeURIComponent(q)}`)).slice(0, 5).forEach(o => items.push(["order", o.id, `Pedido · ${o.code} · ${ORDER_STATES[o.status]}`]));
      (await api(`/api/tasks?q=${encodeURIComponent(q)}`)).slice(0, 5).forEach(t => items.push(["task", t.id, `Tarefa · ${t.title}`])); } catch {} }
    res.innerHTML = items.slice(0, 14).map((it, i) => `<button data-i="${i}">${esc(it[2])}</button>`).join("") || `<div class="muted" style="padding:8px 12px">Nada encontrado</div>`;
    res.classList.remove("hidden");
    res.querySelectorAll("button").forEach(b => b.onclick = () => { const it = items[+b.dataset.i]; res.classList.add("hidden"); inp.value = "";
      if (S.view !== "3d" && !["order", "task"].includes(it[0])) setView("3d"); select(it[0], it[1], { data: it[3] }); });
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
