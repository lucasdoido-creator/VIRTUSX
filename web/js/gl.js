// Renderizador WebGL mínimo (substitui Three.js nesta etapa, pois o ambiente de build não tem acesso à rede).
// Cena declarativa: caixas (prédios, armazéns, avatares, solo), linhas (rede) e pulsos (eventos reais).
const V = {
  sub: (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]],
  cross: (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]],
  norm: a => { const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0] / l, a[1] / l, a[2] / l]; },
  dot: (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2],
};
const M = {
  persp(fov, asp, n, f) { const t = 1 / Math.tan(fov / 2), nf = 1 / (n - f);
    return new Float32Array([t / asp, 0, 0, 0, 0, t, 0, 0, 0, 0, (f + n) * nf, -1, 0, 0, 2 * f * n * nf, 0]); },
  lookAt(e, c, up) { const z = V.norm(V.sub(e, c)), x = V.norm(V.cross(up, z)), y = V.cross(z, x);
    return new Float32Array([x[0], y[0], z[0], 0, x[1], y[1], z[1], 0, x[2], y[2], z[2], 0, -V.dot(x, e), -V.dot(y, e), -V.dot(z, e), 1]); },
  mul(a, b) { const o = new Float32Array(16);
    for (let i = 0; i < 4; i++) for (let j = 0; j < 4; j++) { let s = 0; for (let k = 0; k < 4; k++) s += a[k * 4 + j] * b[i * 4 + k]; o[i * 4 + j] = s; }
    return o; },
  inv(m) { const a = m, o = new Float32Array(16);
    const b00 = a[0] * a[5] - a[1] * a[4], b01 = a[0] * a[6] - a[2] * a[4], b02 = a[0] * a[7] - a[3] * a[4], b03 = a[1] * a[6] - a[2] * a[5],
      b04 = a[1] * a[7] - a[3] * a[5], b05 = a[2] * a[7] - a[3] * a[6], b06 = a[8] * a[13] - a[9] * a[12], b07 = a[8] * a[14] - a[10] * a[12],
      b08 = a[8] * a[15] - a[11] * a[12], b09 = a[9] * a[14] - a[10] * a[13], b10 = a[9] * a[15] - a[11] * a[13], b11 = a[10] * a[15] - a[11] * a[14];
    let d = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06; if (!d) return null; d = 1 / d;
    o[0] = (a[5] * b11 - a[6] * b10 + a[7] * b09) * d; o[1] = (a[2] * b10 - a[1] * b11 - a[3] * b09) * d;
    o[2] = (a[13] * b05 - a[14] * b04 + a[15] * b03) * d; o[3] = (a[10] * b04 - a[9] * b05 - a[11] * b03) * d;
    o[4] = (a[6] * b08 - a[4] * b11 - a[7] * b07) * d; o[5] = (a[0] * b11 - a[2] * b08 + a[3] * b07) * d;
    o[6] = (a[14] * b02 - a[12] * b05 - a[15] * b01) * d; o[7] = (a[8] * b05 - a[10] * b02 + a[11] * b01) * d;
    o[8] = (a[4] * b10 - a[5] * b08 + a[7] * b06) * d; o[9] = (a[1] * b08 - a[0] * b10 - a[3] * b06) * d;
    o[10] = (a[12] * b04 - a[13] * b02 + a[15] * b00) * d; o[11] = (a[9] * b02 - a[8] * b04 - a[11] * b00) * d;
    o[12] = (a[5] * b07 - a[4] * b09 - a[6] * b06) * d; o[13] = (a[0] * b09 - a[1] * b07 + a[2] * b06) * d;
    o[14] = (a[13] * b01 - a[12] * b03 - a[14] * b00) * d; o[15] = (a[8] * b03 - a[9] * b01 + a[10] * b00) * d; return o; },
  xf(m, v) { const x = v[0], y = v[1], z = v[2], w = v[3] ?? 1;
    return [m[0] * x + m[4] * y + m[8] * z + m[12] * w, m[1] * x + m[5] * y + m[9] * z + m[13] * w,
            m[2] * x + m[6] * y + m[10] * z + m[14] * w, m[3] * x + m[7] * y + m[11] * z + m[15] * w]; },
};

