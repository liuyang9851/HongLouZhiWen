/**
 * S32 · 3D 可视化模块（零依赖 WebGL）
 * =============================================================
 *
 * 为什么手写 WebGL 而不用 Three.js / 3d-force-graph
 * -----------------------------------------------------------------
 * 本项目前端是「纯原生 ESM + 零外部依赖」（index.html 只加载本地 js，
 * 无任何 CDN）。引入 Three.js(≈600KB) 或 3d-force-graph 会破坏这个前提，
 * 也会带来离线不可用的问题。
 *
 * 但 3D 力导向图的核心并不复杂：
 *   · 300~400 个点 + 1000 条以内的线，WebGL 单次 draw 完全够用
 *   · 需要在 GPU 上做的只是「模型视图投影」一个 4x4 矩阵
 *   · 物理迭代仍在 CPU（与 2D force 同构）
 *
 * 参考的成熟做法（形式借鉴，代码自写）：
 *   · vasturiano/3d-force-graph  : 3D 力导向的交互范式（拖拽旋转/滚轮缩放/节点拾取）
 *   · cosmograph                 : 大规模图的 GPU 渲染思路（点=quad，线=线段）
 *
 * 坐标约定
 * -----------------------------------------------------------------
 *   世界坐标 (x, y, z)，相机在 +z 方向看向原点，透视投影。
 *   屏幕 y 轴向下，所以投影后 y 取负。
 */

/* =============================================================
   1) 极简 mat4（只实现用到的：透视投影 + 相机旋转/平移/缩放）
   ============================================================= */
function mat4Identity() {
  return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]);
}

/** 透视投影：fov 弧度，aspect = w/h */
function mat4Perspective(fov, aspect, near, far) {
  const f = 1 / Math.tan(fov / 2);
  const nf = 1 / (near - far);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) * nf, -1,
    0, 0, 2 * far * near * nf, 0,
  ]);
}

/** 绕 X 轴旋转 */
function mat4RotX(a) {
  const c = Math.cos(a), s = Math.sin(a);
  return new Float32Array([1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]);
}

/** 绕 Y 轴旋转 */
function mat4RotY(a) {
  const c = Math.cos(a), s = Math.sin(a);
  return new Float32Array([c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]);
}

/** 平移 */
function mat4Translate(x, y, z) {
  return new Float32Array([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]);
}

/** 缩放 */
function mat4Scale(k) {
  return new Float32Array([k, 0, 0, 0, 0, k, 0, 0, 0, 0, k, 0, 0, 0, 0, 1]);
}

/** C = A * B（列主序，与 WebGL 一致） */
function mat4Mul(a, b) {
  const o = new Float32Array(16);
  for (let c = 0; c < 4; c++) {
    for (let r = 0; r < 4; r++) {
      o[c * 4 + r] =
        a[0 * 4 + r] * b[c * 4 + 0] +
        a[1 * 4 + r] * b[c * 4 + 1] +
        a[2 * 4 + r] * b[c * 4 + 2] +
        a[3 * 4 + r] * b[c * 4 + 3];
    }
  }
  return o;
}

/* =============================================================
   2) 着色器
   ============================================================= */
const VS_POINT = `
attribute vec3 a_pos;
attribute vec3 a_color;
attribute float a_size;
uniform mat4 u_mvp;
uniform float u_scale;      // 画布高度推导的像素缩放（解决点太小/太大的问题）
varying vec3 v_color;
varying float v_depth;
void main() {
  vec4 clip = u_mvp * vec4(a_pos, 1.0);
  gl_Position = clip;
  // 透视缩放：距离越远点越小；钳制在 [1.5, 54] 像素，避免消失或糊成一片
  float ps = a_size * u_scale / max(clip.w, 0.001);
  gl_PointSize = clamp(ps, 2.5, 64.0);
  v_color = a_color;
  v_depth = clip.w;
}`;

const FS_POINT = `
precision mediump float;
varying vec3 v_color;
varying float v_depth;
void main() {
  vec2 d = gl_PointCoord - vec2(0.5);
  float r = length(d);
  if (r > 0.5) discard;
  // 边缘柔化 + 轻微高光，让球体有体积感
  float alpha = smoothstep(0.5, 0.34, r);
  float hi = smoothstep(0.42, 0.0, length(d - vec2(-0.16, -0.16)));
  vec3 col = v_color + hi * 0.28;
  // 远处的点淡出（深度雾）
  float fog = clamp(1.0 - (v_depth - 1400.0) / 4200.0, 0.42, 1.0);
  gl_FragColor = vec4(col, alpha * fog);
}`;

const VS_LINE = `
attribute vec3 a_pos;
attribute vec3 a_color;
attribute float a_alpha;
uniform mat4 u_mvp;
varying vec3 v_color;
varying float v_alpha;
varying float v_depth;
void main() {
  vec4 clip = u_mvp * vec4(a_pos, 1.0);
  gl_Position = clip;
  v_color = a_color;
  v_alpha = a_alpha;
  v_depth = clip.w;
}`;

const FS_LINE = `
precision mediump float;
varying vec3 v_color;
varying float v_alpha;
varying float v_depth;
void main() {
  float fog = clamp(1.0 - (v_depth - 1400.0) / 4200.0, 0.26, 1.0);
  gl_FragColor = vec4(v_color, v_alpha * fog);
}`;

function compile(gl, type, src) {
  const sh = gl.createShader(type);
  gl.shaderSource(sh, src);
  gl.compileShader(sh);
  if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(sh);
    gl.deleteShader(sh);
    throw new Error('shader: ' + log);
  }
  return sh;
}

function makeProgram(gl, vs, fs) {
  const p = gl.createProgram();
  const v = compile(gl, gl.VERTEX_SHADER, vs);
  const f = compile(gl, gl.FRAGMENT_SHADER, fs);
  gl.attachShader(p, v);
  gl.attachShader(p, f);
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) {
    throw new Error('link: ' + gl.getProgramInfoLog(p));
  }
  gl.deleteShader(v);
  gl.deleteShader(f);
  return p;
}

