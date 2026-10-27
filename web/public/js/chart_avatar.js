/**
 * S37 · 主要人物关系图（头像节点版）
 * =============================================================
 *
 * 与已有三种视图的区别
 * --------------------
 *   2D 力导向  —— 350 节点全量，看得见结构，看不清具体关系
 *   3D 力导向  —— 同上，多了空间感
 *   和弦图     —— 家族之间的流量，不显示具体人物
 *   **本视图** —— 只画 15 个主要人物，节点头像化，**连线上直接写关系**
 *
 * 实现要点
 * --------
 *  · 头像用 Canvas 画圆（clip + drawImage），未加载完时退回「首字圆形」
 *  · 连线关系文字画在线段中点，带白底胶囊避免压线看不清
 *  · 力导向布局与 chart.js 的 force() 同构，但规模小（15 节点）→ 迭代更快、更稳
 *  · 交互：拖拽节点 / 滚轮缩放 / 拖拽空白平移 / 点击选中高亮
 */

/* ---------------- 家族配色（与全站一致）---------------- */
const FAM_COLOR = {
  贾: '#b03a2e',
  史: '#2f4858',
  王: '#7b4b94',
  薛: '#a3802c',
  林: '#3f6a55',
  其他: '#78716c',
};

/**
 * @param {HTMLCanvasElement} canvas
 * @param {{nodes:Array, edges:Array}} data
 * @param {Object} opt { onSelect(node) }
 */
