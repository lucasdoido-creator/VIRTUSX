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
// 3b. vitrine com foto + layout, pela tela
const fs = await import("node:fs"); const png = "/tmp/e2e_item.png";
fs.writeFileSync(png, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64"));
await A.waitForSelector("#mkItem", { timeout: 15000 });
await A.fill("#mkItem [name=title]", "Pacote Manutenção"); await A.fill("#mkItem [name=price]", "a partir de 39 €");
await A.setInputFiles("#mkItem [name=photo]", png); await A.click("#mkItem button.primary");
await A.waitForSelector("#mkSite td:has-text('Pacote Manutenção') >> xpath=.. >> img", { timeout: 15000 });
await A.selectOption("#mkLay", "vitrine"); await A.waitForTimeout(1200);
const vh = await (await fetch(`${BASE}/m/${m.slug}`)).text();
if (!vh.includes('class="lay-vitrine"') || !vh.includes("Pacote Manutenção") || !/\/img\/[0-9a-f]{24}\.png/.test(vh)) throw new Error("vitrine não publicada no site");
const im = vh.match(/\/m\/[^"]+\/img\/[0-9a-f]{24}\.png/)[0]; if ((await fetch(BASE + im)).status !== 200) throw new Error("foto da vitrine não serve");
await A.screenshot({ path: `${OUT}/x5_vitrine.png` });
log("vitrine: item com foto e layout 'vitrine' publicados no site");
// 3c. financeiro manual e estoque da operação, pela tela
await A.click("#closePanel").catch(() => {});
await A.click("#viewSeg button[data-view=tabelas]"); await A.click("#tTabs button:has-text('Financeiro')");
await A.waitForSelector("#fnForm", { timeout: 15000 });
await A.selectOption("#fnForm [name=kind]", "receita"); await A.selectOption("#fnForm [name=category]", "mensalidade de parceiro");
await A.fill("#fnForm [name=description]", "Mensalidade parceiro Lisboa"); await A.fill("#fnForm [name=amount]", "1.250,90"); await A.click("#fnForm button.primary");
await A.waitForSelector("#tBody td:has-text('Mensalidade parceiro Lisboa')", { timeout: 15000 });
const fin = (await api("/api/financeiro-manual")).data; if (!fin.lancamentos.some(e => e.amount_minor === 125090)) throw new Error("lançamento não gravado");
await A.screenshot({ path: `${OUT}/x6_financeiro.png` });
await A.click("#tTabs button:has-text('Estoque das operações')"); await A.waitForSelector("#skForm", { timeout: 15000 });
await A.fill("#skForm [name=name]", "Cabo de rede"); await A.fill("#skForm [name=min_qty]", "5"); await A.click("#skForm button.primary");
await A.waitForSelector("#tBody td:has-text('Cabo de rede')", { timeout: 15000 });
const mv = "#tBody tr:has-text('Cabo de rede') form[data-mv]";
await A.fill(`${mv} [name=qty]`, "10"); await A.fill(`${mv} [name=reason]`, "compra inicial"); await A.click(`${mv} button`);
await A.waitForSelector("#tBody td:has-text('compra inicial')", { timeout: 15000 });
await A.screenshot({ path: `${OUT}/x7_estoque.png` });
log("financeiro manual (R$ 1.250,90) e estoque (entrada 10) lançados pela tela");
await A.click("#viewSeg button[data-view=\"3d\"]");
// 4. torre no mapa e Sentinela
await A.click("#closePanel", { timeout: 2000 }).catch(() => {}); await A.fill("#q", "Portugal"); await A.click("#qres button:has-text('Mercado · Portugal')");
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
