/**
 * S37 · 简化示意图（欢迎页「程序架构」前置说明 + 部署视图）
 * ============================================================
 * 目的：复杂架构图信息密度太高，先给同学一个**直观印象**。
 *
 *   renderSequenceDiagram()    UML 顺序图：一次提问经历了什么
 *   renderDeploymentDiagram()  UML 部署图：东西都跑在哪
 *
 * 全部零依赖 SVG，配色与 arch_diagram.js 保持一致。
 */

const C = {
  front: '#2f4858', frontBg: '#eef3f6',
  api:   '#b03a2e', apiBg: '#fdf3f1',
  db:    '#2f6f4e', dbBg: '#f0f7f2',
  llm:   '#7b4b94', llmBg: '#f4eff8',
  warn:  '#a3802c', warnBg: '#f6efdc',
  line:  '#b8c4cc', text: '#2c3e50', muted: '#7a8a96',
  band:  '#f7f4ee', bandLine: '#e3dbcd',
};

const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

const box = (x, y, w, h, fill, stroke, rx = 8, sw = 1.4, dash = '') =>
  `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${rx}" fill="${fill}" stroke="${stroke}"
     stroke-width="${sw}"${dash ? ` stroke-dasharray="${dash}"` : ''}/>`;

const txt = (x, y, s, size = 14, fill = C.text, weight = 400, anchor = 'middle') =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}"
     text-anchor="${anchor}" font-family="system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif">${esc(s)}</text>`;

// 旋转文字（用于阶段标签）
const vtxt = (x, y, s, size = 13, fill = C.text, weight = 700) =>
  `<text x="${x}" y="${y}" font-size="${size}" fill="${fill}" font-weight="${weight}"
     text-anchor="middle" transform="rotate(-90 ${x} ${y})"
     font-family="system-ui,-apple-system,'Segoe UI','Microsoft YaHei',sans-serif">${esc(s)}</text>`;

/* ============================================================
   一、UML 顺序图
   ------------------------------------------------------------
   4 条生命线：浏览器 → FastAPI → HBase → LLM
   左侧用 ①②③④ 标出四个阶段，便于第一次看就抓住主线。
   ============================================================ */
export function renderSequenceDiagram() {
  const W = 1040, H = 700;
  const LEFT = 108;                 // 左侧阶段栏宽度
  const X = [250, 500, 730, 930];   // 4 条生命线 x
  const TOP = 84;                   // 生命线起始 y
  const BOT = 640;

  const heads = [
    { x: X[0], name: '用户 / 浏览器', sub: '原生 ES Module', c: C.front, bg: C.frontBg },
    { x: X[1], name: 'FastAPI 后端', sub: ':8000', c: C.api, bg: C.apiBg },
    { x: X[2], name: 'HBase 索引', sub: '11,780 chunk', c: C.db, bg: C.dbBg },
    { x: X[3], name: 'DeepSeek', sub: '大模型 API', c: C.llm, bg: C.llmBg },
  ];

  const headW = 168, headH = 62;

  // 消息箭头
  const msg = (x1, x2, y, label, color, marker, dashed = false) => {
    const mid = (x1 + x2) / 2;
    return `
    <line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${color}" stroke-width="1.9"
          ${dashed ? 'stroke-dasharray="7 5"' : ''} marker-end="url(#${marker})"/>
    ${txt(mid, y - 9, label, 13.5, C.text, 600)}`;
  };

  // 自调用（回到自己的生命线）
  const selfMsg = (x, y, label, color, marker, w = 46, h = 34) => `
    <path d="M ${x} ${y} h ${w} v ${h} h ${-w}" fill="none" stroke="${color}"
          stroke-width="1.9" marker-end="url(#${marker})"/>
    ${txt(x + w + 10, y + h / 2 + 5, label, 13.5, C.text, 600, 'start')}`;

  // 阶段底色条
  const phase = (y0, y1, label) =>
    box(LEFT - 92, y0, 74, y1 - y0, C.band, C.bandLine, 7, 1) +
    vtxt(LEFT - 55, (y0 + y1) / 2, label, 13.5, C.muted);

  return `
