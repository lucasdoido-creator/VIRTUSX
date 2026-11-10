// Teste de navegador ponta a ponta (Playwright). Uso: node scripts/e2e.mjs http://127.0.0.1:8000 /tmp/shots
import { chromium } from "playwright";
import { execSync } from "node:child_process";
const BASE = process.argv[2] || "http://127.0.0.1:8000", OUT = process.argv[3] || "/tmp/shots";
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const errors = []; const log = (...a) => console.log("•", ...a);
async function ctxPage(email, viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ viewport }); const page = await ctx.newPage();
  page.on("console", m => { if (m.type() === "error") errors.push(`[${email}] ${m.text()}`); });
  page.on("pageerror", e => errors.push(`[${email}] ${e.message}`));
  await page.goto(BASE + "/"); await page.waitForSelector("#login:not(.hidden)");
  await page.fill("[name=email]", email); await page.fill("[name=password]", "demo1234"); await page.click("#login button.primary");
  await page.waitForSelector("#app:not(.hidden)"); await page.waitForSelector("#live.live", { timeout: 15000 });
  return { ctx, page };
}
const api = (page, path, method = "GET", body) => page.evaluate(async ([path, method, body]) => {
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json", "X-Requested-With": "plataforma" }, body: body ? JSON.stringify(body) : undefined });
  return r.json(); }, [path, method, body]);

// Abre o prédio de um parceiro pela busca (no mapa-múndi os prédios só aparecem de perto)
const openPartner = async (page, name) => { await page.fill("#q", name); await page.click(`#qres button:has-text('Parceiro · ${name}')`); };

// 1. Central: visão global 3D
const A = await ctxPage("admin@central.demo"); await A.page.waitForTimeout(2500);
const ceoLabels = await A.page.$$eval(".lbl.ceo", els => els.length); log("rótulos de CEO no mundo:", ceoLabels);
if (!ceoLabels) throw new Error("CEO de IA não apareceu no mundo 3D");
await A.page.screenshot({ path: `${OUT}/01_global.png` }); log("visão global ok");
const labels = await A.page.$$eval(".lbl", els => els.filter(e => e.style.display !== "none").map(e => e.textContent));
log("rótulos visíveis:", labels.length, labels.slice(0, 6).join(" | "));

// 2. Parceiro brasileiro cria a página pelo prédio (UI)
const M = await ctxPage("marina@aurora.demo");
await openPartner(M.page, "Casa Aurora"); await M.page.waitForSelector("#panel:not(.hidden) #pTitle:has-text('Casa Aurora')");
await M.page.waitForTimeout(1500); await M.page.screenshot({ path: `${OUT}/02_predio_parceiro.png` });
await M.page.click("#pTabs button:has-text('Comercial')"); await M.page.waitForSelector("#sfForm");
await M.page.fill("#sfForm [name=slug]", "casa-aurora"); await M.page.fill("#sfForm [name=headline]", "Feito com calma, em São Paulo");
await M.page.click("#sfForm button.primary"); await M.page.waitForSelector("#pBody a:has-text('Abrir loja')", { timeout: 10000 }); log("página criada e publicada pelo parceiro");

// 2b. Diretoria: o parceiro envia uma ideia ao CEO de IA e acompanha o plano ser criado e distribuído
await M.page.click("#pTabs button:has-text('Diretoria')"); await M.page.waitForSelector("#ideaForm");
await M.page.fill("#ideaForm [name=idea]", "Kit presente corporativo com café especial e caneca personalizada");
await M.page.fill("#ideaForm [name=keywords]", "presente corporativo café");
await M.page.click("#ideaForm button.primary");
await M.page.waitForFunction(() => (document.querySelector("#pBody")?.textContent || "").includes("presente corporativo") &&
  [...document.querySelectorAll("#pBody .card")].some(c => c.textContent.includes("presente corporativo") && c.textContent.includes("em execução")), null, { timeout: 60000 }).catch(async e => {
    console.log("diag diretoria:", await M.page.evaluate(() => JSON.stringify({ ativo: document.activeElement && (document.activeElement.tagName + "#" + document.activeElement.id + "[" + (document.activeElement.name || "") + "]"),
      sujos: [...document.querySelectorAll("#pBody form input, #pBody form textarea")].filter(i => !["checkbox", "hidden", "file", "radio"].includes(i.type) && i.value !== i.defaultValue).map(i => i.name),
      texto: (document.querySelector("#pBody")?.textContent || "").slice(0, 600) }))); throw e; });
const staff = await M.page.$$eval("#pBody .card", cs => (cs.find(c => c.textContent.includes("presente corporativo")) || {}).textContent || "");
log("CEO planejou a ideia; equipe contratada grátis:", /Equipe contratada/.test(staff));
await M.page.screenshot({ path: `${OUT}/02b_diretoria_ceo.png` });

// 3. Cliente faz pedido de teste na loja
const C = await browser.newContext({ viewport: { width: 1200, height: 900 } }); const shop = await C.newPage();
shop.on("pageerror", e => errors.push("[loja] " + e.message));
await shop.goto(BASE + "/loja/casa-aurora"); await shop.waitForSelector("[data-add]");
const seller = await shop.textContent(".seller"); log("checkout identifica:", seller.replace(/\s+/g, " ").trim());
await shop.click("[data-add] >> nth=0"); await shop.click("[data-add] >> nth=0");
await shop.fill("[name=name]", "Cliente Teste"); await shop.fill("[name=email]", "cliente@example.com"); await shop.fill("[name=address]", "Rua A, 1");
await shop.fill("[name=city]", "São Paulo"); await shop.fill("[name=state]", "SP"); await shop.fill("[name=postal_code]", "01000-000");
await shop.screenshot({ path: `${OUT}/03_loja.png`, fullPage: true });
await shop.click("#buy"); await shop.waitForSelector(".status .big:has-text('Pagamento confirmado')", { timeout: 15000 });
await shop.screenshot({ path: `${OUT}/04_pedido_pago.png` }); log("pagamento de teste confirmado via webhook");