/* =============================================================
   3) 颜色工具
   ============================================================= */
const FAM_COLOR = {
  贾: [0.86, 0.22, 0.16],   // 绛红
  史: [0.13, 0.36, 0.53],   // 靛蓝
  王: [0.58, 0.26, 0.76],   // 紫
  薛: [0.83, 0.62, 0.13],   // 金黄
  林: [0.16, 0.55, 0.38],   // 翠绿
  其他: [0.55, 0.52, 0.50],
};
const rgb = (name) => FAM_COLOR[name] || FAM_COLOR['其他'];

/* =============================================================
   4) 主入口：3D 力导向图
   ============================================================= */
/**
 * @param {HTMLCanvasElement} canvas
 * @param {{nodes:Array, edges:Array}} graph
 * @param {Object} opt  { onSelect(node), cooccur:Array }
 * @returns {Object} 实例（含 destroy / resetView）
 */
export function force3d(canvas, graph, opt = {}) {
  const gl = canvas.getContext('webgl', {
    antialias: true, alpha: true, premultipliedAlpha: false,
  });
  if (!gl) throw new Error('WebGL 不可用');

  const N = graph.nodes.length;

  /* ---------- 4.1 初始化节点 ----------
   * 不用「随机球面 + 力导向收敛」，因为 350 个节点的力导向需要数百帧才稳定，
   * 打开时先是空的一团；改用**可解释的确定性布局**：
   *   · 六个家族各占一个方位（绕 y 轴均匀分布）
   *   · 家族内部按度数从中心向外辐射
   *   · y 轴按「提及频次」分层，高频人物靠近中平面
   * 这样一打开就是有结构的形状，且物理只做轻微松弛。
   */
  const FAM_ORDER = ['贾', '王', '薛', '林', '史', '其他'];
  const famAngle = {};
  FAM_ORDER.forEach((f, i) => {
    famAngle[f] = (i / FAM_ORDER.length) * Math.PI * 2;
  });
  const R0 = 300 + N * 0.42;

  // 每个家族内的排序（度数降序 → 中心是核心人物）
  const byFam = {};
  for (const n of graph.nodes) {
    (byFam[n.family] = byFam[n.family] || []).push(n);
  }
  for (const f of Object.keys(byFam)) {
    byFam[f].sort((a, b) => (b.degree || 0) - (a.degree || 0));
  }
  const famPos = {};   // 家族 → 该家族当前已放几个
  const maxMention = Math.max(...graph.nodes.map((n) => n.mention || 0), 1);

  const nodes = graph.nodes.map((n, i) => {
    const fam = n.family || '其他';
    const baseAng = famAngle[fam] ?? 0;
    const list = byFam[fam] || [];
    const k = famPos[fam] = (famPos[fam] || 0) + 1;
    const total = list.length || 1;

    // 家族内：第 k 个 → 由内向外、并绕家族轴散布
    const ring = Math.cbrt(k / total);                 // 0→1（立方根：体积均匀）
    const spread = 0.55;                               // 家族扇区宽度（弧度）
    const jitter = ((k * 2.399963) % (Math.PI * 2)) - Math.PI;  // 黄金角抖动
    const ang = baseAng + jitter * 0.30 * ring;

    const radius = R0 * (0.34 + 0.66 * ring);
    // y 由提及频次决定：提及越多越靠中平面
    const mNorm = (n.mention || 0) / maxMention;
    const y = (0.5 - mNorm) * R0 * 0.55 + (jitter * 0.04) * R0;

    return {
      ...n,
      x: radius * Math.cos(ang),
      y,
      z: radius * Math.sin(ang),
      vx: 0, vy: 0, vz: 0,
      r: Math.max(2.2, Math.min(8.0, 2.2 + Math.sqrt(n.degree || 1) * 0.72)),
      color: rgb(fam),
    };
  });
  const idx = new Map(nodes.map((n, i) => [n.id, i]));

  const links = graph.edges
    .map((e) => ({ s: idx.get(e.source), t: idx.get(e.target), relation: e.relation }))
    .filter((e) => e.s !== undefined && e.t !== undefined);

  // 共现叠加（MapReduce cooccur）
  const coLinks = (opt.cooccur || [])
    .map((e) => ({ s: idx.get(e.source), t: idx.get(e.target), count: e.count }))
    .filter((e) => e.s !== undefined && e.t !== undefined);
  const CO_MAX = coLinks.length ? Math.max(...coLinks.map((c) => c.count)) : 1;

  /* ---------- 4.2 程序与缓冲区 ---------- */
  const progP = makeProgram(gl, VS_POINT, FS_POINT);
  const progL = makeProgram(gl, VS_LINE, FS_LINE);

  const bufPos = gl.createBuffer();
  const bufCol = gl.createBuffer();
  const bufSize = gl.createBuffer();

  const lPos = gl.createBuffer();
  const lCol = gl.createBuffer();
  const lAlpha = gl.createBuffer();

  gl.enable(gl.BLEND);
  gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

  /* ---------- 4.3 相机 ---------- */
  const cam = {
    yaw: 0.6, pitch: 0.34,
    dist: R0 * 3.1,
    target: [0, 0, 0],
  };

  /* ---------- 4.4 物理参数（与 2D 同构，按节点数自适应） ---------- */
  const REPULSION = N > 250 ? 1400 : (N > 150 ? 1100 : 800);
  const SPRING = N > 250 ? 0.006 : 0.01;
  const REST = N > 250 ? 96 : 72;
  const DAMP = 0.82;
  const Z_GRAVITY = 0.0016;   // 轻微向中心平面收拢，避免过于球状

  let alpha = 0.35;
  let raf = 0;
  let alive = true;

  /* ---------- 4.5 交互 ---------- */
  let drag = null;
  let hovered = -1;
  let selected = -1;
  let spin = 0.0018;          // 自动缓慢自转（可被拖拽暂停）

  const DPR = Math.min(window.devicePixelRatio || 1, 2);

  function resize() {
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 520;
    canvas.width = Math.round(w * DPR);
    canvas.height = Math.round(h * DPR);
    gl.viewport(0, 0, canvas.width, canvas.height);
  }

  /** 屏幕坐标 → 世界世界射线，用于节点拾取 */
  function project() {
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 520;
    const proj = mat4Perspective(Math.PI / 4, w / h, 1, 20000);
    // 视图矩阵：先绕原点旋转（相机看向原点），再沿视线后退 dist
    // 注意顺序：mat4Mul(A,B) 表示先应用 B 再应用 A
    // 顺序很关键：先旋转（相机绕原点转），再沿 z 后退。
    // 写成矩阵链即 Translate * RotX * RotY（最左最先作用于顶点）。
    let view = mat4RotY(-cam.yaw);
    view = mat4Mul(mat4RotX(-cam.pitch), view);
    view = mat4Mul(mat4Translate(0, 0, -cam.dist), view);
    const mvp = mat4Mul(proj, view);

    const out = new Array(N);
    for (let i = 0; i < N; i++) {
      const n = nodes[i];
      const m = mvp;
      const cx = m[0] * n.x + m[4] * n.y + m[8] * n.z + m[12];
      const cy = m[1] * n.x + m[5] * n.y + m[9] * n.z + m[13];
      const cw = m[3] * n.x + m[7] * n.y + m[11] * n.z + m[15];
      out[i] = {
        sx: (cx / cw * 0.5 + 0.5) * w,
        // WebGL 的 NDC y 向上，屏幕 y 向下 → 取反
        sy: (1 - (cy / cw * 0.5 + 0.5)) * h,
        depth: cw,
      };
    }
    return { mvp, out, w, h };
  }

  function pick(mx, my) {
    const { out } = project();
    let best = -1;
    let bestD = 1e9;
    for (let i = 0; i < N; i++) {
      const p = out[i];
      if (p.depth <= 0) continue;
      const dx = p.sx - mx;
      const dy = p.sy - my;
      const d2 = dx * dx + dy * dy;
      // 拾取半径随景深放大（近处更好点）
      const rr = Math.max(14, 260 / p.depth * 6);
      if (d2 < rr * rr && p.depth < bestD) {
        bestD = p.depth;
        best = i;
      }
    }
    return best;
  }

  /* ---------- 4.6 物理迭代 ---------- */
  function tick() {
    if (alpha < 0.004) return;

    // 斥力（O(N²)，N=350 时可接受；每帧 ~6 万次）
    for (let i = 0; i < N; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < N; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
        let d2 = dx * dx + dy * dy + dz * dz;
        if (d2 < 1) d2 = 1;
        const d = Math.sqrt(d2);
        const f = REPULSION / d2;
        const fx = (dx / d) * f, fy = (dy / d) * f, fz = (dz / d) * f;
        a.vx -= fx; a.vy -= fy; a.vz -= fz;
        b.vx += fx; b.vy += fy; b.vz += fz;
      }
    }

    // 弹簧
    for (const L of links) {
      const a = nodes[L.s], b = nodes[L.t];
      const dx = b.x - a.x, dy = b.y - a.y, dz = b.z - a.z;
      const d = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const f = (d - REST) * SPRING;
      const fx = (dx / d) * f, fy = (dy / d) * f, fz = (dz / d) * f;
      a.vx += fx; a.vy += fy; a.vz += fz;
      b.vx -= fx; b.vy -= fy; b.vz -= fz;
    }

    // 向心力（防止整体飘散）+ 阻尼 + 位置更新
    for (const n of nodes) {
      n.vx -= n.x * 0.0016;
      n.vy -= n.y * 0.0016;
      n.vz -= n.z * (0.0016 + Z_GRAVITY);

      n.vx *= DAMP; n.vy *= DAMP; n.vz *= DAMP;
      n.x += n.vx * alpha;
      n.y += n.vy * alpha;
      n.z += n.vz * alpha;
    }

    alpha *= 0.965;
  }

  /* ---------- 4.7 渲染 ---------- */
  function render() {
    const w = canvas.clientWidth || 800;
    const h = canvas.clientHeight || 520;

    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    const { mvp, out } = project();

    /* --- 线 --- */
    const linePos = [];
    const lineCol = [];
    const lineAl = [];

    // 共现边（细、暖色、半透明）
    for (const L of coLinks) {
      const a = nodes[L.s], b = nodes[L.t];
      linePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      const c = [0.85, 0.55, 0.25];
      lineCol.push(c[0], c[1], c[2], c[0], c[1], c[2]);
      const al = 0.16 + (L.count / CO_MAX) * 0.34;
      lineAl.push(al, al);
    }
    // 语义边（冷灰色）
    for (const L of links) {
      const a = nodes[L.s], b = nodes[L.t];
      const hi = (selected === L.s || selected === L.t);
      linePos.push(a.x, a.y, a.z, b.x, b.y, b.z);
      const c = hi ? [0.95, 0.40, 0.28] : [0.42, 0.50, 0.58];
      lineCol.push(c[0], c[1], c[2], c[0], c[1], c[2]);
      const al = hi ? 0.95 : 0.42;
      lineAl.push(al, al);
    }

    if (linePos.length) {
      gl.useProgram(progL);
      gl.uniformMatrix4fv(gl.getUniformLocation(progL, 'u_mvp'), false, mvp);

      gl.bindBuffer(gl.ARRAY_BUFFER, lPos);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(linePos), gl.DYNAMIC_DRAW);
      const aPos = gl.getAttribLocation(progL, 'a_pos');
      gl.enableVertexAttribArray(aPos);
      gl.vertexAttribPointer(aPos, 3, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, lCol);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(lineCol), gl.DYNAMIC_DRAW);
      const aCol = gl.getAttribLocation(progL, 'a_color');
      gl.enableVertexAttribArray(aCol);
      gl.vertexAttribPointer(aCol, 3, gl.FLOAT, false, 0, 0);

      gl.bindBuffer(gl.ARRAY_BUFFER, lAlpha);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(lineAl), gl.DYNAMIC_DRAW);
      const aAl = gl.getAttribLocation(progL, 'a_alpha');
      gl.enableVertexAttribArray(aAl);
      gl.vertexAttribPointer(aAl, 1, gl.FLOAT, false, 0, 0);

      gl.lineWidth(1.6);
      gl.drawArrays(gl.LINES, 0, linePos.length / 3);
    }

    /* --- 点 --- */
    const pos = new Float32Array(N * 3);
    const col = new Float32Array(N * 3);
    const siz = new Float32Array(N);

    // 按景深排序（远→近），让近的点覆盖远的（WebGL 无逐点深度测试）
    const order = Array.from({ length: N }, (_, i) => i).sort((a, b) => out[b].depth - out[a].depth);

    for (let k = 0; k < N; k++) {
      const i = order[k];
      const n = nodes[i];
      pos[k * 3] = n.x; pos[k * 3 + 1] = n.y; pos[k * 3 + 2] = n.z;

      let c = n.color;
      let mul = 1;
      if (i === selected) { c = [0.95, 0.30, 0.22]; mul = 1.9; }
      else if (i === hovered) { mul = 1.5; }
      else if (selected >= 0) {
        // 选中时非邻居降亮
        const isNb = links.some(
          (L) => (L.s === selected && L.t === i) || (L.t === selected && L.s === i)
        );
        mul = isNb ? 1.15 : 0.42;
      }
      col[k * 3] = c[0] * mul;
      col[k * 3 + 1] = c[1] * mul;
      col[k * 3 + 2] = c[2] * mul;
      siz[k] = n.r;
    }

    gl.useProgram(progP);
    gl.uniformMatrix4fv(gl.getUniformLocation(progP, 'u_mvp'), false, mvp);
    // u_scale = 画布高度 × 系数：让「半径 4」的节点在默认视距下约 10px
    const uScaleLoc = gl.getUniformLocation(progP, 'u_scale');
    gl.uniform1f(uScaleLoc, canvas.height * 7.0);

    gl.bindBuffer(gl.ARRAY_BUFFER, bufPos);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.DYNAMIC_DRAW);
    let ap = gl.getAttribLocation(progP, 'a_pos');
    gl.enableVertexAttribArray(ap);
    gl.vertexAttribPointer(ap, 3, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, bufCol);
    gl.bufferData(gl.ARRAY_BUFFER, col, gl.DYNAMIC_DRAW);
    let ac = gl.getAttribLocation(progP, 'a_color');
    gl.enableVertexAttribArray(ac);
    gl.vertexAttribPointer(ac, 3, gl.FLOAT, false, 0, 0);

    gl.bindBuffer(gl.ARRAY_BUFFER, bufSize);
    gl.bufferData(gl.ARRAY_BUFFER, siz, gl.DYNAMIC_DRAW);
    let as = gl.getAttribLocation(progP, 'a_size');
    gl.enableVertexAttribArray(as);
    gl.vertexAttribPointer(as, 1, gl.FLOAT, false, 0, 0);

    gl.drawArrays(gl.POINTS, 0, N);
  }

  /* ---------- 4.8 主循环 ---------- */
  let frame = 0;
  let autoFit = true;      // 首帧后自动适配视距（用户一旦滚轮就关闭）

  /** 计算节点包围球半径 */
  function boundsRadius() {
    let m = 0;
    for (const n of nodes) {
      const d = Math.sqrt(n.x * n.x + n.y * n.y + n.z * n.z);
      if (d > m) m = d;
    }
    return m || 1;
  }

  function loop() {
    if (!alive) return;
    frame++;
    // 物理每 2 帧一次，渲染每帧（省 CPU）
    if (frame % 2 === 0) tick();
    // 持续把视距贴到当前包围球上（物理收敛时 R 会变小，必须跟随）
    // 用户一旦滚轮缩放就关闭，交给手动控制
    if (autoFit) {
      const r = boundsRadius();
      // 垂直半视角 fov/2 = 22.5° → sin = 0.3827
      // 要让半径 r 的球完整落在纵向视野内：dist >= r / sin(halfFov) * 余量
      const halfFov = Math.PI / 8;           // fov=45° 的一半
      // 1.52 余量：透视下球面近端还会往外扩，1.28 实测仍溢出 7%
      const need = r / Math.sin(halfFov) * 1.05;
      cam.dist = Math.max(need, 260);
    }
    if (!drag && alpha > 0.01) cam.yaw += spin * alpha;
    render();
    raf = requestAnimationFrame(loop);
  }

  /* ---------- 4.9 事件 ---------- */
  function onDown(e) {
    const r = canvas.getBoundingClientRect();
    drag = {
      mx: e.clientX - r.left, my: e.clientY - r.top,
      yaw: cam.yaw, pitch: cam.pitch,
      moved: false,
    };
    canvas.style.cursor = 'grabbing';
  }

  function onMove(e) {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;

    if (drag) {
      const dx = mx - drag.mx;
      const dy = my - drag.my;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      cam.yaw = drag.yaw + dx * 0.006;
      cam.pitch = Math.max(-1.45, Math.min(1.45, drag.pitch + dy * 0.006));
      return;
    }
    // hover 拾取（限制频率：每 3 帧一次）
    if (frame % 3 === 0) {
      hovered = pick(mx, my);
      canvas.style.cursor = hovered >= 0 ? 'pointer' : 'grab';
    }
  }

  function onUp(e) {
    if (drag && !drag.moved) {
      const r = canvas.getBoundingClientRect();
      const i = pick(e.clientX - r.left, e.clientY - r.top);
      selected = (i === selected) ? -1 : i;
      if (selected >= 0 && typeof opt.onSelect === 'function') {
        opt.onSelect(nodes[selected]);
      } else if (selected < 0 && typeof opt.onSelect === 'function') {
        opt.onSelect(null);
      }
    }
    drag = null;
    canvas.style.cursor = 'grab';
  }

  function onWheel(e) {
    e.preventDefault();
    autoFit = false;
    cam.dist = Math.max(160, Math.min(6000, cam.dist * (1 + Math.sign(e.deltaY) * 0.09)));
  }

  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });

  resize();
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);

  loop();

  /* ---------- 4.10 实例方法 ---------- */
  return {
    destroy() {
      alive = false;
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('wheel', onWheel);
    },
    resetView() {
      cam.yaw = 0.6; cam.pitch = 0.34;
      selected = -1; hovered = -1;
      alpha = 1;
      autoFit = true;
      frame = 0;
    },
    reheat() { alpha = 0.9; },
    camera: cam,
  };
}

