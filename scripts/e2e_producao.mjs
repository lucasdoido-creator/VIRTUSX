// E2E da operação real (produção limpa): primeiro acesso → cadastros → freelancer autorizado → checklist de TI → 3D.
// Uso: node scripts/e2e_producao.mjs http://127.0.0.1:8001 /tmp/shots
import { chromium } from "playwright";
const BASE = process.argv[2] || "http://127.0.0.1:8001", OUT = process.argv[3] || "/tmp/shots";
const browser = await chromium.launch({ args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"] });
const errors = []; const log = (...a) => console.log("•", ...a);
const PW = "senha-forte-123";
async function page(viewport = { width: 1440, height: 900 }) {
  const ctx = await browser.newContext({ viewport }); const p = await ctx.newPage();
  p.on("pageerror", e => errors.push(e.message));
  p.on("console", m => { if (m.type() === "error" && !/status of (401|403|404|409)/.test(m.text())) errors.push(m.text()); });
  return p;
}

// 1. primeiro acesso: cria o administrador
const A = await page();
await A.goto(BASE + "/"); await A.waitForSelector("#setupForm:not(.hidden)");
if (!(await A.textContent("#envbar")).includes("PRODUÇÃO")) throw new Error("faixa de produção ausente");
await A.fill("#setupForm [name=name]", "Lucas"); await A.fill("#setupForm [name=email]", "lucas@empresa.com"); await A.fill("#setupForm [name=password]", PW);
await A.click("#setupForm button"); await A.waitForSelector("#app:not(.hidden)", { timeout: 15000 });
log("administrador criado no primeiro acesso; faixa:", (await A.textContent("#envbar")).trim());

// 2. cadastros pela tela
await A.click("#viewSeg button[data-view=tabelas]"); await A.click("#tTabs button:has-text('Cadastros')");
const fill = async (form, vals) => { for (const [k, v] of Object.entries(vals)) {
  const el = A.locator(`form[data-form=${form}] [name=${k}]`);
  if ((await el.getAttribute("type")) === "checkbox") { if (v) await el.check(); else await el.uncheck(); }
  else if ((await el.evaluate(e => e.tagName)) === "SELECT") await el.selectOption({ label: v }); else await el.fill(v); } };
const save = async (form, txt) => { await A.click(`form[data-form=${form}] button`); await A.waitForFunction(t => document.querySelector("#tBody").textContent.includes(t), txt, { timeout: 10000 }); };
for (const [n, c] of [["Base Centro", "São Paulo"], ["Base Campinas", "Campinas"], ["Base Santos", "Santos"]]) { await fill("base", { name: n, city: c }); await save("base", n); }
await fill("client", { name: "Clínica Vida", segment: "saúde", contract: "1.500,00", contact_name: "Dra. Paula" }); await save("client", "Clínica Vida");
await fill("unit", { client_id: "Clínica Vida", name: "Matriz", city: "São Paulo", address: "Av. Exemplo, 10", location_confirmed: true, base_id: "Base Centro" }); await save("unit", "Matriz");
await fill("unit", { client_id: "Clínica Vida", name: "Unidade Campinas", city: "Campinas", base_id: "Base Campinas" }); await save("unit", "Unidade Campinas");
await fill("user", { name: "Ana Freelancer", email: "ana@freela.com", role: "Técnico / freelancer", base_id: "Base Centro", password: PW, active: false });
await save("user", "ana@freela.com");
await A.waitForSelector("[data-auth]"); log("freelancer cadastrado aguardando autorização");
await A.screenshot({ path: `${OUT}/p1_cadastros.png`, fullPage: true });
await A.click("[data-auth]"); await A.waitForFunction(() => !document.querySelector("[data-auth]"), null, { timeout: 10000 });
log("freelancer autorizado");

// 3. tarefa com checklist de TI atribuída à freelancer, criada no prédio do cliente
await A.click('#viewSeg button[data-view="3d"]'); await A.waitForTimeout(1500);
await A.fill("#q", "Clínica Vida"); await A.click("#qres button:has-text('Parceiro · Clínica Vida')"); await A.waitForSelector("#pTitle:has-text('Clínica Vida')");
await A.click("#pTabs button:has-text('Operação')"); await A.click("#pBody summary:has-text('Nova tarefa')");
await A.selectOption("#taskForm [name=template]", "chamado_ti");
await A.selectOption("#taskForm [name=unit_id]", { label: "Matriz" });
await A.selectOption("#taskForm [name=assignee]", { label: "Ana Freelancer" });
await A.click("#taskForm button.primary"); await A.waitForSelector("#pBody tr[data-task-open]:has-text('Atendimento de chamado')", { timeout: 10000 });
log("chamado com checklist criado e atribuído");
await A.click("#closePanel"); await A.waitForTimeout(1200);
await A.screenshot({ path: `${OUT}/p2_mundo_bases.png` });
const baseLabels = await A.$$eval(".lbl", els => els.filter(e => e.textContent.startsWith("Base ·")).length);
if (baseLabels !== 3) throw new Error("bases no 3D: " + baseLabels);
log("3 bases visíveis no mundo 3D");

// 4. freelancer entra, vê só a própria tarefa, marca o checklist e conclui com evidência
const T = await page({ width: 390, height: 844 });
await T.goto(BASE + "/"); await T.fill("#login form.f [name=email]", "ana@freela.com"); await T.fill("#login form.f [name=password]", PW);
await T.click("#login form.f button.primary"); await T.waitForSelector("#tBody table");
const rows = await T.$$eval("#tBody tr[data-i]", r => r.length); if (rows !== 1) throw new Error("técnico vê tarefas demais: " + rows);
await T.click("#tBody tr[data-i]"); await T.waitForSelector("[data-ck]");
const n = await T.$$eval("[data-ck]", x => x.length);
for (let i = 0; i < n; i++) { await T.check(`[data-ck="${i}"]`); await T.waitForSelector(`#pBody h4:has-text("Checklist ${i + 1}/${n}")`); }
await T.setInputFiles("#tFile [name=file]", { name: "relatorio.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 relatorio") });
await T.check("#tFile [name=ev]"); await T.click("#tFile button"); await T.waitForSelector("#pBody a:has-text('relatorio.pdf')");
await T.selectOption("#tEdit [name=status]", "concluida"); await T.fill("#tEdit [name=note]", "Chamado resolvido");
await T.click("#tEdit button"); await T.waitForFunction(() => document.querySelector("#pSub").textContent.includes("concluída"), null, { timeout: 10000 });
await T.screenshot({ path: `${OUT}/p3_tecnico_celular.png` });
log(`freelancer marcou ${n} itens, anexou evidência e concluiu pelo celular`);

const vig = await A.evaluate(async () => (await (await fetch("/api/saude", { headers: { "X-Requested-With": "plataforma" } })).json()).data);
const vigErr = vig.client_errors.filter(e => !e.resolved);
if (vigErr.length) { console.log("VIGIA REGISTROU ERRO DE TELA:\n" + JSON.stringify(vigErr.slice(0, 5), null, 1)); process.exit(1); }
log("vigia: nenhum erro de tela registrado; saúde =", vig.level);
await browser.close();
if (errors.length) { console.log("ERROS:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E PRODUÇÃO OK");
