/**
 * S37 · 人物出场热力图（替代原 3D 曲面）
 * =====================================================
 *
 * 为什么要替换
 * ------------
 * 原「人物出场曲面（3D）」把 30 人物 × 80 回 = 2,400 个数据点
 * 画成 2,400 根立体柱子。问题：
 *   · 数据点太多，柱子互相遮挡，看不出具体数值
 *   · 透视投影下远处柱子被压扁，视觉上"糊成一片"
 *   · 需要拖拽旋转才能看清局部，有交互成本
 *   · 本质是**矩阵数据**，用 3D 柱状图表达是最差的形式
 *
 * 为什么热力图更合适
 * ----------------
 *   · 矩阵 → 色块，一格一值，无遮挡
 *   · 一屏看完全部 80 回 × 30 人物的分布规律
 *   · 颜色深浅 = 提及次数，热点一眼可见
 *   · 附带交互：悬停看具体数值、点击行/列高亮
 *
 * 设计细节
 * --------
 *   · 行 = 人物（按总提及量降序，重要人物在上）
 *   · 列 = 回次（1–80）
 *   · 颜色 = 红系渐变（与项目主色一致），0 值为浅底色
 *   · 右侧附「峰值回次」标注，方便快速定位
 */

const C = {
  ink: '#2c3e50',
  ink2: '#5b6b7a',
  ink3: '#7a8a96',
  paper: '#fff',
  paper3: '#e8e2d8',
  cinnabar: '#b03a2e',
  line: '#ddd4c4',
};

function setupCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width || canvas.clientWidth || 800;
  const h = rect.height || canvas.clientHeight || 400;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);
  return { ctx, w, h };
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

/**
 * 人物出场热力图
 *
 * @param {HTMLCanvasElement} canvas
 * @param {Object} data  person_hui_matrix.json
 *   { huis:[1..80], persons:[{name,total,series,peak_hui,peak_val}], max_val }
 * @param {Object} opt
 *   onSelect(personName)  点击行时回调
 */
export function personHeatmap(canvas, data, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);

  const huis = data.huis || [];
  const persons = (data.persons || []).slice();
  // 按总提及降序（重要人物在上）
  persons.sort((a, b) => (b.total || 0) - (a.total || 0));

  const rows = persons.length;
  const cols = huis.length;
  if (!rows || !cols) return { destroy() {} };

  // 左侧人物名 + 右侧峰值标注
  const pad = { t: 22, r: 78, b: 34, l: 82 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;
  const cw = iw / cols;
  const ch = ih / rows;

  const maxV = data.max_val || Math.max(...persons.flatMap((p) => p.series || [0]));
  let hovered = null;      // { ri, ci }
  let selectedRow = null;  // 高亮某个人物

  function colorFor(v) {
    if (!v) return 'rgba(232,226,216,.45)';
    const a = Math.pow(v / maxV, 0.55);   // 幂次让低值也可见
    // 红系渐变：浅粉 → 深朱
    const r0 = 250, g0 = 240, b0 = 228;
    const r1 = 155, g1 = 40, b1 = 28;
    const r = Math.round(r0 + (r1 - r0) * a);
    const g = Math.round(g0 + (g1 - g0) * a);
    const b = Math.round(b0 + (b1 - b0) * a);
    return `rgb(${r},${g},${b})`;
  }

  function draw() {
    ctx.clearRect(0, 0, w, h);

    // ---- 标题行（列标签：回次）----
    ctx.font = '10px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.fillStyle = C.ink3;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    const step = Math.max(1, Math.ceil(cols / 20));
    huis.forEach((hv, ci) => {
      if (ci % step === 0 || ci === cols - 1) {
        ctx.fillText(String(hv), pad.l + ci * cw + cw / 2, pad.t - 6);
      }
    });

    // ---- 热力格 ----
    persons.forEach((p, ri) => {
      const series = p.series || [];
      const dim = selectedRow !== null && selectedRow !== p.name;
      for (let ci = 0; ci < cols; ci++) {
        const v = series[ci] || 0;
        const x = pad.l + ci * cw;
        const y = pad.t + ri * ch;
        ctx.fillStyle = colorFor(v);
        ctx.globalAlpha = dim ? 0.45 : 1;   // 淡化但仍可读（0.22 太浅）
        ctx.fillRect(x, y, Math.max(cw - 0.8, 0.8), Math.max(ch - 0.8, 0.8));
      }
      ctx.globalAlpha = 1;

      // 行标签（人物名）
      ctx.fillStyle = dim ? C.ink3 : (selectedRow === p.name ? C.cinnabar : C.ink2);
      ctx.font = `${selectedRow === p.name ? '600 ' : ''}11px "PingFang SC","Microsoft YaHei",sans-serif`;
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(p.name, pad.l - 8, pad.t + ri * ch + ch / 2);

      // 右侧峰值标注
      if (p.peak_hui && p.peak_val) {
        ctx.fillStyle = C.ink3;
        ctx.font = '9.5px "PingFang SC","Microsoft YaHei",sans-serif';
        ctx.textAlign = 'left';
        ctx.fillText(`第${p.peak_hui}回 · ${p.peak_val}`, pad.l + iw + 8,
                     pad.t + ri * ch + ch / 2);
      }
    });

    // ---- 列标题（回次）----
    ctx.fillStyle = C.ink3;
    ctx.font = '10px "PingFang SC","Microsoft YaHei",sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText('回次', pad.l + iw / 2, pad.t + ih + 12);

    // ---- 悬停提示 ----
    if (hovered) {
      const p = persons[hovered.ri];
      const v = (p.series || [])[hovered.ci] || 0;
      const huiNo = huis[hovered.ci];
      const label = `${p.name} · 第${huiNo}回 · ${v} 次`;
      ctx.font = '11.5px "PingFang SC","Microsoft YaHei",sans-serif';
      const tw = ctx.measureText(label).width;
      let bx = pad.l + hovered.ci * cw + cw / 2 - tw / 2 - 8;
      let by = pad.t + hovered.ri * ch - 26;
      if (by < 0) by = pad.t + hovered.ri * ch + ch + 6;
      bx = Math.max(4, Math.min(w - tw - 20, bx));
      ctx.fillStyle = 'rgba(44,62,80,.92)';
      roundRect(ctx, bx, by, tw + 16, 20, 4);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'left';
      ctx.textBaseline = 'middle';
      ctx.fillText(label, bx + 8, by + 10);

      // 高亮当前格
      ctx.strokeStyle = C.cinnabar;
      ctx.lineWidth = 1.6;
      ctx.strokeRect(pad.l + hovered.ci * cw, pad.t + hovered.ri * ch,
                     cw, ch);
    }
  }

  function hitTest(mx, my) {
    const ci = Math.floor((mx - pad.l) / cw);
    const ri = Math.floor((my - pad.t) / ch);
    if (ci < 0 || ci >= cols || ri < 0 || ri >= rows) return null;
    return { ri, ci };
  }

  let raf = null;
  function schedule() {
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = null; draw(); });
  }

  function onMove(e) {
    const rect = canvas.getBoundingClientRect();
    hovered = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    canvas.style.cursor = hovered ? 'pointer' : 'default';
    schedule();
  }

  function onLeave() {
    hovered = null;
    schedule();
  }

  function onClick(e) {
    const rect = canvas.getBoundingClientRect();
    const hit = hitTest(e.clientX - rect.left, e.clientY - rect.top);
    if (!hit) {
      selectedRow = null;
      schedule();
      return;
    }
    const p = persons[hit.ri];
    selectedRow = selectedRow === p.name ? null : p.name;
    schedule();
    if (opt.onSelect) opt.onSelect(selectedRow, p);
  }

  canvas.addEventListener('mousemove', onMove);
  canvas.addEventListener('mouseleave', onLeave);
  canvas.addEventListener('click', onClick);
  draw();

  return {
    destroy() {
      canvas.removeEventListener('mousemove', onMove);
      canvas.removeEventListener('mouseleave', onLeave);
      canvas.removeEventListener('click', onClick);
      if (raf) cancelAnimationFrame(raf);
    },
    clearSelection() { selectedRow = null; draw(); },
  };
}