<svg id="seqSvg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1180px;display:block;margin:0 auto">
  <defs>
    ${[
      ['sqA', C.front], ['sqB', C.api], ['sqC', C.db], ['sqD', C.llm], ['sqM', C.muted],
    ].map(([id, col]) => `
    <marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${col}"/>
    </marker>`).join('')}
  </defs>

  <!-- 阶段栏 -->
  ${phase(TOP, 250, '① 检索')}
  ${phase(250, 330, '② 融合')}
  ${phase(330, 470, '③ 生成')}
  ${phase(470, BOT - 6, '④ 展示')}

  <!-- 生命线 -->
  ${heads.map((h) => `
    <line x1="${h.x}" y1="${TOP + headH / 2}" x2="${h.x}" y2="${BOT}"
          stroke="${C.line}" stroke-width="1.4" stroke-dasharray="6 5"/>`).join('')}

  <!-- 头部 -->
  ${heads.map((h) => `
    ${box(h.x - headW / 2, TOP - headH / 2, headW, headH, h.bg, h.c, 9, 1.6)}
    ${txt(h.x, TOP - 4, h.name, 16.5, h.c, 700)}
    ${txt(h.x, TOP + 17, h.sub, 12.5, C.muted, 400)}`).join('')}

  <!-- ===== 消息序列 ===== -->
  ${msg(X[0], X[1], 150, '① 提问「宝玉和黛玉什么关系？」', C.api, 'sqB')}
  ${msg(X[1], X[2], 196, '② 三路召回：关键词 + 向量 + 图谱', C.db, 'sqC')}
  ${msg(X[2], X[1], 238, '候选片段 Top-30', C.db, 'sqC', true)}

  ${selfMsg(X[1], 272, '③ RRF 融合 k=60 + 同回去重', C.api, 'sqB')}

  ${msg(X[1], X[3], 366, '④ 拼装 prompt（片段 + 引用编号）', C.llm, 'sqD')}
  ${msg(X[3], X[1], 412, '⑤ 流式返回 token', C.llm, 'sqD', true)}

  ${msg(X[1], X[0], 500, '⑥ SSE 推送增量 + citations', C.api, 'sqA', true)}
  ${selfMsg(X[0], 534, '⑦ 打字机显示，引用角标可点击', C.front, 'sqA')}
  ${selfMsg(X[0], 596, '⑧ 点击跳转原文对应段落', C.front, 'sqA')}

  <!-- 底部说明 -->
  ${txt(LEFT, H - 18, '★ 关键：每个结论都带「版本 · 回次 · 段号」，可点回原文 —— 这是「可追溯」的含义', 13.5, C.muted, 600, 'start')}
</svg>`;
}

/* ============================================================
   一·B、UML 顺序图（建库 / 数据入湖链路）
   ------------------------------------------------------------
   5 条生命线：脚本 → 语料 → HDFS → MapReduce → HBase
   左侧用 ①②③④ 标出「采集切分 → 入湖 → 批统计 → 建索引」。
   ============================================================ */
export function renderBuildSequenceDiagram() {
  const W = 1140, H = 706;
  const LEFT = 108;
  const X = [205, 405, 615, 825, 1030];
  const TOP = 84;
  const BOT = 648;

  const heads = [
    { x: X[0], name: '数据管道脚本', sub: 'run_all.sh / s2·s3·s10', c: C.warn, bg: C.warnBg },
    { x: X[1], name: '原始语料', sub: '本地磁盘 · txt / PDF', c: C.front, bg: C.frontBg },
    { x: X[2], name: 'HDFS', sub: '/hlm/…', c: C.db, bg: C.dbBg },
    { x: X[3], name: 'MapReduce / Hive', sub: '批统计', c: C.llm, bg: C.llmBg },
    { x: X[4], name: 'HBase', sub: '5 张表', c: C.api, bg: C.apiBg },
  ];

  const headW = 178, headH = 62;

  const msg = (x1, x2, y, label, color, marker, dashed = false) => {
    const mid = (x1 + x2) / 2;
    return `
    <line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${color}" stroke-width="1.9"
          ${dashed ? 'stroke-dasharray="7 5"' : ''} marker-end="url(#${marker})"/>
    ${txt(mid, y - 9, label, 13.5, C.text, 600)}`;
  };

  const selfMsg = (x, y, label, color, marker, w = 46, h = 34) => `
    <path d="M ${x} ${y} h ${w} v ${h} h ${-w}" fill="none" stroke="${color}"
          stroke-width="1.9" marker-end="url(#${marker})"/>
    ${txt(x + w + 10, y + h / 2 + 5, label, 13.5, C.text, 600, 'start')}`;

  const phase = (y0, y1, label) =>
    box(LEFT - 92, y0, 74, y1 - y0, C.band, C.bandLine, 7, 1) +
    vtxt(LEFT - 55, (y0 + y1) / 2, label, 13.5, C.muted);

  return `
