// E2E: mapa real (país → estado), central de comandos, quadro de tarefas (arrastar) e atendimento WhatsApp (simulado no demo).
// Uso: node scripts/e2e_mapa_canais.mjs http://127.0.0.1:8000 /tmp/shots
import { chromium } from "playwright";
const BASE = process.argv[2] || "http://127.0.0.1:8000", OUT = process.argv[3] || "/tmp/shots";
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const errors = []; const log = (...a) => console.log("•", ...a);
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } }); const A = await ctx.newPage();
A.on("pageerror", e => errors.push(e.message));
A.on("console", m => { if (m.type() === "error" && !/status of (400|401|403|404|409|503)/.test(m.text())) errors.push(m.text()); });
await A.goto(BASE + "/"); await A.fill("#login form.f [name=email]", "admin@central.demo"); await A.fill("#login form.f [name=password]", "demo1234");
await A.click("#login form.f button.primary"); await A.waitForSelector("#live.live", { timeout: 20000 });

// 1. mapa real carregado (download real no CI; fixture offline aqui)
await A.waitForFunction(() => document.querySelectorAll(".lbl.pais").length > 0, null, { timeout: 120000 });
await A.waitForTimeout(2500); await A.screenshot({ path: `${OUT}/m1_mapa_mundi.png` });
log("mapa-múndi carregado; rótulos de país:", await A.$$eval(".lbl.pais", e => e.map(x => x.textContent.split(" ")[0]).join(", ")));

// 2. país → estados → estado
await A.fill("#q", "brasil"); await A.click("#qres button:has-text('País · Brasil')");
await A.waitForSelector("#pTitle:has-text('Brasil')");
await A.waitForSelector("#pBody [data-st]", { timeout: 180000 });  // estados (download único no CI)
log("estados do Brasil:", await A.$$eval("#pBody [data-st]", b => b.length));
await A.waitForTimeout(1800); await A.screenshot({ path: `${OUT}/m2_brasil_estados.png` });
await A.click("#pBody [data-st='São Paulo']"); await A.waitForSelector("#pTitle:has-text('São Paulo')");
const unitsSP = await A.$$eval("#pBody .card", c => c.length); log("unidades em SP no painel:", unitsSP);
if (!unitsSP) throw new Error("nenhuma unidade listada em São Paulo");
await A.waitForTimeout(1500); await A.screenshot({ path: `${OUT}/m3_estado_sp.png` });

// 3. central de comandos
await A.keyboard.press("Control+k"); await A.waitForSelector("#cmdIn");
await A.fill("#cmdIn", "chamado Casa Aurora unidade Loja Pinheiros prazo amanhã: impressora da recepção sem rede");
await A.click("#cmdForm button"); await A.waitForSelector("#cmdOk"); log("comando interpretado:", (await A.textContent("#cmdRes h4")).trim());
await A.click("#cmdOk"); await A.waitForSelector("#cmdTask", { timeout: 10000 }); log("chamado criado por comando");
await A.click("#closePanel");

// 4. quadro de tarefas: arrastar de "a fazer" para "em andamento"
await A.click("#viewSeg button[data-view=tabelas]"); await A.click("#tTabs button:has-text('Quadro de tarefas')");
await A.waitForSelector(".kcol[data-col=a_fazer] .kcard");
const card = A.locator(".kcol[data-col=a_fazer] .kcard", { hasText: "Atendimento de chamado" }).first();
const id = await card.getAttribute("data-id");
await card.dragTo(A.locator(".kcol[data-col=em_andamento]"));
await A.waitForSelector(`.kcol[data-col=em_andamento] .kcard[data-id="${id}"]`, { timeout: 10000 });
log("cartão arrastado para Em andamento"); await A.screenshot({ path: `${OUT}/m4_quadro.png` });

// 5. atendimento: mensagem simulada de número novo → vincular → responder → abrir chamado
await A.click("#tTabs button:has-text('Atendimento')"); await A.waitForSelector("#simForm");
await A.fill("#simForm [name=phone]", "11 97777-1234"); await A.fill("#simForm [name=text]", "Bom dia! O Wi-Fi da loja caiu.");
await A.click("#simForm button"); await A.waitForSelector(".inbox .list button:has-text('5511977771234')", { timeout: 10000 });
await A.click(".inbox .list button:has-text('5511977771234')"); await A.waitForSelector("#lnk");
await A.selectOption("#lnkP", { label: "Casa Aurora (DEMO)" }); await A.click("#lnk"); await A.waitForSelector("#tk", { timeout: 10000 });
await A.fill("#sendForm textarea", "Bom dia! Já estamos verificando o Wi-Fi."); await A.click("#sendForm button");
await A.waitForSelector(".bubble.out:has-text('verificando o Wi-Fi')", { timeout: 10000 });
await A.click("#tk"); await A.waitForSelector("#pTitle:has-text('Atendimento de chamado')", { timeout: 10000 });
log("conversa vinculada, resposta enviada (simulada) e chamado aberto a partir do WhatsApp");
await A.click("#closePanel"); await A.screenshot({ path: `${OUT}/m5_atendimento.png` });

await browser.close();
if (errors.length) { console.log("ERROS:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E MAPA + COMANDOS + ATENDIMENTO OK");