const VS = `attribute vec3 aPos;attribute vec3 aNor;uniform mat4 uVP;uniform vec3 uOff;uniform vec3 uScale;
varying vec3 vNor;varying vec3 vLocal;varying vec3 vWorld;
void main(){vec3 p=aPos*uScale+uOff;vLocal=aPos*uScale;vNor=aNor;vWorld=p;gl_Position=uVP*vec4(p,1.);}`;
const FS = `precision mediump float;uniform vec3 uColor;uniform float uEmissive;uniform float uWin;uniform float uGrid;uniform float uAlpha;
uniform vec3 uFog;uniform vec3 uEye;uniform vec3 uWinColor;uniform float uEdge;uniform sampler2D uTex;uniform float uUseTex;uniform float uK;uniform float uFogK;varying vec3 vNor;varying vec3 vLocal;varying vec3 vWorld;
void main(){vec3 n=normalize(vNor);vec3 L=normalize(vec3(.45,.9,.3));float diff=max(dot(n,L),0.);float hemi=.5+.5*n.y;
vec3 col=uColor*(.30+.55*diff+.22*hemi);
if(uUseTex>.5&&n.y>.5){vec2 uv=vec2((vWorld.x/uK+180.)/360.,(vWorld.z/uK+90.)/180.);col=(uv.x<0.||uv.x>1.||uv.y<0.||uv.y>1.)?uFog*1.2:texture2D(uTex,uv).rgb;}
if(uWin>.5&&abs(n.y)<.5){vec2 uv=vec2(abs(n.x)>.5?vLocal.z:vLocal.x,vLocal.y);vec2 cell=fract(uv/vec2(1.1,1.3));
float w=step(.22,cell.x)*step(cell.x,.78)*step(.28,cell.y)*step(cell.y,.78)*step(.9,vLocal.y);
vec2 id=floor(uv/vec2(1.1,1.3));float r=fract(sin(dot(id,vec2(12.9898,78.233)))*43758.5453);
col=mix(col,uWinColor*(.35+.65*r),w*.8);}
if(uGrid>.5){vec2 g=abs(fract(vWorld.xz/10.)-.5);float line=1.-smoothstep(0.,.025,min(g.x,g.y));vec2 G=abs(fract(vWorld.xz/60.)-.5);float big=1.-smoothstep(0.,.006,min(G.x,G.y));col+=vec3(.03,.10,.18)*line+vec3(.20,.10,.45)*big;}
if(uEdge>.5&&abs(n.y)<.5){float e=1.-smoothstep(0.,.35,min(abs(vLocal.y-floor(vLocal.y/6.)*6.),6.));col+=uWinColor*.25*e;}
col+=uColor*uEmissive;float d=length(vWorld-uEye);float fog=1.-exp(-pow(d*uFogK,2.));col=mix(col,uFog,clamp(fog,0.,1.));
gl_FragColor=vec4(col,uAlpha);}`;
const LVS = `attribute vec3 aPos;uniform mat4 uVP;void main(){gl_Position=uVP*vec4(aPos,1.);}`;
const LFS = `precision mediump float;uniform vec4 uColor;void main(){gl_FragColor=uColor;}`;

function cube() {
  const f = [ // face: normal, 4 corners
    [[0, 0, 1], [[-.5, 0, .5], [.5, 0, .5], [.5, 1, .5], [-.5, 1, .5]]], [[0, 0, -1], [[.5, 0, -.5], [-.5, 0, -.5], [-.5, 1, -.5], [.5, 1, -.5]]],
    [[1, 0, 0], [[.5, 0, .5], [.5, 0, -.5], [.5, 1, -.5], [.5, 1, .5]]], [[-1, 0, 0], [[-.5, 0, -.5], [-.5, 0, .5], [-.5, 1, .5], [-.5, 1, -.5]]],
    [[0, 1, 0], [[-.5, 1, .5], [.5, 1, .5], [.5, 1, -.5], [-.5, 1, -.5]]], [[0, -1, 0], [[-.5, 0, -.5], [.5, 0, -.5], [.5, 0, .5], [-.5, 0, .5]]],
  ];
  const pos = [], nor = [];
  for (const [n, c] of f) for (const i of [0, 1, 2, 0, 2, 3]) { pos.push(...c[i]); nor.push(...n); }
  return { pos: new Float32Array(pos), nor: new Float32Array(nor) };
}