<svg id="bseqSvg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1220px;display:block;margin:0 auto">
  <defs>
    ${[
      ['bsA', C.warn], ['bsB', C.front], ['bsC', C.db], ['bsD', C.llm], ['bsE', C.api],
    ].map(([id, col]) => `
    <marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${col}"/>
    </marker>`).join('')}
  </defs>

  <!-- 阶段栏 -->
  ${phase(TOP, 250, '① 采集切分')}
  ${phase(250, 342, '② 入湖')}
  ${phase(342, 470, '③ 批统计')}
  ${phase(470, BOT - 6, '④ 建索引')}

  <!-- 生命线 -->
  ${heads.map((h) => `
    <line x1="${h.x}" y1="${TOP + headH / 2}" x2="${h.x}" y2="${BOT}"
          stroke="${C.line}" stroke-width="1.4" stroke-dasharray="6 5"/>`).join('')}

  <!-- 头部 -->
  ${heads.map((h) => `
    ${box(h.x - headW / 2, TOP - headH / 2, headW, headH, h.bg, h.c, 9, 1.6)}
    ${txt(h.x, TOP - 4, h.name, 16, h.c, 700)}
    ${txt(h.x, TOP + 17, h.sub, 12, C.muted, 400)}`).join('')}

  <!-- ===== ① 采集与切分 ===== -->
  ${msg(X[0], X[1], 148, '① 读取原始语料（txt / PDF）', C.front, 'bsB')}
  ${msg(X[1], X[0], 190, '文本流', C.front, 'bsB', true)}
  ${selfMsg(X[0], 224, '② 清洗 · 跨源去重 · 按回切块', C.warn, 'bsA')}

  <!-- ===== ② 入湖（HDFS）===== -->
  ${msg(X[0], X[2], 294, '③ 上传 jsonl 分片', C.db, 'bsC')}
  ${msg(X[2], X[0], 330, '写入确认', C.db, 'bsC', true)}

  <!-- ===== ③ 批统计（MapReduce → HBase）===== -->
  ${msg(X[0], X[3], 384, '④ 提交 4 个统计作业', C.llm, 'bsD')}
  ${msg(X[3], X[2], 420, '⑤ 输出 /hlm/mrout', C.db, 'bsC')}
  ${msg(X[3], X[4], 456, '⑥ 写入词频 / 统计表', C.api, 'bsE')}

  <!-- ===== ④ 建索引（HBase）===== -->
  ${selfMsg(X[0], 500, '⑦ 向量化 bge-small → 512 维', C.warn, 'bsA')}
  ${msg(X[0], X[4], 566, '⑧ 写入三张索引表', C.api, 'bsE')}
  ${msg(X[4], X[0], 602, '回填 rowkey', C.api, 'bsE', true)}
  ${selfMsg(X[0], 626, '⑨ 一致性校验（s32）', C.warn, 'bsA')}

  <!-- 底部说明 -->
  ${txt(LEFT, H - 16, '★ 产出：11,780 chunk · 11,780 向量（512 维）· 451,817 倒排词项 · HBase 合计 609,759 行', 13.5, C.muted, 600, 'start')}