/**
 * 单个人物的出场曲线（配合热力图，做"详情补充"）
 * 当用户点击热力图某一行时，在下方画该人物的逐回曲线。
 */
export function personCurve(canvas, persons, huis, opt = {}) {
  const { ctx, w, h } = setupCanvas(canvas);
  const pad = { t: 18, r: 16, b: 26, l: 44 };
  const iw = w - pad.l - pad.r;
  const ih = h - pad.t - pad.b;

  const list = persons.slice(0, opt.limit || 6);
  const cols = huis.length;
  const maxV = Math.max(1, ...list.flatMap((p) => p.series || [0]));

  const colors = ['#b03a2e', '#2f4858', '#7b4b94', '#a3802c', '#3f6a55', '#78716c'];

  // 网格
  ctx.strokeStyle = 'rgba(221,212,196,.6)';
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = pad.t + (ih / 4) * i;
    ctx.beginPath();
    ctx.moveTo(pad.l, y);
    ctx.lineTo(pad.l + iw, y);
    ctx.stroke();
    ctx.fillStyle = C.ink3;
    ctx.font = '9.5px sans-serif';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    ctx.fillText(String(Math.round(maxV * (1 - i / 4))), pad.l - 6, y);
  }

  // 曲线
  list.forEach((p, pi) => {
    const series = p.series || [];
    ctx.beginPath();
    series.forEach((v, ci) => {
      const x = pad.l + (iw / (cols - 1)) * ci;
      const y = pad.t + ih - (v / maxV) * ih;
      if (ci === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = colors[pi % colors.length];
    ctx.lineWidth = 1.8;
    ctx.stroke();
  });

  // x 轴
  ctx.fillStyle = C.ink3;
  ctx.font = '9.5px "PingFang SC",sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';
  const step = Math.max(1, Math.ceil(cols / 10));
  huis.forEach((hv, ci) => {
    if (ci % step === 0 || ci === cols - 1) {
      ctx.fillText(String(hv), pad.l + (iw / (cols - 1)) * ci, pad.t + ih + 5);
    }
  });
}
