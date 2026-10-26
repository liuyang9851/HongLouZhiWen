/**
 * chart.js —— 零依赖 Canvas 图表库（阶段 S1）
 *
 * 为什么不用 ECharts：
 *   需求文档 §8.2 原本选 ECharts，但 Node 端零依赖 + 无 CDN（教学环境可能离线）
 *   是本项目更硬的约束。这里自绘 5 种图，代码量可控（< 400 行），且能精确控制
 *   《红楼梦》的宣纸/朱砂配色。
 *
 * 提供：bar / hbar / line / pie / heat / force
 */

const C = {
  ink: '#1c1917', ink2: '#44403c', ink3: '#78716c',
  paper: '#faf7f2', paper2: '#f2ece3', paper3: '#e8dfd2', line: '#ddd2c2',
  cinnabar: '#b03a2e', cinnabar2: '#cf5040', dai: '#2f4858', gold: '#a3802c',
  jade: '#3f6a55', amber: '#b0730f',
};
export const PALETTE = [
  '#b03a2e', '#2f4858', '#a3802c', '#3f6a55', '#7b4b94',
  '#b0730f', '#4a6b7c', '#8c5a3c', '#5c7a4b', '#9a4d6b',
];

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  // 兜底：canvas 刚插入 DOM、或父级尚未完成布局时 rect 可能是 0，
  // 这时退回到父级尺寸 / CSS 高度的估算值，避免画布 0×0 画不出东西。
  let w = rect.width;
  let h = rect.height;
  if (w < 4) {
    const p = canvas.parentElement?.getBoundingClientRect();
    w = (p && p.width > 4) ? p.width : (canvas.parentElement?.clientWidth || 480);
  }
  if (h < 4) {
    const cssH = parseFloat(getComputedStyle(canvas.parentElement || canvas).height);
    h = Number.isFinite(cssH) && cssH > 4 ? cssH : 240;
  }
  w = Math.max(w, 40);
  h = Math.max(h, 40);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  canvas.style.width = w + 'px';
  canvas.style.height = h + 'px';
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  return { ctx, w, h };
}

function niceMax(v) {
  if (v <= 0) return 1;
  const e = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / e;
  // 取「刚好盖住最大值」的 1/1.5/2/2.5/3/4/5/10 步长，
  // 避免 21728 被取整到 50000 那样把柱子压扁。
  const step = n <= 1 ? 1 : n <= 1.5 ? 1.5 : n <= 2 ? 2 : n <= 2.5 ? 2.5
    : n <= 3 ? 3 : n <= 4 ? 4 : n <= 5 ? 5 : n <= 8 ? 8 : 10;
  return step * e;
}

function roundRect(ctx, x, y, w, h, r) {
  const rr = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

/* ---------------------------------------------------------------- 柱状图 */
export function bar(canvas, data, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const pad = { t: 16, r: 12, b: opt.rotate ? 54 : 30, l: 44 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const vals = data.map((d) => d.value);
  const maxV = niceMax(Math.max(...vals, 1));
  const bw = iw / data.length;

  // 网格 + y 轴
  ctx.strokeStyle = C.paper3; ctx.fillStyle = C.ink3;
  ctx.font = '10px "Cascadia Code", monospace';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (ih * i) / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + iw, y); ctx.stroke();
    ctx.fillText(Math.round(maxV * (1 - i / 4)), pad.l - 6, y);
  }

  // 柱
  const bwid = bw * 0.68;
  data.forEach((d, i) => {
    const bh = Math.max((d.value / maxV) * ih, d.value > 0 ? 1.5 : 0);
    const x = pad.l + i * bw + bw * 0.16;
    const y = pad.t + ih - bh;
    const col = d.color || opt.color || C.cinnabar;
    const grad = ctx.createLinearGradient(0, y, 0, y + bh);
    grad.addColorStop(0, col);
    grad.addColorStop(1, col + 'cc');
    ctx.fillStyle = grad;
    roundRect(ctx, x, y, bwid, bh, 2); ctx.fill();
  });

  // x 轴标签（独立循环：按可用宽度抽稀，避免 80 个标签糊成一片）
  ctx.fillStyle = C.ink3;
  ctx.font = '10px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const maxLabels = Math.max(4, Math.floor(iw / 34));   // 每个标签至少占 34px
  const labelStep = Math.max(1, Math.ceil(data.length / maxLabels));
  data.forEach((d, i) => {
    if (i % labelStep !== 0 && i !== data.length - 1) return;
    const cx = pad.l + i * bw + bw / 2;
    if (opt.rotate) {
      ctx.save();
      ctx.translate(cx, pad.t + ih + 7);
      ctx.rotate(-Math.PI / 4);
      ctx.textAlign = 'right';
      ctx.fillText(d.label, 0, 0);
      ctx.restore();
    } else {
      ctx.fillText(d.label, cx, pad.t + ih + 6);
    }
  });
}