// 4. Operação registra envio → comissão
const orders = (await api(A.page, "/api/orders")).data; const o = orders.find(x => x.status === "pago");
const ship = await api(A.page, `/api/orders/${o.id}/ship`, "POST"); log("envio:", ship.data.tracking_code, "comissão:", ship.data.commission.amount_minor);
await A.page.waitForFunction(() => document.querySelector("#ticker").textContent.includes("comissao.apurada"), null, { timeout: 10000 });
log("centro recebeu comissao.apurada em tempo real");
await openPartner(A.page, "Casa Aurora"); await A.page.waitForTimeout(1800);
await A.page.click("#pTabs button:has-text('Financeiro')"); await A.page.waitForTimeout(500);
await A.page.screenshot({ path: `${OUT}/05_predio_financeiro.png` });

// 5. Desconexão e recuperação: o servidor cai, novos eventos acontecem, o painel reconecta e recebe o que perdeu.
const diag = await A.page.evaluate(() => { const el = document.querySelector("#panel"), r = el.getBoundingClientRect(), m = document.querySelector("main");
  return { rect: [r.x, r.y, r.width, r.height].map(Math.round), tf: el.style.transform, cls: el.className, win: [innerWidth, innerHeight, scrollX, scrollY],
    main: [m.scrollLeft, m.scrollTop, m.clientWidth, m.clientHeight], doc: [document.documentElement.scrollWidth, document.documentElement.scrollHeight],
    lbls: document.querySelectorAll(".lbl").length }; });
log("painel antes de fechar:", JSON.stringify(diag), "erros até aqui:", JSON.stringify(errors.slice(0, 5)));
await A.page.click("#closePanel");
await A.page.evaluate(() => { window.__eventos = []; });  // só conta o que chegar depois da queda
await A.page.route("**/api/stream**", r => r.abort());
execSync(process.env.E2E_RESTART || "true", { stdio: "ignore" });
await A.page.waitForFunction(() => !document.querySelector("#live").classList.contains("live"), null, { timeout: 30000 });
log("estado durante a queda:", (await A.page.textContent("#live")).trim());
await A.page.screenshot({ path: `${OUT}/05b_reconectando.png` });
const d = await (await fetch(BASE + "/api/public/stores/casa-aurora/checkout", { method: "POST",
  headers: { "Content-Type": "application/json", "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ items: [{ product_id: "prd_caneca", qty: 1 }],
  customer: { name: "Outro Cliente", email: "outro@example.com" }, shipping: { country: "BR", address: "Rua B", city: "Campinas", postal_code: "13000-000" } }) })).json();
log("pedido criado enquanto o painel estava sem conexão:", d.data.order_code);
await A.page.unroute("**/api/stream**");
await A.page.waitForSelector("#live.live", { timeout: 30000 });
await A.page.waitForFunction(() => (window.__eventos || []).includes("estoque.reservado"), null, { timeout: 30000 })
  .catch(async e => { console.log("eventos recebidos:", await A.page.evaluate(() => (window.__eventos || []).slice(-40).join(", "))); throw e; });
log("reconectado e eventos perdidos recebidos");

// 6. Modo tabelas e visão móvel leve
await A.page.click("#viewSeg button[data-view=tabelas]"); await A.page.waitForSelector("#tBody table");
await A.page.screenshot({ path: `${OUT}/06_tabelas.png` });
await A.page.click("#tTabs button:has-text('Agentes')"); await A.page.waitForTimeout(600); await A.page.screenshot({ path: `${OUT}/07_agentes.png` });
const Mob = await ctxPage("marina@aurora.demo", { width: 390, height: 844 }); await Mob.page.waitForTimeout(2000);
await Mob.page.screenshot({ path: `${OUT}/08_movel.png` });
log("móvel modo leve:", await Mob.page.isChecked("#lite"));

// 7. Isolamento na interface do parceiro
const pw = (await api(M.page, "/api/world")).data; log("parceiro vê prédios:", pw.partners.map(p => p.name).join(", "));
const forbidden = await api(M.page, "/api/partners/prt_lisboa/building"); log("acesso a outro parceiro:", forbidden.error);

const vig = await A.page.evaluate(async () => (await (await fetch("/api/saude", { headers: { "X-Requested-With": "plataforma" } })).json()).data);
const vigErr = vig.client_errors.filter(e => !e.resolved);
if (vigErr.length) { console.log("VIGIA REGISTROU ERRO DE TELA:\n" + JSON.stringify(vigErr.slice(0, 5), null, 1)); process.exit(1); }
log("vigia: nenhum erro de tela registrado; saúde =", vig.level);
await browser.close();
const EXPECTED = [/status of 401/, /status of 403/, /ERR_INCOMPLETE_CHUNKED_ENCODING/, /ERR_FAILED/, /ERR_EMPTY_RESPONSE/, /ERR_CONNECTION_REFUSED/]; // login inicial, teste de isolamento, queda proposital do servidor
const real = errors.filter(e => !EXPECTED.some(r => r.test(e)));
if (real.length) { errors.length = 0; errors.push(...real); console.log("ERROS DE CONSOLE:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E OK");
