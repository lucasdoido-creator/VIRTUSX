// Expansão internacional pela tela + Sentinela.
import { chromium } from "playwright";
const BASE = process.argv[2] || "http://127.0.0.1:8000", OUT = process.argv[3] || "/tmp/shots";
const log = (...a) => console.log("•", ...a); const errors = [];
const b = await chromium.launch(); const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); const A = await ctx.newPage();
A.on("pageerror", e => errors.push(e.message));
await A.goto(BASE + "/"); await A.fill("#login form.f [name=email]", "admin@central.demo"); await A.fill("#login form.f [name=password]", "demo1234");
await A.click("#login form.f button.primary"); await A.waitForSelector("#live.live", { timeout: 20000 }); await A.waitForTimeout(1500);
const api = (path, opts = {}) => A.evaluate(async ([p, o]) => (await (await fetch(p, { ...o, headers: { "X-Requested-With": "plataforma", "Content-Type": "application/json" } })).json()), [path, opts]);
// 1. país no mapa → expandir
await A.fill("#q", "Portugal"); await A.click("#qres button:has-text('País · Portugal')");
await A.waitForSelector("#expBox summary", { timeout: 15000 }); await A.click("#expBox summary");
if ((await A.inputValue("#mktForm [name=lang]")) !== "pt-PT" || (await A.inputValue("#mktForm [name=currency]")) !== "EUR") throw new Error("idioma/moeda não vieram do catálogo");
await A.fill("#mktForm [name=brand]", "VirtusX TI"); await A.fill("#mktForm [name=offer]", "Suporte de TI remoto e manutenção preventiva para pequenas empresas");
await A.fill("#mktForm [name=email]", "contato@virtusx.example"); await A.fill("#mktForm [name=price]", "39");
await A.click("#mktForm button.primary"); await A.waitForSelector("#pTitle:has-text('🌐 Portugal')", { timeout: 30000 });
log("mercado criado pela tela: Portugal · pt-PT · EUR");
// 2. CEO e agentes trabalham (aqui disparados direto, como o agendador faria)
const mk = (await api("/api/mercados")).data.find(m => m.country === "Portugal");
await api(`/api/agents/mkt_${mk.id}/run`, { method: "POST" });
for (const a of ["juridico", "localizacao", "marketing", "vendas", "financeiro"]) await api(`/api/agents/${a}/run`, { method: "POST" });
await A.waitForTimeout(800); await A.click("#mkRun").catch(() => {}); await A.waitForTimeout(1500);
const m = (await api(`/api/mercados/${mk.id}`)).data;
if (m.progresso.feitas < 5) throw new Error("setores não entregues: " + JSON.stringify(m.progresso));
await A.waitForSelector("#pBody h4:has-text('Jurídico')", { timeout: 15000 });
await A.screenshot({ path: `${OUT}/x1_mercado.png` });
log(`CEO dividiu e agentes entregaram ${m.progresso.feitas}/${m.progresso.total} setores`);
// 3. prévia (não publicada) → aprovar → publicada
const pv = await A.evaluate(async s => (await fetch(s)).text(), `/m/${m.slug}`);
if (!pv.includes("PRÉVIA") || !pv.includes('lang="pt-PT"')) throw new Error("prévia do site incorreta");
const anon = await (await fetch(`${BASE}/m/${m.slug}`)).status; if (anon !== 404) throw new Error("site visível antes da aprovação");
await api(`/api/approvals/${m.publicacao.id}/decide`, { method: "POST", body: JSON.stringify({ approve: true }) });
const pub = await fetch(`${BASE}/m/${m.slug}`); const html = await pub.text();
if (pub.status !== 200 || !html.includes('hreflang="pt-PT"') || html.includes("noindex")) throw new Error("site publicado incorreto");
if (!(await (await fetch(`${BASE}/sitemap.xml`)).text()).includes(m.slug)) throw new Error("sitemap sem o país");
const vis = await b.newContext({ viewport: { width: 1280, height: 900 } }); const P = await vis.newPage();   // visitante (sem login)
await P.goto(`${BASE}/m/${m.slug}?utm_source=google&utm_campaign=lancamento`); await P.screenshot({ path: `${OUT}/x2_site_portugal.png`, fullPage: true });
await P.fill("[name=nome]", "Rita Sousa"); await P.fill("[name=email]", "rita@exemplo.pt"); await P.fill("[name=mensagem]", "Preciso de suporte para 5 computadores");
await P.check("[name=consentimento]"); await P.click("#orcamento button[type=submit]");
await P.waitForSelector(".note.ok", { timeout: 10000 }); await vis.close();
const leads = (await api(`/api/leads?market_id=${m.id}`)).data;
if (leads.length !== 1 || leads[0].utm_source !== "google") throw new Error("lead não captado com origem: " + JSON.stringify(leads));
log("visitante pediu orçamento no site: lead captado com origem google/lancamento e tarefa criada");
log("site do país: prévia só para a equipe; publicado após aprovação, com hreflang e sitemap");
await A.click("#closePanel").catch(() => {}); await A.fill("#q", "Portugal"); await A.click("#qres button:has-text('Mercado · Portugal')");
await A.waitForSelector("#mkLeads td:has-text('Rita Sousa')", { timeout: 15000 });
await A.selectOption(`#mkLeads [data-lead="${leads[0].id}"]`, "ganho"); await A.waitForTimeout(1500);
const f = (await api(`/api/mercados/${m.id}`)).data.funil;
if (f.ganhos !== 1 || f.visitas < 1) throw new Error("funil incorreto: " + JSON.stringify(f));
await A.screenshot({ path: `${OUT}/x4_funil.png` });
log(`funil: ${f.visitas} visita(s) → ${f.leads} lead(s) → ${f.ganhos} ganho(s)`);
// 4. torre no mapa e Sentinela
await A.click("#closePanel"); await A.fill("#q", "Portugal"); await A.click("#qres button:has-text('Mercado · Portugal')");
await A.waitForSelector("#pTitle:has-text('Portugal')"); await A.click("#closePanel");
await A.click("#saudeBtn"); await A.waitForSelector("#pTitle:has-text('Saúde do sistema')", { timeout: 15000 });
await A.waitForSelector("#sentRun", { state: "visible", timeout: 30000 }).catch(async e => {
  console.log("diag:", await A.evaluate(() => JSON.stringify({ painel: document.querySelector("#panel").className, titulo: document.querySelector("#pTitle").textContent,
    sent: !!document.querySelector("#sentRun"), box: (document.querySelector("#sentBox") || {}).textContent?.slice(0, 120) })));
  await A.screenshot({ path: `${OUT}/x_falha.png` }); throw e; });
await A.waitForFunction(() => document.querySelector("#panel").style.transform === "translateX(0%)", null, { timeout: 10000 });   // painel terminou de abrir
await A.locator("#sentRun").evaluate(el => { el.scrollIntoView({ block: "center" }); el.click(); });
await A.waitForFunction(() => /achado/.test(document.querySelector("#sentBox")?.textContent || ""), null, { timeout: 30000 }); await A.waitForSelector("#sentBox", { timeout: 15000 });
await A.screenshot({ path: `${OUT}/x3_sentinela.png` });
log("Sentinela:", ((await A.textContent("#sentBox")) || "").replace(/\s+/g, " ").slice(0, 120));
const vig = (await api("/api/saude")).data.client_errors.filter(e => !e.resolved);
await b.close();
if (vig.length) { console.log("VIGIA:", JSON.stringify(vig.slice(0, 3))); process.exit(1); }
if (errors.length) { console.log("ERROS:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E EXPANSÃO + SENTINELA OK");