/* ------------------------------------------------------------ 横向条形图 */
export function hbar(canvas, data, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const pad = { t: 10, r: 44, b: 10, l: opt.labelW || 66 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const maxV = niceMax(Math.max(...data.map((d) => d.value), 1));
  const rowH = ih / data.length;
  const barH = Math.min(rowH * 0.62, 20);

  data.forEach((d, i) => {
    const y = pad.t + i * rowH + (rowH - barH) / 2;
    ctx.fillStyle = C.ink2;
    ctx.font = '11px "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText(d.label, pad.l - 8, y + barH / 2);

    const bw = Math.max((d.value / maxV) * iw, 1);
    const col = d.color || opt.color || C.cinnabar;
    ctx.fillStyle = C.paper2;
    roundRect(ctx, pad.l, y, iw, barH, barH / 2); ctx.fill();
    const grad = ctx.createLinearGradient(pad.l, 0, pad.l + bw, 0);
    grad.addColorStop(0, col + 'dd');
    grad.addColorStop(1, col);
    ctx.fillStyle = grad;
    roundRect(ctx, pad.l, y, bw, barH, barH / 2); ctx.fill();

    ctx.fillStyle = C.ink3;
    ctx.font = '10px "Cascadia Code", monospace';
    ctx.textAlign = 'left';
    ctx.fillText(d.value, pad.l + iw + 6, y + barH / 2);
  });
}

/* -------------------------------------------------------------- 折线图 */
export function line(canvas, data, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const pad = { t: 16, r: 14, b: 30, l: 46 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const maxV = niceMax(Math.max(...data.map((d) => d.value), 1));
  const minV = opt.zeroBase === false ? Math.min(...data.map((d) => d.value)) : 0;
  const span = Math.max(maxV - minV, 1);

  ctx.strokeStyle = C.paper3; ctx.fillStyle = C.ink3;
  ctx.font = '10px "Cascadia Code", monospace';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (ih * i) / 4;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(pad.l + iw, y); ctx.stroke();
    ctx.fillText(Math.round(minV + span * (1 - i / 4)), pad.l - 6, y);
  }

  const px = (i) => pad.l + (data.length === 1 ? iw / 2 : (iw * i) / (data.length - 1));
  const py = (v) => pad.t + ih - ((v - minV) / span) * ih;

  // 面积
  if (opt.area !== false) {
    const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ih);
    grad.addColorStop(0, (opt.color || C.cinnabar) + '30');
    grad.addColorStop(1, (opt.color || C.cinnabar) + '02');
    ctx.beginPath();
    ctx.moveTo(px(0), pad.t + ih);
    data.forEach((d, i) => ctx.lineTo(px(i), py(d.value)));
    ctx.lineTo(px(data.length - 1), pad.t + ih);
    ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();
  }

  // 线
  ctx.beginPath();
  data.forEach((d, i) => (i ? ctx.lineTo(px(i), py(d.value)) : ctx.moveTo(px(i), py(d.value))));
  ctx.strokeStyle = opt.color || C.cinnabar;
  ctx.lineWidth = 1.8; ctx.lineJoin = 'round'; ctx.stroke();

  // 标记离群点
  if (opt.markMax) {
    let mi = 0;
    data.forEach((d, i) => { if (d.value > data[mi].value) mi = i; });
    ctx.beginPath();
    ctx.arc(px(mi), py(data[mi].value), 4, 0, Math.PI * 2);
    ctx.fillStyle = C.amber; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
    ctx.fillStyle = C.amber;
    ctx.font = '10px "PingFang SC", sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
    ctx.fillText(opt.markMax, px(mi), py(data[mi].value) - 7);
  }

  // x 轴标签
  ctx.fillStyle = C.ink3;
  ctx.font = '10px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const step = Math.max(1, Math.ceil(data.length / 12));
  data.forEach((d, i) => {
    if (i % step === 0 || i === data.length - 1) ctx.fillText(d.label, px(i), pad.t + ih + 6);
  });
}