</svg>`;
}

/* ============================================================
   一·C、问题驱动图（从需求到选型到实现到效果）
   ------------------------------------------------------------
   不讲技术名词，讲「什么问题逼出了什么决定」。
   4 列：需求 → 选型 → 实现 → 效果
   4 行：查得到 / 找得全 / 答得准 / 存得下
   ============================================================ */
export function renderQuestionDrivenDiagram() {
  const W = 1180;
  const ROW_L = 118;             // 左侧行标签列宽（要放下 3 个汉字 + 内边距）
  const COL_W = 250;
  const GAP = 16;
  const X0 = ROW_L + 10;
  const headY = 40, headH = 44;
  const cardY0 = 112, cardH = 86, cardGap = 12;
  const nRows = 4;
  const H = cardY0 + nRows * cardH + (nRows - 1) * cardGap + 84;

  const stages = [
    { title: '① 需求', sub: '用户遇到什么', c: C.front, bg: C.frontBg },
    { title: '② 选型', sub: '凭什么选它', c: C.llm, bg: C.llmBg },
    { title: '③ 实现', sub: '落在哪个模块', c: C.api, bg: C.apiBg },
    { title: '④ 效果', sub: '可验证的数字', c: C.db, bg: C.dbBg },
  ];

  const rows = [
    {
      tag: '查得到',
      cells: [
        ['想在原著里快速定位一段话'],
        ['按回切块 + 联合 bigram 倒排'],
        ['s2_1_chunk.py', 's2_2_index.py'],
        ['11,780 个可检索块'],
      ],
    },
    {
      tag: '找得全',
      cells: [
        ['换个说法就搜不到了'],
        ['向量 512 维 + 人物图谱'],
        ['RRF 融合 k=60', '同回去重（每回 2 正文 1 批语）'],
        ['三路互补，召回更稳'],
      ],
    },
    {
      tag: '答得准',
      cells: [
        ['大模型会编造「原文」'],
        ['RAG + 强制引用', '三档知识策略'],
        ['app.py build_messages', '片段编号 → 引用角标'],
        ['每条结论都带出处', '可点回原文段落'],
      ],
    },
    {
      tag: '存得下',
      cells: [
        ['百万级索存放哪、怎么算'],
        ['HDFS 存湖 + HBase 在线', 'MapReduce 离线批算'],
        ['s3_mapreduce.py', 's10_1_unify_pizhu.py'],
        ['609,759 行 · 副本 2', 'HDFS 550 MB / 1.97 TB'],
      ],
    },
  ];

  // 卡片：1~2 行文字，垂直居中
  const card = (x, y, lines, color, emphasize = false) => {
    const n = lines.length;
    const first = y + cardH / 2 - (n - 1) * 10 + 5;
    return `
    ${box(x, y, COL_W, cardH, emphasize ? '#fffdf8' : '#ffffff',
          emphasize ? color + '99' : '#dfd6c7', 9, emphasize ? 1.9 : 1.3)}
    ${box(x, y, 4, cardH, color, color, 2, 0)}
    ${lines.map((t, i) => txt(x + 14, first + i * 20, t, 12.5,
      i === 0 ? C.text : C.muted, i === 0 ? 600 : 400, 'start')).join('')}`;
  };

  const rowTag = (y, tag, color) => `
    ${box(6, y + cardH / 2 - 16, ROW_L - 18, 32, color + '14', color + '55', 16, 1.2)}
    ${txt(6 + (ROW_L - 18) / 2, y + cardH / 2 + 5, tag, 13.5, color, 700)}`;

  return `
<svg id="qdriveSvg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1240px;display:block;margin:0 auto">
  <defs>
    <marker id="qdArrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.muted}"/>
    </marker>
  </defs>

  <!-- 阶段表头 -->
  ${stages.map((st, i) => {
    const x = X0 + i * (COL_W + GAP);
    return `${box(x, headY, COL_W, headH, st.c, st.c, 9, 0)}
      ${txt(x + COL_W / 2, headY + 21, st.title, 16.5, '#ffffff', 700)}
      ${txt(x + COL_W / 2, headY + 37, st.sub, 11.5, '#ffffffcc', 400)}
      ${i < 3 ? `<line x1="${x + COL_W + 2}" y1="${headY + headH / 2}"
            x2="${x + COL_W + GAP - 3}" y2="${headY + headH / 2}"
            stroke="${C.muted}" stroke-width="2" marker-end="url(#qdArrow)"/>` : ''}`;
  }).join('')}

  <!-- 行 -->
  ${rows.map((row, ri) => {
    const y = cardY0 + ri * (cardH + cardGap);
    return rowTag(y, row.tag, stages[0].c) +
      row.cells.map((lines, ci) =>
        card(X0 + ci * (COL_W + GAP), y, lines, stages[ci].c, ci === 0)).join('');
  }).join('')}

  <!-- 底部主线说明 -->
  ${txt(W / 2, H - 40, '★ 这条链的起点不是技术，而是「读《红楼梦》时的一个具体问题」—— 每选一个技术，都对应一个原来答不上来的问题',
    13.5, C.text, 600)}
  ${txt(W / 2, H - 18, '需求不清就别选型，选型不清就别写代码：四个阶段一一对应，任何一格空着都会在后面返工',
    12.5, C.muted, 400)}