/* =============================================================
   5) 3D 柱状「回目地形」——把 80 回的规模画成立体楼梯
   =============================================================
   参考形式：3D bar chart（无需依赖，用最简画家算法 + 透视）
   用途：一眼看出全书各回的篇幅起伏，比 2D 柱状更有空间感
   ============================================================= */
export function surface3d(canvas, data, opt = {}) {
  const ctx = canvas.getContext('2d');
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  let W = 0, H = 0;

  // data: { huis:[1..80], persons:[{name, series:[...]}], max_val }
  const huis = data.huis || [];
  const persons = data.persons || [];
  const NX = huis.length;
  const NY = persons.length;
  const MAXV = data.max_val || 1;

  const HMAX = 1.35;                      // 柱体最大高度（与 draw 里一致）
  const cam = { yaw: -0.55, pitch: 0.78, zoom: 1.0, m: null };
  let drag = null;
  let hover = null;          // {px, py, person, hui, val}

  function resize() {
    const par = canvas.parentElement;
    W = canvas.clientWidth || par?.clientWidth || 900;
    H = canvas.clientHeight || par?.clientHeight || 460;
    if (W < 60) W = 900;
    if (H < 60) H = 460;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    draw();
  }

  /**
   * 按当前视角计算所有顶点的屏幕包围盒，返回需要的缩放系数。
   * 视角自由旋转后，同一个形体在不同角度下的投影范围差别很大，
   * 用固定系数必然在某些角度溢出 → 每次重绘都按包围盒自适应。
   */
  /**
   * 刚体旋转（不含缩放）：返回旋转后的 (X, Y, Z)
   *   wy 归一化到 [0,1] → 居中得到 y0 ∈ [-0.5, 0.5]
   */
  function rot(wx, wy, wz) {
    const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
    const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
    const y0 = wy / HMAX - 0.5;
    // ① 绕 Y 轴
    const X1 = wx * cy + wz * sy;
    const Z1 = -wx * sy + wz * cy;
    // ② 绕 X 轴
    const Y2 = y0 * cp - Z1 * sp;
    const Z2 = y0 * sp + Z1 * cp;
    return { X: X1, Y: Y2, Z: Z2 };
  }

  /**
   * 按当前视角计算旋转后形体的像素跨度（只做一次，用于自适应缩放）
   * 这里**不含透视**——透视只影响深度感的强弱，不该影响「画多大」。
   */
  function measure() {
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9;
    // 地面（wy=0）的跨度：决定水平方向需要多宽
    let gMinX = 1e9, gMaxX = -1e9;
    for (const wx of [-1, 1]) {
      for (const wz of [-1, 1]) {
        const r = rot(wx, 0, wz);
        if (r.X < gMinX) gMinX = r.X;
        if (r.X > gMaxX) gMaxX = r.X;
      }
    }
    for (const wx of [-1, 1]) {
      for (const wy of [0, HMAX]) {
        for (const wz of [-1, 1]) {
          const r = rot(wx, wy, wz);
          if (r.X < minX) minX = r.X;
          if (r.X > maxX) maxX = r.X;
          if (r.Y < minY) minY = r.Y;
          if (r.Y > maxY) maxY = r.Y;
        }
      }
    }
    // 透视会让近端（Z<0）放大到 1/(1-0.22) ≈ 1.28 倍，
    // 这里按最大放大倍数预留，避免近端溢出
    const PERSP_MAX = 1 / (1 - 0.22);
    return {
      spanX: ((maxX - minX) || 1) * PERSP_MAX,
      spanY: ((maxY - minY) || 1) * PERSP_MAX,
      // 地面宽度：用它决定水平占满，避免被柱高"抢占"缩放
      groundX: ((gMaxX - gMinX) || 1) * PERSP_MAX,
      cx: (maxX + minX) / 2,
      cy: (maxY + minY) / 2,
    };
  }

  /** 世界 → 屏幕：旋转 → 自适应缩放 → 透视微调 */
  function proj(wx, wy, wz) {
    const r = rot(wx, wy, wz);

    // 自适应缩放：把旋转后的跨度映射到画布的 86% / 80%
    const m = cam.m;
    const persp = 1 / (1 + r.Z * 0.22);      // 轻微透视，仅增强立体感
    // 水平方向：让「地面宽度」占满 94% 画布（柱高不参与，否则会被压得很小）
    // 垂直方向：只做不溢出保护
    const kW = W * 0.94 / m.groundX;
    const kH = H * 0.92 / m.spanY;
    const k = Math.min(kW, kH);

    return {
      x: W * 0.5 + (r.X - m.cx) * k * cam.zoom * persp,
      y: H * 0.52 - (r.Y - m.cy) * k * cam.zoom * persp,
      depth: r.Z,
      persp,
    };
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    // 每次重绘先量当前视角下的跨度（视角自由旋转后必须重新量）
    cam.m = measure();

    const CELL = 1.0;
    const bw = 0.42;

    // 1) 组装所有柱体多边形
    const polys = [];
    for (let pi = 0; pi < NY; pi++) {
      const p = persons[pi];
      for (let hi = 0; hi < NX; hi++) {
        const v = p.series[hi] || 0;
        if (!v) continue;
        const wx = (hi - (NX - 1) / 2) / (NX / 2);        // -1..1
        const wz = (pi - (NY - 1) / 2) / (NY / 2);        // -1..1
        const hNorm = Math.pow(v / MAXV, 0.78);           // 幂次压缩，小值也可见
        const h = hNorm * 1.35;

        const dx = (bw / (NX / 2)) * 0.5;
        const dz = (bw / (NY / 2)) * 0.5;

        const A  = proj(wx - dx, 0, wz - dz);
        const B  = proj(wx + dx, 0, wz - dz);
        const C  = proj(wx + dx, 0, wz + dz);
        const D  = proj(wx - dx, 0, wz + dz);
        const A2 = proj(wx - dx, h, wz - dz);
        const B2 = proj(wx + dx, h, wz - dz);
        const C2 = proj(wx + dx, h, wz + dz);
        const D2 = proj(wx - dx, h, wz + dz);

        // 颜色：高度 → 色相（低=青灰，高=绛红）
        const t = v / MAXV;
        const col = heatColor(t);

        const sides = [
          { pts: [D, C, C2, D2], depth: (D.depth + C.depth) / 2, shade: 1.00 },
          { pts: [C, B, B2, C2], depth: (C.depth + B.depth) / 2, shade: 0.78 },
          { pts: [A, D, D2, A2], depth: (A.depth + D.depth) / 2, shade: 0.62 },
          { pts: [B, A, A2, B2], depth: (B.depth + A.depth) / 2, shade: 0.50 },
        ];
        for (const sd of sides) {
          polys.push({
            pts: sd.pts, depth: sd.depth - 0.001, shade: sd.shade,
            col, pi, hi, v, kind: 'side',
          });
        }
        polys.push({
          pts: [A2, B2, C2, D2],
          depth: (A2.depth + C2.depth) / 2 + 0.002,
          shade: 1.30, col, pi, hi, v, kind: 'top',
        });
      }
    }

    // 2) 画家算法
    polys.sort((a, b) => b.depth - a.depth);

    for (const q of polys) {
      ctx.beginPath();
      ctx.moveTo(q.pts[0].x, q.pts[0].y);
      for (let k = 1; k < q.pts.length; k++) ctx.lineTo(q.pts[k].x, q.pts[k].y);
      ctx.closePath();
      const c = q.col;
      const r = Math.min(255, c[0] * q.shade);
      const g = Math.min(255, c[1] * q.shade);
      const b = Math.min(255, c[2] * q.shade);
      const hi = hover && hover.pi === q.pi && hover.hi === q.hi;
      ctx.fillStyle = hi
        ? 'rgba(255,90,60,0.98)'
        : `rgba(${r | 0},${g | 0},${b | 0},0.92)`;
      ctx.fill();
      if (q.kind === 'top' && !hi) {
        ctx.strokeStyle = `rgba(255,255,255,${0.10 + 0.22 * (q.v / MAXV)})`;
        ctx.lineWidth = 0.5;
        ctx.stroke();
      }
    }

    // 3) 地面网格（回目轴 + 人物轴）
    drawFloor();

    // 4) 轴标
    ctx.font = '9px ui-sans-serif, system-ui, sans-serif';
    ctx.fillStyle = 'rgba(110,125,138,0.75)';
    ctx.textAlign = 'center';
    for (const h of [1, 20, 40, 60, 80]) {
      const hi = huis.indexOf(h);
      if (hi < 0) continue;
      const wx = (hi - (NX - 1) / 2) / (NX / 2);
      const pt = proj(wx, 0, -1.06);
      ctx.fillText(`第${h}回`, pt.x, pt.y + 14);
    }
    // 人物轴：每隔几人标一个
    ctx.textAlign = 'right';
    for (let pi = 0; pi < NY; pi += Math.max(1, Math.floor(NY / 6))) {
      const wz = (pi - (NY - 1) / 2) / (NY / 2);
      const pt = proj(-1.06, 0, wz);
      ctx.fillText(persons[pi].name, pt.x - 4, pt.y + 3);
    }
    ctx.textAlign = 'start';

    // 5) hover 提示
    if (hover) {
      const txt = `${hover.person} · 第${hover.hui}回 · ${hover.val} 次`;
      ctx.font = '600 11px ui-sans-serif, system-ui, sans-serif';
      const tw = ctx.measureText(txt).width;
      const px = Math.max(6, Math.min(W - tw - 26, hover.px - tw / 2));
      ctx.fillStyle = 'rgba(28,36,44,0.90)';
      ctx.beginPath();
      ctx.roundRect(px - 9, 10, tw + 18, 22, 6);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.fillText(txt, px, 25);
    }
  }

  /** 高度 → 颜色（青灰 → 金 → 绛红） */
  function heatColor(t) {
    // 三段插值
    const stops = [
      [0.00, [ 96, 132, 148]],
      [0.42, [214, 168,  62]],
      [0.72, [196,  96,  54]],
      [1.00, [150,  30,  26]],
    ];
    for (let i = 0; i < stops.length - 1; i++) {
      const [t0, c0] = stops[i];
      const [t1, c1] = stops[i + 1];
      if (t <= t1 || i === stops.length - 2) {
        const k = Math.max(0, Math.min(1, (t - t0) / (t1 - t0 || 1)));
        return [
          c0[0] + (c1[0] - c0[0]) * k,
          c0[1] + (c1[1] - c0[1]) * k,
          c0[2] + (c1[2] - c0[2]) * k,
        ];
      }
    }
    return stops[0][1];
  }

  function drawFloor() {
    const N = 5;
    ctx.strokeStyle = 'rgba(47,72,88,0.10)';
    ctx.lineWidth = 0.7;
    for (let i = 0; i <= N; i++) {
      const t = (i / N) * 2 - 1;
      // 沿 wx 的线
      let a = proj(-1, 0, t), b = proj(1, 0, t);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      // 沿 wz 的线
      a = proj(t, 0, -1); b = proj(t, 0, 1);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
    }
  }

  /** 屏幕 → 最近柱体（按投影中心距离） */
  function pick(mx, my) {
    let best = null, bestD = 1e9;
    for (let pi = 0; pi < NY; pi++) {
      const p = persons[pi];
      for (let hi = 0; hi < NX; hi++) {
        const v = p.series[hi] || 0;
        if (!v) continue;
        const wx = (hi - (NX - 1) / 2) / (NX / 2);
        const wz = (pi - (NY - 1) / 2) / (NY / 2);
        const hNorm = Math.pow(v / MAXV, 0.62) * 1.9;
        const pt = proj(wx, hNorm * 0.5, wz);
        const d = (pt.x - mx) ** 2 + (pt.y - my) ** 2;
        if (d < bestD) { bestD = d; best = { pi, hi, person: p.name, hui: huis[hi], val: v, px: pt.x, py: pt.y }; }
      }
    }
    return bestD < 900 ? best : null;
  }

  function onMove(e) {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (drag) {
      cam.yaw = drag.yaw + (mx - drag.mx) * 0.007;
      // 不限制俯仰角：允许 -π/2 ~ π/2 全范围（俯视到平视），
      // 超出会翻转，但对探索式查看是有用的
      cam.pitch = drag.pitch + (my - drag.my) * 0.006;
      draw();
      return;
    }
    const h = pick(mx, my);
    const changed = (!!h !== !!hover) || (h && hover && (h.pi !== hover.pi || h.hi !== hover.hi));
    hover = h;
    if (changed) draw();
    canvas.style.cursor = h ? 'pointer' : 'grab';
  }

  function onDown(e) {
    const r = canvas.getBoundingClientRect();
    drag = { mx: e.clientX - r.left, my: e.clientY - r.top, yaw: cam.yaw, pitch: cam.pitch };
    canvas.style.cursor = 'grabbing';
  }
  function onUp() { drag = null; canvas.style.cursor = 'grab'; }
  function onWheel(e) {
    e.preventDefault();
    cam.zoom = Math.max(0.5, Math.min(2.3, cam.zoom * (1 - Math.sign(e.deltaY) * 0.08)));
    draw();
  }

  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  if (canvas.parentElement) ro.observe(canvas.parentElement);
  resize();
  requestAnimationFrame(() => resize());
  setTimeout(resize, 120);

  return {
    destroy() {
      ro.disconnect();
      canvas.removeEventListener('pointerdown', onDown);
      canvas.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      canvas.removeEventListener('wheel', onWheel);
    },
    resetView() { cam.yaw = -0.55; cam.pitch = 0.78; cam.zoom = 1.0; draw(); },
  };
}