export class Renderer {
  constructor(canvas, opts = {}) {
    this.canvas = canvas; this.lite = !!opts.lite;
    const gl = canvas.getContext("webgl", { antialias: !this.lite, alpha: false, preserveDrawingBuffer: !!opts.preserve });
    if (!gl) throw new Error("WebGL indisponível");
    this.gl = gl; this.meshes = []; this.lines = []; this.lineSets = []; this.pulses = []; this.dirty = true; this.K = opts.K || 40; this.maxDist = opts.maxDist || 600;
    this.bg = [0.020, 0.028, 0.070];
    this.cam = { tx: 0, ty: 0, tz: 10, dist: 230, yaw: 0.5, pitch: 0.78 };
    this.p = this._prog(VS, FS); this.lp = this._prog(LVS, LFS);
    const c = cube(); this.vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.vb); gl.bufferData(gl.ARRAY_BUFFER, c.pos, gl.STATIC_DRAW);
    this.nb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, this.nb); gl.bufferData(gl.ARRAY_BUFFER, c.nor, gl.STATIC_DRAW);
    this.lb = gl.createBuffer();
    this.U = {}; for (const u of ["uVP", "uOff", "uScale", "uColor", "uEmissive", "uWin", "uGrid", "uAlpha", "uFog", "uEye", "uWinColor", "uEdge", "uTex", "uUseTex", "uK", "uFogK"]) this.U[u] = gl.getUniformLocation(this.p, u);
    this.LU = { uVP: gl.getUniformLocation(this.lp, "uVP"), uColor: gl.getUniformLocation(this.lp, "uColor") };
    this._controls(); this._resize();
    new ResizeObserver(() => this._resize()).observe(canvas);
    const loop = t => { this._frame(t); this.raf = requestAnimationFrame(loop); }; this.raf = requestAnimationFrame(loop);
  }
  _prog(vs, fs) {
    const gl = this.gl, mk = (t, s) => { const sh = gl.createShader(t); gl.shaderSource(sh, s); gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh)); return sh; };
    const p = gl.createProgram(); gl.attachShader(p, mk(gl.VERTEX_SHADER, vs)); gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); return p;
  }
  _resize() {
    const dpr = this.lite ? 1 : Math.min(window.devicePixelRatio || 1, 2);
    const w = this.canvas.clientWidth, h = this.canvas.clientHeight;
    this.canvas.width = Math.max(1, w * dpr); this.canvas.height = Math.max(1, h * dpr); this.dirty = true;
  }
  eye() { const c = this.cam, cp = Math.cos(c.pitch);
    return [c.tx + c.dist * cp * Math.sin(c.yaw), c.ty + c.dist * Math.sin(c.pitch), c.tz + c.dist * cp * Math.cos(c.yaw)]; }
  vp() { const a = this.canvas.width / this.canvas.height;
    const far = Math.max(2000, this.cam.dist * 8), near = Math.max(0.5, this.cam.dist / 400);
    return M.mul(M.persp(0.75, a, near, far), M.lookAt(this.eye(), [this.cam.tx, this.cam.ty, this.cam.tz], [0, 1, 0])); }
  add(m) { this.meshes.push(Object.assign({ pos: [0, 0, 0], scale: [1, 1, 1], color: [.5, .5, .5], emissive: 0, win: 0, grid: 0, alpha: 1, visible: true }, m)); this.dirty = true; return this.meshes[this.meshes.length - 1]; }
  clear() { this.meshes = []; this.lines = []; this.pulses = []; this.dirty = true; }
  // Linhas em lote: milhares de segmentos (fronteiras) num único desenho.
  addLineSet(key, floats, color) {
    const gl = this.gl; this.removeLineSet(key);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf); gl.bufferData(gl.ARRAY_BUFFER, floats, gl.STATIC_DRAW);
    this.lineSets.push({ key, buf, count: floats.length / 3, color, visible: true }); this.dirty = true;
  }
  removeLineSet(key) { const i = this.lineSets.findIndex(s => s.key === key); if (i >= 0) { this.gl.deleteBuffer(this.lineSets[i].buf); this.lineSets.splice(i, 1); this.dirty = true; } }
  setTexture(canvas) {
    const gl = this.gl; if (!this.tex) this.tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, this.tex); gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, canvas);
    gl.generateMipmap(gl.TEXTURE_2D); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE); this.dirty = true;
  }
  // Ponto do chão (y=0) sob o cursor → coordenadas do mundo.
  groundPoint(px, py) {
    const inv = M.inv(this.vp()); if (!inv) return null;
    const nx = px / this.canvas.clientWidth * 2 - 1, ny = 1 - py / this.canvas.clientHeight * 2;
    const a = M.xf(inv, [nx, ny, -1, 1]), b = M.xf(inv, [nx, ny, 1, 1]);
    const o = [a[0] / a[3], a[1] / a[3], a[2] / a[3]], e = [b[0] / b[3], b[1] / b[3], b[2] / b[3]];
    const dy = e[1] - o[1]; if (Math.abs(dy) < 1e-9) return null; const t = -o[1] / dy; if (t < 0) return null;
    return [o[0] + (e[0] - o[0]) * t, 0, o[2] + (e[2] - o[2]) * t];
  }
  pulse(from, to, color, dur = 1.4) { if (this.reduced) return; this.pulses.push({ from, to, color, t0: performance.now(), dur }); }
  project(p) {
    const v = M.xf(this._vp || this.vp(), [p[0], p[1], p[2], 1]); if (v[3] <= 0) return null;
    const x = v[0] / v[3], y = v[1] / v[3]; if (Math.abs(x) > 1.2 || Math.abs(y) > 1.2) return null;
    return { x: (x * .5 + .5) * this.canvas.clientWidth, y: (1 - (y * .5 + .5)) * this.canvas.clientHeight, depth: v[2] / v[3] };
  }
  pick(px, py) {
    const inv = M.inv(this.vp()); if (!inv) return null;
    const nx = px / this.canvas.clientWidth * 2 - 1, ny = 1 - py / this.canvas.clientHeight * 2;
    const a = M.xf(inv, [nx, ny, -1, 1]), b = M.xf(inv, [nx, ny, 1, 1]);
    const o = [a[0] / a[3], a[1] / a[3], a[2] / a[3]], e = [b[0] / b[3], b[1] / b[3], b[2] / b[3]], d = V.norm(V.sub(e, o));
    let best = null, bt = Infinity;
    for (const m of this.meshes) {
      if (!m.pick || !m.visible) continue;
      const mn = [m.pos[0] - m.scale[0] / 2, m.pos[1], m.pos[2] - m.scale[2] / 2], mx = [m.pos[0] + m.scale[0] / 2, m.pos[1] + m.scale[1], m.pos[2] + m.scale[2] / 2];
      let t0 = -Infinity, t1 = Infinity, hit = true;
      for (let i = 0; i < 3; i++) {
        if (Math.abs(d[i]) < 1e-9) { if (o[i] < mn[i] || o[i] > mx[i]) { hit = false; break; } continue; }
        let ta = (mn[i] - o[i]) / d[i], tb = (mx[i] - o[i]) / d[i]; if (ta > tb) [ta, tb] = [tb, ta];
        t0 = Math.max(t0, ta); t1 = Math.min(t1, tb); if (t0 > t1) { hit = false; break; }
      }
      if (hit && t0 > 0 && t0 < bt) { bt = t0; best = m; }
    }
    return best;
  }
  _controls() {
    const cv = this.canvas, ptrs = new Map(); let down = null, moved = 0, pinch = null;
    cv.addEventListener("contextmenu", e => e.preventDefault());
    cv.addEventListener("pointerdown", e => { cv.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, [e.clientX, e.clientY]); down = { x: e.clientX, y: e.clientY, b: e.button }; moved = 0; });
    cv.addEventListener("pointermove", e => {
      if (!ptrs.has(e.pointerId)) { this.onHover && this.onHover(e); return; }
      const prev = ptrs.get(e.pointerId); const dx = e.clientX - prev[0], dy = e.clientY - prev[1]; ptrs.set(e.pointerId, [e.clientX, e.clientY]);
      moved += Math.abs(dx) + Math.abs(dy);
      if (ptrs.size === 2) { const [p1, p2] = [...ptrs.values()]; const dd = Math.hypot(p1[0] - p2[0], p1[1] - p2[1]);
        if (pinch) this.cam.dist = Math.min(this.maxDist, Math.max(8, this.cam.dist * pinch / dd)); pinch = dd; }
      else if (down && (down.b === 2 || e.shiftKey)) { const s = this.cam.dist / 600, cy = Math.cos(this.cam.yaw), sy = Math.sin(this.cam.yaw);
        this.cam.tx -= (dx * cy + dy * sy) * s; this.cam.tz -= (-dx * sy + dy * cy) * s; }
      else { this.cam.yaw -= dx * 0.006; this.cam.pitch = Math.min(1.45, Math.max(0.18, this.cam.pitch + dy * 0.005)); }
      this.dirty = true; this.onCamera && this.onCamera();
    });
    const up = e => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch = null;
      if (down && moved < 6 && e.type === "pointerup") { const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
        this.onClick && this.onClick(this.pick(x, y), this.groundPoint(x, y)); }
      if (!ptrs.size) down = null; };
    cv.addEventListener("pointerup", up); cv.addEventListener("pointercancel", up);
    cv.addEventListener("wheel", e => { e.preventDefault(); this.cam.dist = Math.min(this.maxDist, Math.max(8, this.cam.dist * (e.deltaY > 0 ? 1.12 : 0.89))); this.dirty = true; this.onCamera && this.onCamera(); }, { passive: false });
  }
  _frame(t) {
    if (!this.dirty && !this.pulses.length && !this.animating) return;
    this.dirty = false; const gl = this.gl;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height); gl.clearColor(...this.bg, 1); gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.enable(gl.DEPTH_TEST); const vp = this.vp(); this._vp = vp; const eye = this.eye();
    gl.useProgram(this.p); const U = this.U;
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vb); let loc = gl.getAttribLocation(this.p, "aPos"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.nb); loc = gl.getAttribLocation(this.p, "aNor"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 0, 0);
    gl.uniformMatrix4fv(U.uVP, false, vp); gl.uniform3fv(U.uFog, this.bg); gl.uniform3fv(U.uEye, eye);
    gl.uniform1f(U.uK, this.K); gl.uniform1f(U.uFogK, 0.0042 * 230 / Math.max(this.cam.dist, 60)); gl.uniform1i(U.uTex, 0);
    if (this.tex) { gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.tex); }
    const draw = m => { gl.uniform3fv(U.uOff, m.pos); gl.uniform3fv(U.uScale, m.scale); gl.uniform3fv(U.uColor, m.color);
      gl.uniform1f(U.uEmissive, m.emissive); gl.uniform1f(U.uWin, this.lite ? 0 : m.win); gl.uniform3fv(U.uWinColor, m.winColor || [0.45, 0.85, 1.0]); gl.uniform1f(U.uEdge, this.lite ? 0 : (m.edge || 0)); gl.uniform1f(U.uUseTex, m.tex && this.tex ? 1 : 0); gl.uniform1f(U.uGrid, m.grid); gl.uniform1f(U.uAlpha, m.alpha); gl.drawArrays(gl.TRIANGLES, 0, 36); };
    const opaque = this.meshes.filter(m => m.visible && m.alpha >= 1), trans = this.meshes.filter(m => m.visible && m.alpha < 1);
    opaque.forEach(draw);
    const now = performance.now();
    this.pulses = this.pulses.filter(p => (now - p.t0) / 1000 < p.dur);
    const ps = Math.max(1.6, this.cam.dist / 220);  // pulso continua visível com a câmera longe
    for (const p of this.pulses) { const k = (now - p.t0) / 1000 / p.dur, len = Math.hypot(p.to[0] - p.from[0], p.to[2] - p.from[2]), h = Math.sin(k * Math.PI) * Math.max(10, len * 0.15);
      draw({ pos: [p.from[0] + (p.to[0] - p.from[0]) * k, p.from[1] + (p.to[1] - p.from[1]) * k + h, p.from[2] + (p.to[2] - p.from[2]) * k], scale: [ps, ps, ps], color: p.color, emissive: 1.4, win: 0, grid: 0, alpha: 1 }); }
    if (trans.length) { gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false); trans.forEach(draw); gl.depthMask(true); gl.disable(gl.BLEND); }
    if (this.lineSets.length) {
      gl.useProgram(this.lp); gl.uniformMatrix4fv(this.LU.uVP, false, vp); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      const l = gl.getAttribLocation(this.lp, "aPos"); gl.enableVertexAttribArray(l);
      for (const s of this.lineSets) { if (!s.visible) continue; gl.bindBuffer(gl.ARRAY_BUFFER, s.buf); gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 0, 0);
        gl.uniform4fv(this.LU.uColor, s.color); gl.drawArrays(gl.LINES, 0, s.count); }
      gl.disable(gl.BLEND);
    }
    if (this.lines.length) {
      gl.useProgram(this.lp); gl.uniformMatrix4fv(this.LU.uVP, false, vp); gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA, gl.ONE);
      gl.bindBuffer(gl.ARRAY_BUFFER, this.lb); const l = gl.getAttribLocation(this.lp, "aPos"); gl.enableVertexAttribArray(l);
      for (const ln of this.lines) { gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([...ln.a, ...ln.b]), gl.DYNAMIC_DRAW);
        gl.vertexAttribPointer(l, 3, gl.FLOAT, false, 0, 0); gl.uniform4fv(this.LU.uColor, ln.color); gl.drawArrays(gl.LINES, 0, 2); }
      gl.disable(gl.BLEND);
    }
    this.onFrame && this.onFrame();
  }
}