</svg>`;
}

/* ============================================================
   一·D、选型对比条形图：Spark vs MapReduce 各阶段耗时
   ------------------------------------------------------------
   同一个 wordfreq 作业、同一份输入，实测分阶段耗时。
   用堆叠横条把「时间花在哪」摊开，才能解释为什么本项目仍选 MapReduce。
   ============================================================ */
export function renderEngineBenchmark() {
  const W = 940;
  const ROW_H = 74, ROW_GAP = 30;
  const LABEL_W = 158;          // 左侧引擎名
  const BAR_X = LABEL_W + 16;
  const BAR_W = 600;            // 条形最大宽度
  const TOP = 96;
  const n = 2;
  const H = TOP + n * ROW_H + (n - 1) * ROW_GAP + 290;

  // 实测数据（秒）
  const rows = [
    {
      name: 'MapReduce',
      total: 16.53,
      color: C.api,
      segs: [
        { label: '提交 + 作业初始化', v: 3.20, note: 'JVM 启动 / 上传 streaming jar' },
        { label: 'Map 阶段',        v: 6.10, note: '2 个 map task，data-local' },
        { label: 'Shuffle + Sort',  v: 4.30, note: 'combiner 已把 48.9 万压到 16.5 万' },
        { label: 'Reduce + 写 HDFS', v: 2.93, note: '1 个 reduce task' },
      ],
    },
    {
      name: 'Spark',
      total: 5.65,
      color: C.llm,
      segs: [
        { label: '提交 + Session 启动', v: 1.86, note: 'import + SparkContext' },
        { label: '读 HDFS',            v: 1.97, note: 'textFile + count 触发' },
        { label: 'Shuffle + 计算',     v: 0.82, note: 'reduceByKey' },
        { label: '关闭',               v: 1.00, note: 'spark.stop()' },
      ],
    },
  ];

  const maxT = Math.max(...rows.map((r) => r.total));
  const scale = BAR_W / maxT;

  // 图例
  const segNames = rows[0].segs.map((sg) => sg.label);

  return `