/* -------------------------------------------------------------- 饼/环图 */
export function pie(canvas, data, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const cx = w / 2, cy = h / 2;
  const r = Math.min(w, h) / 2 - 14;
  const inner = opt.donut ? r * 0.56 : 0;
  const total = data.reduce((s, d) => s + d.value, 0) || 1;

  let a = -Math.PI / 2;
  data.forEach((d, i) => {
    const ang = (d.value / total) * Math.PI * 2;
    const col = d.color || PALETTE[i % PALETTE.length];
    ctx.beginPath();
    ctx.moveTo(cx, cy);
    ctx.arc(cx, cy, r, a, a + ang);
    ctx.closePath();
    ctx.fillStyle = col;
    ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.4; ctx.stroke();
    a += ang;
  });

  if (inner) {
    ctx.beginPath();
    ctx.arc(cx, cy, inner, 0, Math.PI * 2);
    ctx.fillStyle = '#fff'; ctx.fill();
    if (opt.center) {
      ctx.fillStyle = C.ink;
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = '600 16px "Songti SC", serif';
      ctx.fillText(opt.center, cx, cy - 6);
      if (opt.centerSub) {
        ctx.fillStyle = C.ink3;
        ctx.font = '10px "PingFang SC", sans-serif';
        ctx.fillText(opt.centerSub, cx, cy + 12);
      }
    }
  }
}

