// Animações de interface. Usa GSAP quando presente (web/vendor/gsap.min.js); caso contrário,
// um interpolador local com a mesma semântica básica (to, duration, ease, onUpdate, onComplete).
const prefersReduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
export const motion = { reduced: prefersReduced || localStorage.getItem("reduzirAnimacoes") === "1" };

const ease = {
  "power2.out": t => 1 - Math.pow(1 - t, 2),
  "power3.inOut": t => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2),
  "none": t => t,
};
const active = new Set();

export function to(target, vars) {
  const { duration = 0.6, ease: e = "power3.inOut", onUpdate, onComplete, ...props } = vars;
  if (motion.reduced || duration <= 0) {
    Object.assign(target, props); onUpdate && onUpdate(); onComplete && onComplete(); return { kill() {} };
  }
  if (window.gsap) return window.gsap.to(target, { duration, ease: e, onUpdate, onComplete, ...props });
  const from = {}; for (const k in props) from[k] = target[k];
  const fn = ease[e] || ease["power3.inOut"]; const t0 = performance.now();
  const job = { dead: false, kill() { this.dead = true; active.delete(this); } };
  active.add(job);
  const step = now => {
    if (job.dead) return;
    const p = Math.min(1, (now - t0) / (duration * 1000)); const k = fn(p);
    for (const key in props) target[key] = from[key] + (props[key] - from[key]) * k;
    onUpdate && onUpdate();
    if (p < 1) requestAnimationFrame(step); else { active.delete(job); onComplete && onComplete(); }
  };
  requestAnimationFrame(step);
  return job;
}

export function slideIn(el) {
  if (motion.reduced) { el.style.transform = "translateX(0)"; return; }
  const s = { x: 105 }; el.style.transform = "translateX(105%)";
  to(s, { x: 0, duration: 0.35, ease: "power2.out", onUpdate: () => (el.style.transform = `translateX(${s.x}%)`) });
}
export function slideOut(el, done) {
  if (motion.reduced) { el.style.transform = "translateX(105%)"; done && done(); return; }
  const s = { x: 0 };
  to(s, { x: 105, duration: 0.25, ease: "power2.out", onUpdate: () => (el.style.transform = `translateX(${s.x}%)`), onComplete: done });
}
export function fadeIn(el) {
  if (motion.reduced) { el.style.opacity = 1; return; }
  const s = { o: 0 }; el.style.opacity = 0;
  to(s, { o: 1, duration: 0.3, ease: "power2.out", onUpdate: () => (el.style.opacity = s.o) });
}

// Carrega GSAP somente se o arquivo foi colocado em web/vendor/ (o servidor informa em /api/health).
export async function loadGsapIfAvailable() {
  try {
    const h = await (await fetch("/api/health")).json();
    if (!h.vendor || !h.vendor.gsap) return false;
    await new Promise((res, rej) => { const s = document.createElement("script"); s.src = "/static/vendor/gsap.min.js"; s.onload = res; s.onerror = rej; document.head.appendChild(s); });
    return true;
  } catch { return false; }
}