<svg id="benchSvg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1140px;display:block;margin:0 auto">
  <!-- 标题 -->
  ${txt(W / 2, 34, '同一作业、同一份输入：时间到底花在哪', 20, C.text, 700)}
  ${txt(W / 2, 56, '文本 bigram 词频统计 · 输入 1,035 行 / 2.0 MB · 产出 133,673 个 key（两边 md5 完全相同）', 15, C.muted, 400)}

  <!-- 坐标轴 -->
  ${[0, 4, 8, 12, 16].map((v) => {
    const x = BAR_X + v * scale;
    return `<line x1="${x}" y1="${TOP - 12}" x2="${x}" y2="${TOP + n * (ROW_H + ROW_GAP) - ROW_GAP + 8}"
            stroke="${C.line}" stroke-width="1" stroke-dasharray="3 4"/>
      ${txt(x, TOP - 20, v + 's', 14, C.muted, 400)}`;
  }).join('')}

  <!-- 条形 -->
  ${rows.map((r, i) => {
    const y = TOP + i * (ROW_H + ROW_GAP);
    let x = BAR_X;
    const bars = r.segs.map((sg, k) => {
      const w = sg.v * scale;
      const alpha = 1 - k * 0.17;
      const rect = `<rect x="${x}" y="${y}" width="${Math.max(w, 1)}" height="${ROW_H}"
        fill="${r.color}" fill-opacity="${alpha}" stroke="#fff" stroke-width="1.2"/>`;
      const inner = w > 46 ? txt(x + w / 2, y + ROW_H / 2 + 4.5, sg.v.toFixed(2) + 's', 13.5, '#fff', 700) : '';
      x += w;
      return rect + inner;
    }).join('');
    return `
    ${txt(BAR_X - 14, y + 26, r.name, 17, r.color, 700, 'end')}
    ${txt(BAR_X - 14, y + 45, '合计 ' + r.total.toFixed(2) + ' s', 14, C.muted, 400, 'end')}
    ${bars}`;
  }).join('')}

  <!-- 图例：每个引擎一组，颜色与条形一致 -->
  ${rows.map((r, ri) => {
    const ly = TOP + n * (ROW_H + ROW_GAP) + 12 + ri * 28;
    return r.segs.map((sg, k) => {
      const lx = BAR_X - 30 + k * 190;
      return `
      <rect x="${lx}" y="${ly}" width="13" height="13" rx="2"
            fill="${r.color}" fill-opacity="${1 - k * 0.17}"/>
      ${txt(lx + 19, ly + 11, sg.label, 14, C.text, 400, 'start')}`;
    }).join('');
  }).join('')}

  <!-- 关键结论 -->
  ${(() => {
    const cy = TOP + n * (ROW_H + ROW_GAP) + 92;
    return `
    <rect x="${LABEL_W - 30}" y="${cy - 22}" width="${W - LABEL_W - 40}" height="158" rx="10"
          fill="${C.warnBg}" stroke="${C.warn}" stroke-width="1.3"/>
    ${txt(LABEL_W - 8, cy + 4, '为什么本项目仍然选 MapReduce', 16.5, C.warn, 700, 'start')}
    ${txt(LABEL_W - 8, cy + 34, '① 规模太小 —— 输入 2 MB，连一个 HDFS block（128 MB）都没填满，Spark 的启动开销吃掉优势', 14.5, C.text, 400, 'start')}
    ${txt(LABEL_W - 8, cy + 62, '② 没有迭代 —— 四个作业都是一次性全表扫描，Spark 的内存缓存复用完全用不上', 14.5, C.text, 400, 'start')}
    ${txt(LABEL_W - 8, cy + 90, '③ 教学更透明 —— MapReduce 日志直接给出 Map → Combine → Reduce 三个数字，shuffle 语义看得见', 14.5, C.text, 400, 'start')}
    ${txt(LABEL_W - 8, cy + 120, '※ 结论：Spark 更适合「大数据量 + 迭代计算」，本项目的规模用不上它的核心优势', 15, C.warn, 600, 'start')}`;
  })()}
</svg>`;
}

/* ============================================================
   二、UML 部署图
   ------------------------------------------------------------
   看清楚三件事：什么跑在浏览器、什么在本机进程、什么在 Docker。
   ============================================================ */
export function renderDeploymentDiagram() {
  const W = 1100, H = 880;

  // 节点框：标题 / 副标题 / 条目，高度按条目数自适应
  const node = (x, y, w, h, title, sub, color, bg, items = []) => `
    ${box(x, y, w, h, bg, color, 10, 1.7)}
    ${txt(x + w / 2, y + 28, title, 15.5, color, 700)}
    ${sub ? txt(x + w / 2, y + 50, sub, 12.5, C.muted, 400) : ''}
    ${items.map((it, k) => txt(x + w / 2, y + 78 + k * 22, it, 13, C.text, 400)).join('')}`;

  // 水平连线（标签在线上方）
  const hlink = (x1, x2, y, label, color, dash = '') => `
    <line x1="${x1}" y1="${y}" x2="${x2}" y2="${y}" stroke="${color}" stroke-width="1.8"
          ${dash ? `stroke-dasharray="${dash}"` : ''} marker-end="url(#depArrow)"/>
    ${label ? txt((x1 + x2) / 2, y - 8, label, 12.5, C.muted, 600) : ''}`;

  // 竖直连线
  const vlink = (x, y1, y2, label, color, dash = '') => `
    <line x1="${x}" y1="${y1}" x2="${x}" y2="${y2}" stroke="${color}" stroke-width="1.8"
          ${dash ? `stroke-dasharray="${dash}"` : ''} marker-end="url(#depArrow)"/>
    ${label ? txt(x + 10, (y1 + y2) / 2 + 4, label, 12.5, C.muted, 600, 'start') : ''}`;

  // 折线（三段：竖-横-竖）
  const elink = (x1, y1, y2, x2, label, color, dash = '') => `
    <path d="M ${x1} ${y1} V ${y2} H ${x2}" fill="none" stroke="${color}"
          stroke-width="1.8" stroke-dasharray="${dash || 'none'}" marker-end="url(#depArrow)"/>
    ${label ? txt(x1 + 14, y2 - 9, label, 12.5, C.muted, 600, 'start') : ''}`;

  return `