/* =============================================================
   5) 和弦图：家族 × 家族 关系强度
   -------------------------------------------------------------
   调用：chord(canvas, M, fams, opt)
     M     N×N 对称矩阵，M[i][j] = 家族 i 与 j 的关系条数
           （对角线 M[i][i] = 族内关系条数，仅计一次）
     fams  家族名数组，长度 N
   返回：{ destroy() }  —— 与 force()/force3d() 接口一致

   设计：
     · 外环按「该家族的关系总数」分配角度
     · 弦带宽度 ∝ 两家关系条数
     · 对角线（族内关系）画成内侧细弧
     · 悬停扇区高亮该家族，其余淡出
   ============================================================= */
export function chord(canvas, M, fams, opt = {}) {
  const DPR = Math.min(window.devicePixelRatio || 1, 2);
  const COLORS = {
    贾: '#b03a2e', 史: '#2f4858', 王: '#7b4b94',
    薛: '#a3802c', 林: '#3f6a55', 其他: '#78716c',
  };
  const colorOf = (f) => COLORS[f] || COLORS['其他'];
  const n = fams.length;

  let ctx = canvas.getContext('2d');
  let raf = 0;
  let hover = -1;
  let geo = null;      // 布局缓存

  function sizeCanvas() {
    const p = canvas.parentElement;
    const w = canvas.clientWidth || (p && p.clientWidth) || 720;
    const h = canvas.clientHeight || (p && p.clientHeight) || 520;
    canvas.width = Math.round(w * DPR);
    canvas.height = Math.round(h * DPR);
    const rect = canvas.getBoundingClientRect();
    return {
      w: w, h: h,
      ox: canvas.width / 2,
      oy: canvas.height / 2,
      wpx: rect.width || w,
      hpx: rect.height || h,
    };
  }

  // ---- 布局：把每个家族映射到一段角度 ----
  function layout() {
    const S = sizeCanvas();
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, S.w, S.h);

    // 各家关系总数（行和）
    const totals = fams.map((_, i) => {
      let s = M[i][i] || 0;
      for (let j = 0; j < n; j++) if (j !== i) s += M[i][j] || 0;
      return s;
    });
    const sumAll = totals.reduce((a, b) => a + b, 0) || 1;

    const cx = S.w / 2, cy = S.h / 2;
    const R = Math.max(80, Math.min(S.w, S.h) / 2 - 62);   // 外环外径
    const ringW = Math.max(14, Math.min(26, R * 0.11));     // 环带厚度
    const inner = R - ringW;

    const GAP = 0.022;                       // 扇区之间留白（弧度）
    const totalGap = GAP * n;
    const usable = Math.PI * 2 - totalGap;

    let a = -Math.PI / 2;                    // 从正上方开始
    const spans = [];
    for (let i = 0; i < n; i++) {
      const frac = totals[i] / sumAll;
      const span = Math.max(0.02, usable * frac);
      spans.push({ a0: a, a1: a + span, mid: a + span / 2, total: totals[i] });
      a += span + GAP;
    }

    // 每个家族扇区内部再细分：族内 + 分别对外的子区间
    for (let i = 0; i < n; i++) {
      const sp = spans[i];
      const width = sp.a1 - sp.a0;
      const t = totals[i] || 1;
      let acc = sp.a0;
      sp.slots = new Array(n).fill(null);
      for (let j = 0; j < n; j++) {
        const v = M[i][j] || 0;
        if (v <= 0) continue;
        const w = width * (v / t);
        sp.slots[j] = { a0: acc, a1: acc + w };
        acc += w;
      }
    }
    return { S, cx, cy, R, ringW, inner, spans };
  }

  // ---- 画一条弦带（二次贝塞尔近似）----
  function ribbon(cx, cy, r, s1, s2, alpha) {
    const p = (r_, ang) => [cx + Math.cos(ang) * r_, cy + Math.sin(ang) * r_];
    const [x1, y1] = p(r, s1.a0);
    const [x2, y2] = p(r, s1.a1);
    const [x3, y3] = p(r, s2.a0);
    const [x4, y4] = p(r, s2.a1);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.bezierCurveTo(cx, cy, cx, cy, x3, y3);
    ctx.lineTo(x4, y4);
    ctx.bezierCurveTo(cx, cy, cx, cy, x2, y2);
    ctx.closePath();
    ctx.globalAlpha = alpha;
    ctx.fill();
    ctx.globalAlpha = 1;
  }

  function draw() {
    if (!geo) geo = layout();
    const { S, cx, cy, R, ringW, inner, spans } = geo;
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    ctx.clearRect(0, 0, S.w, S.h);

    // 1) 弦带（先画，压在外环下面）
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const v = M[i][j] || 0;
        if (v <= 0) continue;
        const s1 = spans[i].slots[j];
        const s2 = spans[j].slots[i];
        if (!s1 || !s2) continue;
        const dim = hover >= 0 && hover !== i && hover !== j;
        ctx.fillStyle = colorOf(fams[i]);
        ribbon(cx, cy, inner, s1, s2, dim ? 0.05 : 0.34);
      }
    }

    // 2) 族内关系（内侧细弧）
    for (let i = 0; i < n; i++) {
      const s = spans[i].slots[i];
      if (!s || (M[i][i] || 0) <= 0) continue;
      const dim = hover >= 0 && hover !== i;
      ctx.beginPath();
      ctx.arc(cx, cy, inner - 12, s.a0, s.a1);
      ctx.strokeStyle = colorOf(fams[i]);
      ctx.lineWidth = 9;
      ctx.globalAlpha = dim ? 0.08 : 0.5;
      ctx.lineCap = 'round';
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // 3) 外环扇区 + 标签
    for (let i = 0; i < n; i++) {
      const sp = spans[i];
      const dim = hover >= 0 && hover !== i;
      ctx.beginPath();
      ctx.arc(cx, cy, R, sp.a0, sp.a1);
      ctx.arc(cx, cy, inner, sp.a1, sp.a0, true);
      ctx.closePath();
      ctx.fillStyle = colorOf(fams[i]);
      ctx.globalAlpha = dim ? 0.18 : 1;
      ctx.fill();
      ctx.globalAlpha = 1;

      // 标签
      const lr = R + 22;
      const lx = cx + Math.cos(sp.mid) * lr;
      const ly = cy + Math.sin(sp.mid) * lr;
      ctx.fillStyle = dim ? 'rgba(120,113,108,.45)' : '#2c3e50';
      ctx.font = `600 ${Math.max(12, Math.min(15, R * 0.075))}px system-ui,sans-serif`;
      ctx.textAlign = Math.abs(Math.cos(sp.mid)) < 0.3 ? 'center'
        : (Math.cos(sp.mid) > 0 ? 'left' : 'right');
      ctx.textBaseline = 'middle';
      ctx.fillText(fams[i], lx, ly);

      // 条数
      ctx.fillStyle = dim ? 'rgba(120,113,108,.4)' : '#78716c';
      ctx.font = `400 ${Math.max(10, Math.min(12, R * 0.06))}px system-ui,sans-serif`;
      ctx.fillText(String(sp.total), lx, ly + 16);
    }

    // 4) 中心提示
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#78716c';
    ctx.font = '500 13px system-ui,sans-serif';
    if (hover >= 0) {
      ctx.fillStyle = COLORS[fams[hover]] || COLORS['其他'];
      ctx.font = '700 18px system-ui,sans-serif';
      ctx.fillText(fams[hover], cx, cy - 10);
      ctx.fillStyle = '#78716c';
      ctx.font = '400 13px system-ui,sans-serif';
      ctx.fillText(`${spans[hover].total} 条关系`, cx, cy + 14);
    } else {
      ctx.fillText('悬停扇区查看家族关系', cx, cy);
    }
  }

  // ---- 悬停命中的家族 ----
  function hit(testX, testY) {
    if (!geo) return -1;
    const { cx, cy, R, spans } = geo;
    const dx = testX - cx, dy = testY - cy;
    const d = Math.hypot(dx, dy);
    if (d > R + 26) return -1;
    let ang = Math.atan2(dy, dx);
    if (ang < -Math.PI / 2) ang += Math.PI * 2;
    for (let i = 0; i < spans.length; i++) {
      if (ang >= spans[i].a0 && ang <= spans[i].a1) return i;
    }
    return -1;
  }

  function onMove(e) {
    const rect = canvas.getBoundingClientRect();
    const h = hit(e.clientX - rect.left, e.clientY - rect.top);
    if (h !== hover) {
      hover = h;
      canvas.style.cursor = h >= 0 ? 'pointer' : 'default';
      draw();
    }
  }
  function onLeave() { if (hover !== -1) { hover = -1; draw(); } }
  function onResize() { geo = null; draw(); }

  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerleave', onLeave);
  window.addEventListener('resize', onResize);

  geo = layout();
  draw();

  return {
    destroy() {
      cancelAnimationFrame(raf);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerleave', onLeave);
      window.removeEventListener('resize', onResize);
      if (ctx) ctx.setTransform(1, 0, 0, 1, 0, 0);
    },
    resetView() { geo = null; hover = -1; draw(); },
  };
}