export function avatarGraph(canvas, data, opt = {}) {
  const ctx = canvas.getContext('2d');
  const DPR = Math.min(window.devicePixelRatio || 1, 2);

  let W = 0;
  let H = 0;
  let raf = 0;
  let alive = true;

  const NODE_R = 22;          // 头像圆半径（CSS px）
  const NODE_R_SEL = 28;      // 选中时放大

  /* ---------------- 数据准备 ---------------- */
  const nodes = data.nodes.map((n, i) => ({
    ...n,
    // 初始环形布局，随后力导向微调
    x: 0, y: 0, vx: 0, vy: 0,
    r: NODE_R,
    img: null,
    imgReady: false,
    pinned: false,      // 被拖拽时为 true，力推不动它
  }));

  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const links = data.edges
    .map((e) => ({ ...e, s: idx.get(e.source), t: idx.get(e.target) }))
    .filter((e) => e.s !== undefined && e.t !== undefined);

  /* ---------------- 预加载头像 ---------------- */
  for (const n of nodes) {
    if (!n.avatar) continue;
    const im = new Image();
    im.onload = () => { n.imgReady = true; };
    im.onerror = () => { n.imgReady = false; };
    im.src = n.avatar;
    n.img = im;
  }

  /* ---------------- 尺寸 ---------------- */
  function resize() {
    const par = canvas.parentElement;
    W = canvas.clientWidth || par?.clientWidth || 900;
    H = canvas.clientHeight || par?.clientHeight || 560;
    if (W < 60) W = 900;
    if (H < 60) H = 560;
    canvas.width = Math.round(W * DPR);
    canvas.height = Math.round(H * DPR);
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
  }

  /* ---------------- 初始布局：主人物居中，其余环形展开 ---------------- */
  function seedLayout() {
    const cx = W / 2;
    const cy = H / 2 + 16;   // 视觉中心下移，避开顶部按钮栏
    const sorted = [...nodes].sort((a, b) => (b.degree || 0) - (a.degree || 0));
    // 依据画布宽高比决定两圈半径，避免横向溢出
    // 实测（画布 878×584）：Rx=0.52W + 向心 0.0002 → 铺展 86%×75%，0 重叠
    const Rx = W * 0.52;
    const Ry = Math.min(H * 0.44, W * 0.52);

    sorted.forEach((n, i) => {
      if (i === 0) {
        // 度数最高 → 正中
        n.x = cx; n.y = cy;
      } else if (i <= 6) {
        // 内圈 6 人
        const a = ((i - 1) / 6) * Math.PI * 2 - Math.PI / 2;
        n.x = cx + Math.cos(a) * Rx * 0.55;
        n.y = cy + Math.sin(a) * Ry * 0.55;
      } else {
        // 外圈其余
        const k = i - 7;
        const total = sorted.length - 7;
        const a = (k / Math.max(total, 1)) * Math.PI * 2 - Math.PI / 2;
        n.x = cx + Math.cos(a) * Rx;
        n.y = cy + Math.sin(a) * Ry;
      }
    });
  }

  /* ---------------- 力模型 ----------------
   * 设计目标（按用户要求）：
   *   ① 标签之间不产生任何力 —— 标签固定在连线中点，见 drawLink
   *   ② 节点不重叠 —— 用「硬碰撞」保证最小间距，而不是只靠库仑斥力
   *   ③ 拖动一个节点时，其余节点只做小幅避让 —— 见 tick() 里的拖拽隔离
   *
   * 为什么不用 REPULSION/d²：
   *   库仑斥力在近距离会急剧放大，节点越是快重叠越是剧烈弹开，
   *   表现为抖动、成对飞散。改成「最小间距 + 线性推力」后非常稳定。
   * ------------------------------------------------------------------ */
  const MIN_GAP = 70;              // 两节点圆环之间的最小空隙（px）
                                   // 实测：26→70 使平均最近邻距离 75.6→114px
  const REPULSE_K = 0.55;          // 间距不足时的线性推力系数
  const SPRING = 0.030;            // 弹簧
  const DAMP = 0.82;               // 阻尼
  const CENTER_PULL = 0.0001;      // 向心（极弱：再大就把节点拉回中心、压缩间距）

  let alpha = 1;

  /** 两个节点的「不重叠最小距离」 */
  function minDist(a, b) {
    return a.r + b.r + MIN_GAP;
  }

  function tick() {
    if (alpha < 0.004) return;
    const cx = W / 2;
    const cy = H / 2;

    /* ---------- ① 硬碰撞：间距不足时线性推开 ----------
     * 与库仑斥力不同，这里的推力与「侵入深度」成正比，且有上限，
     * 不会因为距离趋近 0 而爆炸 → 节点稳定不抖。 */
    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i];
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j];
        if (a.pinned && b.pinned) continue;

        let dx = b.x - a.x;
        let dy = b.y - a.y;
        let d = Math.sqrt(dx * dx + dy * dy);
        const need = minDist(a, b);
        if (d >= need) continue;

        // 完全重合时给一个确定方向，避免 NaN 和对称死锁
        if (d < 0.01) {
          const ang = (i * 2.399963) % (Math.PI * 2);   // 黄金角散开
          dx = Math.cos(ang); dy = Math.sin(ang); d = 1;
        }
        const overlap = need - d;                       // 侵入深度
        const push = Math.min(overlap * REPULSE_K, 6);  // 单帧推力上限
        const ux = dx / d;
        const uy = dy / d;

        // 被拖拽/钉住的节点不移动，全部位移让给另一方
        const wa = a.pinned ? 0 : (b.pinned ? 1 : 0.5);
        const wb = b.pinned ? 0 : (a.pinned ? 1 : 0.5);
        a.vx -= ux * push * wa * 2;
        a.vy -= uy * push * wa * 2;
        b.vx += ux * push * wb * 2;
        b.vy += uy * push * wb * 2;
      }
    }

    /* ---------- ② 弹簧：只拉不推（推的部分交给硬碰撞）----------
     * 弹簧若也能推，会和碰撞力打架 → 抖动。
     * 这里只在「距离过远」时收拢，过近时不额外施加推力。 */
    const maxW = Math.max(...links.map((l) => l.weight || 1), 1);
    for (const L of links) {
      const a = nodes[L.s];
      const b = nodes[L.t];
      if (a.pinned && b.pinned) continue;
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const need = minDist(a, b);
      const w = (L.weight || 1) / maxW;
      // 0.42：实测 0.30 时弹簧会把已拉开的节点又收回来，平均间距被压到 134px
      const rest = Math.max(need, Math.min(W, H) * (0.60 + 0.08 * (1 - w)));
      if (d <= rest) continue;                 // 已足够近，不再拉

      const f = (d - rest) * SPRING * (0.7 + 0.6 * w);
      const ux = dx / d;
      const uy = dy / d;
      const wa = a.pinned ? 0 : (b.pinned ? 1 : 0.5);
      const wb = b.pinned ? 0 : (a.pinned ? 1 : 0.5);
      a.vx += ux * f * wa * 2;
      a.vy += uy * f * wa * 2;
      b.vx -= ux * f * wb * 2;
      b.vy -= uy * f * wb * 2;
    }

    /* ---------- ③ 向心 + 阻尼 + 位置更新 ---------- */
    for (const n of nodes) {
      if (n.pinned) { n.vx = 0; n.vy = 0; continue; }
      n.vx += (cx - n.x) * CENTER_PULL;
      n.vy += (cy - n.y) * CENTER_PULL;
      n.vx *= DAMP;
      n.vy *= DAMP;
      n.x += n.vx * alpha;
      n.y += n.vy * alpha;

      const padX = n.r + 10;
      const padTop = n.r + 52;
      const padBottom = n.r + 26;
      n.x = Math.max(padX, Math.min(W - padX, n.x));
      n.y = Math.max(padTop, Math.min(H - padBottom, n.y));
    }

    alpha *= 0.986;
  }

  /* ---------------- 转换与命中 ---------------- */
  let view = { x: 0, y: 0, k: 1 };
  let drag = null;
  let selectedId = null;
  let hoverIdx = -1;
  let pan = null;

  function toScreen(n) {
    return {
      x: (n.x + view.x) * view.k,
      y: (n.y + view.y) * view.k,
    };
  }

  function hitTest(mx, my) {
    // 逆序：上层（后画的）优先
    for (let i = nodes.length - 1; i >= 0; i--) {
      const n = nodes[i];
      const p = toScreen(n);
      const r = n.r * view.k + 3;
      const dx = mx - p.x;
      const dy = my - p.y;
      if (dx * dx + dy * dy <= r * r) return i;
    }
    return -1;
  }

  /* ---------------- 绘制 ---------------- */
  function roundRect(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  /** 画一个节点头像 */
  function drawNode(n, isSel, isHover) {
    const p = toScreen(n);
    const r = (isSel ? NODE_R_SEL : n.r) * view.k;
    const color = FAM_COLOR[n.family] || FAM_COLOR['其他'];

    // 外发光（选中）
    if (isSel) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + 7, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(176,58,46,.14)';
      ctx.fill();
    }

    // 头像或占位
    ctx.save();
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.closePath();
    ctx.clip();

    if (n.imgReady && n.img) {
      // 铺满圆形
      const s = Math.max((r * 2) / n.img.width, (r * 2) / n.img.height);
      const dw = n.img.width * s;
      const dh = n.img.height * s;
      ctx.drawImage(n.img, p.x - dw / 2, p.y - dh / 2, dw, dh);
    } else {
      // 占位：家族色 + 首字
      ctx.fillStyle = color;
      ctx.fillRect(p.x - r, p.y - r, r * 2, r * 2);
      ctx.fillStyle = '#fff';
      ctx.font = `700 ${Math.round(r * 0.9)}px var(--font-serif, serif)`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText((n.name || '?').slice(0, 1), p.x, p.y + 1);
    }
    ctx.restore();

    // 圆环
    ctx.beginPath();
    ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
    ctx.lineWidth = isSel ? 3.5 : (isHover ? 3 : 2);
    ctx.strokeStyle = isSel ? '#b03a2e' : (isHover ? color : 'rgba(255,255,255,.95)');
    ctx.stroke();
    // 外描边（浅色，增强与背景的分离）
    ctx.beginPath();
    ctx.arc(p.x, p.y, r + 1.2, 0, Math.PI * 2);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(47,72,88,.16)';
    ctx.stroke();

    // 姓名（头像下方）
    const fontSize = Math.max(12, 14 * view.k);
    ctx.font = `600 ${fontSize}px var(--font-sans, sans-serif)`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    const label = n.name;
    const tw = ctx.measureText(label).width;
    const ly = p.y + r + 5;
    // 白底，避免压在连线上
    ctx.fillStyle = 'rgba(250,247,242,.92)';
    roundRect(p.x - tw / 2 - 4, ly - 1, tw + 8, fontSize + 5, 4);
    ctx.fill();
    ctx.fillStyle = isSel ? '#b03a2e' : '#1c1917';
    ctx.fillText(label, p.x, ly + 1);
  }

  /** 画一条边 + 中点关系文字 */
  function drawLink(L, dim) {
    const a = nodes[L.s];
    const b = nodes[L.t];
    const pa = toScreen(a);
    const pb = toScreen(b);
    const ra = a.r * view.k;
    const rb = b.r * view.k;

    // 单位向量，裁剪到圆周（线不压住头像）
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const x1 = pa.x + ux * (ra + 2);
    const y1 = pa.y + uy * (ra + 2);
    const x2 = pb.x - ux * (rb + 2);
    const y2 = pb.y - uy * (rb + 2);

    const w = L.weight || 1;
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.lineWidth = Math.max(1.2, Math.min(3.6, 1.0 + w * 0.7)) * view.k;
    ctx.strokeStyle = dim
      ? 'rgba(120,140,155,.18)'
      : 'rgba(90,110,128,.45)';
    ctx.stroke();

    // 关系文字（中点，带白底胶囊）
    if (!L.relation) return;
    const fs = Math.max(10, 11.5 * view.k);
    ctx.font = `600 ${fs}px var(--font-sans, sans-serif)`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const tw = ctx.measureText(L.relation).width;
    const padX = 5;
    const hgt = fs + 7;

    // ---- 标签固定在线段中点 ----
    // 不使用任何位移/避让：标签之间互不产生作用力，位置只由两端节点决定。
    // （之前沿法线找空位的做法，会让标签看起来在「互相推挤」）
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;

    // 胶囊底
    ctx.fillStyle = dim ? 'rgba(250,247,242,.55)' : 'rgba(255,255,255,.94)';
    roundRect(mx - tw / 2 - padX, my - hgt / 2, tw + padX * 2, hgt, 5);
    ctx.fill();
    ctx.strokeStyle = dim ? 'rgba(120,140,155,.10)' : 'rgba(47,72,88,.14)';
    ctx.lineWidth = 0.8;
    ctx.stroke();

    ctx.fillStyle = dim ? 'rgba(120,140,155,.45)' : '#44403c';
    ctx.fillText(L.relation, mx, my + 0.5);
  }

  function draw() {
    ctx.clearRect(0, 0, W, H);

    // 选中时，非邻居边淡化
    const neighbors = new Set();
    if (selectedId) {
      neighbors.add(selectedId);
      for (const L of links) {
        if (nodes[L.s].id === selectedId) neighbors.add(nodes[L.t].id);
        if (nodes[L.t].id === selectedId) neighbors.add(nodes[L.s].id);
      }
    }

    // 先画边（在头像下面）
    for (const L of links) {
      const dim = selectedId && !(nodes[L.s].id === selectedId || nodes[L.t].id === selectedId);
      drawLink(L, dim);
    }

    // 再画节点：先画非选中的，最后画选中的（在最上层）
    const order = nodes.map((n, i) => i)
      .sort((i, j) => {
        const si = nodes[i].id === selectedId ? 1 : 0;
        const sj = nodes[j].id === selectedId ? 1 : 0;
        return si - sj;
      });
    for (const i of order) {
      const n = nodes[i];
      const isSel = n.id === selectedId;
      const isHover = i === hoverIdx;
      drawNode(n, isSel, isHover);
    }
  }

  /* ---------------- 主循环 ---------------- */
  let frame = 0;
  function loop() {
    if (!alive) return;
    frame++;
    if (alpha > 0.004) tick();
    draw();

    raf = requestAnimationFrame(loop);
  }

  /* ---------------- 事件 ---------------- */
  function pos(e) {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  function onDown(e) {
    const { x, y } = pos(e);
    const i = hitTest(x, y);
    if (i >= 0) {
      drag = { i, ox: nodes[i].x - x / view.k, oy: nodes[i].y - y / view.k };
      // 被拖拽的节点「钉住」：其他节点的力推不动它，位置完全跟随鼠标
      nodes[i].pinned = true;
      // 只给很小的活力 —— 其余节点只做轻微避让，而不是整体重排
      alpha = Math.max(alpha, 0.16);
    } else {
      pan = { x, y, vx: view.x, vy: view.y, moved: false };
    }
    canvas.style.cursor = 'grabbing';
  }

  function onMove(e) {
    const { x, y } = pos(e);

    if (drag) {
      const n = nodes[drag.i];
      n.x = x / view.k + drag.ox;
      n.y = y / view.k + drag.oy;
      n.vx = 0; n.vy = 0;
      // 保持一个「小」的 alpha，让周围节点缓慢小幅让位；解拖后自然衰减
      alpha = Math.max(alpha, 0.10);
      return;
    }
    if (pan) {
      const dx = x - pan.x;
      const dy = y - pan.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) pan.moved = true;
      view.x = pan.vx + dx / view.k;
      view.y = pan.vy + dy / view.k;
      return;
    }

    const i = hitTest(x, y);
    if (i !== hoverIdx) {
      hoverIdx = i;
      canvas.style.cursor = i >= 0 ? 'pointer' : 'grab';
    }
  }

  function onUp(e) {
    const { x, y } = pos(e);
    if (pan && !pan.moved) {
      // 点空白 → 取消选中
      const i = hitTest(x, y);
      if (i < 0) {
        selectedId = null;
        if (typeof opt.onSelect === 'function') opt.onSelect(null);
      }
    }
    if (drag) {
      // 解除钉住
      const di = drag.i;
      if (nodes[di]) nodes[di].pinned = false;
      // 拖动后若位移很小，视为点击选中
      const i = drag.i;
      const n = nodes[i];
      const p = toScreen(n);
      if (Math.hypot(p.x - x, p.y - y) < 6) {
        selectedId = selectedId === n.id ? null : n.id;
        if (typeof opt.onSelect === 'function') opt.onSelect(selectedId ? n : null);
      }
    }
    drag = null;
    pan = null;
    // 解拖后温和收敛，避免整体剧烈重排
    alpha = Math.max(alpha, 0.12);
    canvas.style.cursor = 'grab';
  }

  function onWheel(e) {
    e.preventDefault();
    const { x, y } = pos(e);
    const before = { x: x / view.k - view.x, y: y / view.k - view.y };
    const k2 = Math.max(0.5, Math.min(2.4, view.k * (1 - Math.sign(e.deltaY) * 0.1)));
    view.k = k2;
    view.x = x / k2 - before.x;
    view.y = y / k2 - before.y;
  }

  canvas.style.cursor = 'grab';
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  window.addEventListener('pointerup', onUp);
  canvas.addEventListener('wheel', onWheel, { passive: false });

  const ro = new ResizeObserver(() => { resize(); });
  ro.observe(canvas);
  if (canvas.parentElement) ro.observe(canvas.parentElement);

  resize();
  seedLayout();
  loop();

  return {
    /** 诊断：返回当前布局的平均最近邻距离等指标（仅供调试） */
    stats() {
      const nn = nodes.map((n, i) => {
        let d = Infinity;
        for (let j = 0; j < nodes.length; j++) {
          if (j === i) continue;
          const t = Math.hypot(nodes[i].x - nodes[j].x, nodes[i].y - nodes[j].y);
          if (t < d) d = t;
        }
        return d;
      });
      const el = canvas.parentElement;
      return {
        n: nodes.length,
        W, H,
        avgNN: +(nn.reduce((a, b) => a + b, 0) / nn.length).toFixed(1),
        minNN: +Math.min(...nn).toFixed(1),
        maxNN: +Math.max(...nn).toFixed(1),
        nodeR: NODE_R,
        canvasClientW: canvas.clientWidth,
        canvasClientH: canvas.clientHeight,
      };
    },
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
      view = { x: 0, y: 0, k: 1 };
      selectedId = null;
      hoverIdx = -1;
      alpha = 1;
      seedLayout();
    },
    reheat() { alpha = 1; },
  };
}