<svg id="depSvg" viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;height:auto;max-width:1180px;display:block;margin:0 auto">
  <defs>
    <marker id="depArrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="${C.muted}"/>
    </marker>
  </defs>

  <!-- ===== 宿主机（本机）===== -->
  ${box(20, 20, 800, 830, '#fbfaf7', '#c9c2b4', 14, 1.9, '10 7')}
  ${txt(44, 50, '«device»  开发机 / 演示机（Windows）', 15, C.muted, 700, 'start')}

  <!-- 左列 -->
  ${node(48, 80, 310, 104, '«browser»  浏览器', 'Chrome / Edge', C.front, C.frontBg,
    ['渲染 8 个视图，零框架'])}
  ${node(48, 216, 310, 126, '«process»  Node 静态服务', ':5173', C.api, C.apiBg,
    ['静态资源 + REST 接口', '反向代理到后端', '视频 Range 流式输出'])}
  ${node(48, 390, 310, 104, '«process»  mpv.net', '外部播放器', C.warn, C.warnBg,
    ['背景音乐 单曲循环'])}
  ${node(460, 216, 300, 126, '«container»  hlm-api', 'FastAPI · :8000', C.api, C.apiBg,
    ['三路检索 · RRF 融合', '沙箱自定义查询', 'SSE 流式回答'])}

  <!-- 右列（外部）-->
  ${node(880, 216, 200, 126, '«cloud»  DeepSeek', '官方 API', C.llm, C.llmBg,
    ['模型推理', '可换 GLM 等'])}
  ${node(880, 390, 200, 148, '«storage»  本地磁盘', '语料 / 视频 / 音频', C.warn, C.warnBg,
    ['pipeline/out/*.jsonl', '视频 115 MB 不入库', '背景音乐 .m4s'])}

  <!-- Docker 集群 -->
  ${box(48, 520, 740, 250, C.dbBg, C.db, 12, 1.9, '9 6')}
  ${txt(70, 548, '«execution environment»  Docker Compose · hadoop-lab', 14.5, C.db, 700, 'start')}
  ${node(66, 570, 214, 176, 'master', '172.19.0.2', C.db, '#ffffff',
    ['NameNode', 'HMaster', 'Thrift :9090', 'ZooKeeper'])}
  ${node(300, 570, 214, 176, 'slave1', '172.19.0.3', C.db, '#ffffff',
    ['DataNode', 'NodeManager', 'RegionServer'])}
  ${node(534, 570, 214, 176, 'slave2', '172.19.0.4', C.db, '#ffffff',
    ['DataNode', 'NodeManager', 'RegionServer'])}

  <!-- ===== 连线 ===== -->
  ${vlink(203, 184, 216, 'HTTP', C.front)}
  ${hlink(358, 420, 279, 'proxy /api/*', C.api)}
  ${hlink(760, 880, 279, 'HTTPS', C.llm)}
  ${vlink(203, 342, 390, '启动播放', C.warn)}
  ${vlink(610, 342, 520, 'Thrift 9090', C.db)}

  <!-- Node → 本地磁盘：绕开中间模块 -->
  ${elink(300, 342, 365, 830, 'Range 流式读', C.warn, '7 5')}
  <line x1="830" y1="365" x2="830" y2="450" stroke="${C.warn}" stroke-width="1.8" stroke-dasharray="7 5"/>
  <line x1="830" y1="450" x2="880" y2="450" stroke="${C.warn}" stroke-width="1.8"
        stroke-dasharray="7 5" marker-end="url(#depArrow)"/>

  <!-- 集群内部副本 -->
  <line x1="280" y1="658" x2="300" y2="658" stroke="${C.db}" stroke-width="1.6" stroke-dasharray="5 4"/>
  <line x1="514" y1="658" x2="534" y2="658" stroke="${C.db}" stroke-width="1.6" stroke-dasharray="5 4"/>
  ${txt(418, 806, 'HDFS 副本数 2：数据块在 slave1 / slave2 各存一份', 13, C.muted, 600)}
</svg>`;
}
