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

// Sede no lugar certo + operação manual para um agente
await A.click("[data-view='tabelas']"); await A.click("#tTabs button:has-text('Cadastros')");
await A.waitForSelector("form[data-form=sede]");
await A.fill("form[data-form=sede] [name=city]", "Campinas"); await A.fill("form[data-form=sede] [name=coords]", "-22.9056, -47.0608");
await A.click("form[data-form=sede] button"); await A.waitForSelector("#tBody .card:has-text('Sede') .badge:has-text('coordenadas informadas')", { timeout: 10000 });
log("sede cadastrada com posição exata");
await A.click("#tTabs button:has-text('Agentes')"); await A.click("#tBody tr:has-text('Coordenação')");
await A.waitForSelector("#agOp", { timeout: 10000 });
await A.fill("#agOp [name=description]", "Foco nos chamados atrasados da Casa Aurora");
await A.click("#agOp button.primary");
await A.waitForFunction(() => (document.querySelector("#pBody")?.textContent || "").includes("Revisão de prazos e pendências (cálculo determinístico)"), null, { timeout: 20000 });
log("operação manual: coordenador executou e entregou o relatório na tarefa");
await A.screenshot({ path: `${OUT}/m7_operacao_agente.png` });
await A.click("#closePanel");

// CEO da base, casa do freelancer e fidelidade do cliente
await A.click("[data-view='3d']");
await A.fill("#q", "Base Demo"); await A.click("#qres button:has-text('Base · Base Demo')");
await A.waitForSelector("#ceoBaseRun", { timeout: 15000 }); await A.click("#ceoBaseRun");
await A.waitForFunction(() => /preventiva\(s\) agendada/.test(document.querySelector("#baseCeo")?.textContent || ""), null, { timeout: 20000 });
log("CEO da base organizou as tarefas:", (await A.textContent("#baseCeo")).replace(/\s+/g, " ").slice(0, 140));
await A.screenshot({ path: `${OUT}/m8_ceo_base.png` }); await A.click("#closePanel");
await A.fill("#q", "Bia"); await A.click("#qres button:has-text('Freelancer · Bia')");
await A.waitForSelector("#pTitle:has-text('Bia')"); await A.screenshot({ path: `${OUT}/m9_casa_freelancer.png` }); await A.click("#closePanel");
await A.fill("#q", "Casa Aurora"); await A.click("#qres button:has-text('Parceiro · Casa Aurora')");
await A.waitForSelector("#pBody h4:has-text('Fidelidade')", { timeout: 10000 });
log("freelancer (casa) e fidelidade do cliente:", (await A.textContent("#pBody .card:has(.bar)")).replace(/\s+/g, " ").slice(0, 90));
await A.click("#closePanel");

// Tipo de mapa e saúde do sistema
await A.click("[data-view='3d']"); await A.click("#basemapSeg [data-bm='ruas']");
if ((await A.getAttribute("#basemapSeg [data-bm='ruas']", "aria-pressed")) !== "true") throw new Error("seletor de mapa não marcou Ruas");
await A.click("#basemapSeg [data-bm='satelite']"); await A.waitForTimeout(1500);
await A.click("#saudeBtn"); await A.waitForSelector("#pTitle:has-text('Saúde do sistema')", { timeout: 10000 });
await Promise.all([A.waitForResponse(r => r.url().includes("/api/saude?agora=1"), { timeout: 30000 }), A.click("#sdNow")]);
await A.waitForSelector("#pBody td b:has-text('Banco de dados')", { timeout: 15000 }); await A.waitForTimeout(400);
await A.fill("#keyForm [name=key]", "apikey_012TQ73WMkUYUsfxpnpgaUpk"); await A.click("#keyForm button.primary");
await A.waitForSelector(".toast:has-text('identificador da chave')", { timeout: 10000 });
if (await A.inputValue("#keyForm [name=key]")) throw new Error("campo da chave não foi limpo");
log("campo da chave: identificador recusado com explicação; campo limpo na hora");
await A.screenshot({ path: `${OUT}/m6_saude.png` });
// Nenhum erro de JavaScript pode ter sido registrado pelo vigia durante todo o teste
const saude = await A.evaluate(async () => (await (await fetch("/api/saude", { headers: { "X-Requested-With": "plataforma" } })).json()).data);
const errTela = saude.client_errors.filter(e => !e.resolved);
if (errTela.length) throw new Error("vigia registrou erro de tela: " + JSON.stringify(errTela.slice(0, 3)));
if (process.env.REQUIRE_REAL_MAP) {  // CI (com internet): mapa real E imagem de satélite precisam ter baixado de verdade
  const fresh = await A.evaluate(async () => (await (await fetch("/api/saude?agora=1", { headers: { "X-Requested-With": "plataforma" } })).json()).data);
  const mapa = fresh.checks.find(c => c.code === "mapa");
  if (mapa.level !== "verde") throw new Error("mapa real/satélite não baixou: " + mapa.detail);
  log("mapa real + satélite NASA baixados:", mapa.detail);
}
log("seletor de mapa ok; painel de saúde ok (" + saude.level + "); nenhum erro de tela registrado");

await browser.close();
if (errors.length) { console.log("ERROS:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E MAPA + COMANDOS + ATENDIMENTO OK");