/* -------------------------------------------------------------- 热力图 */
export function heat(canvas, matrix, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const rows = matrix.length;
  const cols = matrix[0]?.length || 0;
  if (!rows || !cols) return;

  const pad = { t: 14, r: 8, b: 26, l: 56 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const cw = iw / cols, ch = ih / rows;
  let maxV = 0;
  matrix.forEach((r) => r.forEach((v) => { if (v > maxV) maxV = v; }));

  matrix.forEach((row, ri) => {
    row.forEach((v, ci) => {
      const a = maxV ? v / maxV : 0;
      ctx.fillStyle = a === 0
        ? '#f6f2ea'
        : `rgba(176,58,46,${0.10 + a * 0.80})`;
      ctx.fillRect(pad.l + ci * cw, pad.t + ri * ch, Math.max(cw - 1, 1), Math.max(ch - 1, 1));
    });
  });

  // y 轴标签
  ctx.fillStyle = C.ink3;
  ctx.font = '10px "PingFang SC", "Microsoft YaHei", sans-serif';
  ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
  (opt.rowLabels || []).forEach((l, ri) => {
    ctx.fillText(l, pad.l - 7, pad.t + ri * ch + ch / 2);
  });
  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
  const step = Math.max(1, Math.ceil(cols / 16));
  (opt.colLabels || []).forEach((l, ci) => {
    if (ci % step === 0) ctx.fillText(l, pad.l + ci * cw + cw / 2, pad.t + ih + 5);
  });
}

/* --------------------------------------------------- 力导向图（含交互） */
export function force(canvas, graph, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  // 留出边距，避免标签/节点被画布边缘切掉
  const M = 26;
  const nodes = graph.nodes.map((n, i) => ({
    ...n,
    // 初始散布半径随节点数放大（否则 350 个节点初始就挤在一起）
    x: w / 2 + Math.cos((i / graph.nodes.length) * Math.PI * 2)
       * Math.min(w, h) * (graph.nodes.length > 250 ? 0.46 : 0.28),
    y: h / 2 + Math.sin((i / graph.nodes.length) * Math.PI * 2)
       * Math.min(w, h) * (graph.nodes.length > 250 ? 0.46 : 0.28),
    vx: 0, vy: 0,
    r: Math.max(4, Math.min(17, 4 + Math.sqrt(n.degree || 1) * 2.1)),
  }));
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const links = graph.edges
    .map((e) => ({ ...e, s: idx.get(e.source), t: idx.get(e.target) }))
    .filter((e) => e.s !== undefined && e.t !== undefined);

  // S29 共现叠加层（来自 MapReduce cooccur）
  // 语义边：有类型（丫鬟/父子），来自 CSV
  // 共现边：无类型、只有强度，来自 HBase hlm_stats 的 MapReduce 产出
  const CO_MAX = (opt.cooccur && opt.cooccur.length)
    ? Math.max(...opt.cooccur.map((c) => c.count)) : 1;
  const coLinks = (opt.cooccur || [])
    .map((e) => ({
      s: idx.get(e.source), t: idx.get(e.target),
      count: e.count,
      w: 0.5 + (e.count / CO_MAX) * 3.2,      // 线宽随共现强度
    }))
    .filter((e) => e.s !== undefined && e.t !== undefined);

  const FAM_COLOR = {
    贾: '#b03a2e', 史: '#2f4858', 王: '#7b4b94',
    薛: '#a3802c', 林: '#3f6a55', 其他: '#78716c',
  };

  // 自适应布局参数（节点越多，斥力越强、弹簧越长、画布越"大"）
  const N = nodes.length;
  const REPULSION = N > 250 ? 5200 : (N > 150 ? 3000 : 1600);
  const SPRING_REST = N > 250 ? 110 : (N > 150 ? 85 : 62);

  let alpha = 1;
  let hovered = null;
  let selected = null;
  let drag = null;
  let view = { x: 0, y: 0, k: 1 };

  /* ---- 物理迭代 ---- */
  function tick() {
    // 斥力
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        let dx = b.x - a.x, dy = b.y - a.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - 0.5) * 2; dy = (Math.random() - 0.5) * 2; d2 = 1; }
        const d = Math.sqrt(d2);
        // 斥力随节点数自适应：节点越多需要越强斥力，否则挤成一团
    // 实测 120 节点用 1600 合适；350 节点需明显加大
    const f = (REPULSION / d2) * alpha;
        const fx = (dx / d) * f, fy = (dy / d) * f;
        a.vx -= fx; a.vy -= fy;
        b.vx += fx; b.vy += fy;
      }
    }
    // 弹簧
    for (const l of links) {
      const a = nodes[l.s], b = nodes[l.t];
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = SPRING_REST;
      const f = ((d - rest) / d) * 0.055 * alpha;
      const fx = dx * f, fy = dy * f;
      a.vx += fx; a.vy += fy;
      b.vx -= fx; b.vy -= fy;
    }
    // 向心
    for (const n of nodes) {
      n.vx += (w / 2 - n.x) * 0.0045 * alpha;
      n.vy += (h / 2 - n.y) * 0.0045 * alpha;
    }
    // 积分 + 阻尼
    for (const n of nodes) {
      if (n === drag) continue;
      n.vx *= 0.72; n.vy *= 0.72;
      n.x += n.vx; n.y += n.vy;
      // 约束在画布内（含标签余量），否则节点会跑到可视区外
      n.x = Math.max(n.r + M, Math.min(w - n.r - M, n.x));
      n.y = Math.max(n.r + M, Math.min(h - n.r - M - 13, n.y));
    }
    alpha *= 0.985;
    if (alpha < 0.008) alpha = 0.008;
  }

  /* ---- 绘制 ---- */
  function draw() {
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.translate(view.x, view.y);
    ctx.scale(view.k, view.k);

    const focus = hovered ?? selected;
    const near = new Set();
    if (focus) {
      near.add(focus.id);
      for (const l of links) {
        if (nodes[l.s]?.id === focus.id) near.add(nodes[l.t]?.id);
        if (nodes[l.t]?.id === focus.id) near.add(nodes[l.s]?.id);
      }
    }

    // ---- S29 共现叠加层：先画（在底层，半透明红虚线）----
    // 注意：必须在语义边循环**之外**画，否则每条语义边都会重绘全部共现边，
    //       且会覆盖 strokeStyle 导致语义边颜色错乱。
    if (coLinks.length) {
      ctx.save();
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 0.7;
      for (const cl of coLinks) {
        const a = nodes[cl.s], b = nodes[cl.t];
        if (!a || !b) continue;
        const cdim = focus && !(near.has(a.id) && near.has(b.id));
        ctx.strokeStyle = cdim
          ? 'rgba(176,58,46,.04)'
          : `rgba(176,58,46,${(0.06 + (cl.w / 3.7) * 0.16).toFixed(3)})`;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
      ctx.restore();
    }

    // ---- 语义边（CSV 来的丫鬟/父子等）----
    for (const l of links) {
      const a = nodes[l.s], b = nodes[l.t];
      if (!a || !b) continue;
      const dim = focus && !(near.has(a.id) && near.has(b.id));
      ctx.beginPath();
      ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
      ctx.strokeStyle = dim ? 'rgba(221,210,194,.35)' : 'rgba(180,168,150,.6)';
      ctx.lineWidth = dim ? 0.6 : 1.1;
      ctx.stroke();

      // 悬停时显示关系名
      if (focus && !dim) {
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        ctx.font = '9px "PingFang SC", sans-serif';
        const tw = ctx.measureText(l.relation).width;
        ctx.fillStyle = 'rgba(255,255,255,.92)';
        roundRect(ctx, mx - tw / 2 - 3, my - 7, tw + 6, 13, 3); ctx.fill();
        ctx.fillStyle = '#78716c';
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(l.relation, mx, my);
      }
    }

    // 节点
    for (const n of nodes) {
      const dim = focus && !near.has(n.id);
      const col = FAM_COLOR[n.family] || FAM_COLOR['其他'];

      ctx.beginPath();
      ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
      ctx.fillStyle = dim ? '#e8dfd2' : col;
      ctx.globalAlpha = dim ? 0.45 : 1;
      ctx.fill();
      if (n === selected || n === hovered) {
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 2.4; ctx.stroke();
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r + 3.5, 0, Math.PI * 2);
        ctx.strokeStyle = col; ctx.lineWidth = 1.4; ctx.stroke();
      }
      ctx.globalAlpha = 1;

      // 标签：大节点或聚焦时显示
      if (!dim && (n.r > 8 || near.has(n.id) || n === hovered)) {
        ctx.font = `${n.r > 11 ? 600 : 400} ${n.r > 11 ? 11.5 : 10.5}px "Songti SC", "SimSun", serif`;
        ctx.textAlign = 'center'; ctx.textBaseline = 'top';
        const tw = ctx.measureText(n.name).width;
        ctx.fillStyle = 'rgba(250,247,242,.82)';
        ctx.fillRect(n.x - tw / 2 - 2, n.y + n.r + 2, tw + 4, 13);
        ctx.fillStyle = '#1c1917';
        ctx.fillText(n.name, n.x, n.y + n.r + 3);
      }
    }
    ctx.restore();
  }

  /* ---- 主循环 ---- */
  let raf;
  let running = true;
  function loop() {
    if (!running) return;
    tick();
    draw();
    raf = requestAnimationFrame(loop);
  }
  loop();

  /* ---- 交互 ---- */
  function toGraph(ev) {
    const rect = canvas.getBoundingClientRect();
    return {
      x: (ev.clientX - rect.left - view.x) / view.k,
      y: (ev.clientY - rect.top - view.y) / view.k,
    };
  }
  function pick(p) {
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const dx = p.x - n.x, dy = p.y - n.y;
      if (dx * dx + dy * dy <= (n.r + 3) * (n.r + 3)) return n;
    }
    return null;
  }

  /* ---- 交互：用 Pointer Events 统一处理「拖节点」与「拖画布平移」 ----
   *
   * 修复的问题（S22）：
   *   ① 提示写着「拖拽移动」，但代码只实现了拖节点，**画布平移完全没写**
   *      （view.x/view.y 只在滚轮里赋值过）
   *   ② mousemove 绑在 canvas 上而非 window，鼠标移出画布拖拽就断
   *   ③ 用 mousedown/mousemove 在触屏/触控板上不生效
   *
   * 新逻辑（Pointer Events）：
   *   · 按在节点上 → 拖节点（物理引擎跳过它）
   *   · 按在空白处 → 拖画布平移视图
   *   · 位移 < 4px 视为「点击」→ 选中节点
   */
  let pan = null;          // { x0, y0, vx0, vy0 } 拖画布时的起点
  let downPt = null;       // 按下时的屏幕坐标（判断点击 vs 拖拽）
  let moved = false;       // 是否发生过有效移动

  canvas.style.touchAction = 'none';   // 触屏下禁用浏览器默认手势

  canvas.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    const p = toGraph(ev);
    const n = pick(p);
    downPt = { x: ev.clientX, y: ev.clientY };
    moved = false;
    if (n) {
      drag = n;
      alpha = Math.max(alpha, 0.35);
      canvas.style.cursor = 'grabbing';
    } else {
      // 空白处按下 → 准备平移画布
      pan = { x0: ev.clientX, y0: ev.clientY, vx0: view.x, vy0: view.y };
      canvas.style.cursor = 'grabbing';
    }
    canvas.setPointerCapture?.(ev.pointerId);
    ev.preventDefault();
  });

  // 关键修复：绑在 window 上，鼠标移出画布也不断
  window.addEventListener('pointermove', (ev) => {
    // ---- 拖画布平移 ----
    if (pan) {
      const dx = ev.clientX - pan.x0;
      const dy = ev.clientY - pan.y0;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
      // 限制平移范围：最多拖出画布 1.2 倍，防止把图拖丢
      const lim = Math.max(w, h) * 1.2;
      view.x = Math.max(-lim, Math.min(lim, pan.vx0 + dx));
      view.y = Math.max(-lim, Math.min(lim, pan.vy0 + dy));
      return;
    }
    // ---- 拖节点 ----
    const p = toGraph(ev);
    if (drag) {
      drag.x = p.x; drag.y = p.y; drag.vx = 0; drag.vy = 0;
      alpha = Math.max(alpha, 0.25);
      if (downPt) {
        const dx = ev.clientX - downPt.x, dy = ev.clientY - downPt.y;
        if (dx * dx + dy * dy > 16) moved = true;
      }
      return;
    }
    // ---- hover 高亮 ----
    if (!canvas.isConnected) return;
    const n = pick(p);
    if (n !== hovered) {
      hovered = n;
      canvas.style.cursor = n ? 'pointer' : 'grab';
      opt.onHover?.(n);
    }
  });

  window.addEventListener('pointerup', (ev) => {
    // ---- 结束平移 ----
    if (pan) {
      pan = null;
      canvas.style.cursor = 'grab';
      return;
    }
    // ---- 结束拖节点 ----
    if (drag) {
      const n = drag;
      drag = null;
      canvas.style.cursor = 'grab';
      // 位移很小 → 视为点击选中
      if (!moved) {
        selected = n;
        opt.onSelect?.(n);
      }
      return;
    }
    // ---- 空白处轻点 → 取消选中 ----
    if (downPt && !moved) {
      selected = null;
      opt.onSelect?.(null);
    }
    downPt = null;
  });

  // 指针被系统取消（如手势中断）→ 清理状态
  window.addEventListener('pointercancel', () => {
    pan = null; drag = null; downPt = null; moved = false;
    canvas.style.cursor = 'grab';
  });

  // 双击空白处 → 重置视图
  canvas.addEventListener('dblclick', (ev) => {
    const p = toGraph(ev);
    if (!pick(p)) {
      view = { x: 0, y: 0, k: 1 };
      alpha = Math.max(alpha, 0.5);
    }
  });

  canvas.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const rect = canvas.getBoundingClientRect();
    const mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
    const nk = Math.max(0.35, Math.min(3.2, view.k * factor));
    view.x = mx - ((mx - view.x) * nk) / view.k;
    view.y = my - ((my - view.y) * nk) / view.k;
    view.k = nk;
  }, { passive: false });

  return {
    destroy() { running = false; cancelAnimationFrame(raf); },
    focus(id) {
      const n = nodes.find((x) => x.id === id);
      if (n) { selected = n; view = { x: w / 2 - n.x, y: h / 2 - n.y, k: 1.4 }; opt.onSelect?.(n); }
    },
    reheat() { alpha = 0.9; },
    resetView() { view = { x: 0, y: 0, k: 1 }; alpha = Math.max(alpha, 0.5); },
    getView() { return { ...view }; },
    nodes,
  };
}

/** 全局重绘：处理窗口尺寸变化 */
const registry = new Map();
export function register(canvas, fn) {
  registry.set(canvas, fn);
  if (!window.__hlmResizeBound) {
    window.__hlmResizeBound = true;
    let t;
    window.addEventListener('resize', () => {
      clearTimeout(t);
      t = setTimeout(() => {
        for (const [c, f] of registry) { if (c.isConnected) f(); else registry.delete(c); }
      }, 140);
    });
  }
}
