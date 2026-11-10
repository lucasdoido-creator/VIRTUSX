// Navegação do mapa com mouse e botões: arrastar move, roda dá zoom no ponto do mouse, botão direito gira, botões funcionam.
import { chromium } from "playwright";
const BASE = process.argv[2] || "http://127.0.0.1:8000", OUT = process.argv[3] || "/tmp/shots";
const log = (...a) => console.log("•", ...a); const errors = [];
const b = await chromium.launch(); const A = await b.newPage({ viewport: { width: 1440, height: 900 } });
A.on("pageerror", e => errors.push(e.message));
await A.goto(BASE + "/"); await A.fill("#login form.f [name=email]", "admin@central.demo"); await A.fill("#login form.f [name=password]", "demo1234");
await A.click("#login form.f button.primary"); await A.waitForSelector("#live.live", { timeout: 20000 }); await A.waitForTimeout(2500);
const cam = () => A.evaluate(() => window.__cam());
const box = await A.locator("#gl").boundingBox(); const cx = box.x + box.width / 2, cy = box.y + box.height / 2;
// zoom no ponto do mouse
let c0 = await cam(); await A.mouse.move(cx + 300, cy); for (let i = 0; i < 5; i++) { await A.mouse.wheel(0, -200); await A.waitForTimeout(30); }
let c1 = await cam();
if (!(c1.dist < c0.dist * 0.6)) throw new Error("roda não aproximou: " + c0.dist + " → " + c1.dist);
if (!(c1.tx > c0.tx)) throw new Error("zoom não foi em direção ao cursor");
log(`roda: distância ${Math.round(c0.dist)} → ${Math.round(c1.dist)}, centro foi para o lado do cursor`);
// arrastar move (não gira)
await A.mouse.move(cx, cy); await A.mouse.down(); await A.mouse.move(cx - 200, cy, { steps: 8 }); await A.mouse.up();
let c2 = await cam();
if (Math.abs(c2.yaw - c1.yaw) > 1e-6 || Math.abs(c2.pitch - c1.pitch) > 1e-6) throw new Error("arrastar girou a câmera");
if (!(Math.hypot(c2.tx - c1.tx, c2.tz - c1.tz) > 1)) throw new Error("arrastar não moveu o mapa");
log("arrastar: move o mapa sem girar");
// botão direito gira/inclina
await A.mouse.move(cx, cy); await A.mouse.down({ button: "right" }); await A.mouse.move(cx + 150, cy + 60, { steps: 8 }); await A.mouse.up({ button: "right" });
let c3 = await cam();
if (!(Math.abs(c3.yaw - c2.yaw) > 0.3)) throw new Error("botão direito não girou");
log("botão direito: gira e inclina");
// botões
await A.click("#navctl [data-nav=north]"); await A.waitForTimeout(1400);
if (Math.abs((await cam()).yaw) > 0.01) throw new Error("botão N não apontou para o norte");
const d0 = (await cam()).dist; await A.click("#navctl [data-nav=in]"); if (!((await cam()).dist < d0)) throw new Error("botão + não aproximou");
for (let k = 0; k < 2; k++) { const p0 = (await cam()).pitch; await A.click("#navctl [data-nav=tilt]"); await A.waitForTimeout(1400); const p1 = (await cam()).pitch;
  if (p0 > 1.3 ? !(p1 < 1) : !(p1 > 1.4)) throw new Error(`2D/3D não alternou: ${p0} → ${p1}`); }
await A.click("#navctl [data-nav=help]"); await A.waitForSelector("#navhelp:not(.hidden)");
await A.screenshot({ path: `${OUT}/n1_navegacao.png` });
await A.click("#navctl [data-nav=home]"); await A.waitForTimeout(1400);
// clique ainda abre (não confundido com arrastar)
await A.fill("#q", "Casa Aurora"); await A.click("#qres button:has-text('Parceiro · Casa Aurora')"); await A.waitForSelector("#pTitle:has-text('Casa Aurora')");
log("botões +, N, 2D, ajuda e visão geral funcionam; busca abre o prédio");
// voo interrompido pelo mouse: a câmera obedece o usuário
await A.click("#navctl [data-nav=home]"); await A.waitForTimeout(150); await A.mouse.move(cx, cy); await A.mouse.wheel(0, -300); await A.waitForTimeout(1500);
const c5 = await cam(); await A.waitForTimeout(500); const c6 = await cam();
if (Math.abs(c5.dist - c6.dist) > 1) throw new Error("voo continuou depois da interação");
log("mouse interrompe voo em andamento");
await b.close();
if (errors.length) { console.log("ERROS:\n" + errors.join("\n")); process.exit(1); }
console.log("E2E NAVEGAÇÃO OK");
