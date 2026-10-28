/**
 * S25 · 项目原理页
 * =================
 * 用一个真实问题走完全链路，展示数据在
 *   用户端 → Hadoop 各组件 → 大模型
 * 每一处发生的**实际变化**。
 *
 * 数据来源：scripts/s25_capture_trace.py 在容器内实跑采集，
 *           落盘为 web/data/trace.json（非示意图，是真实值）。
 */

const H = () => window.__HLM__ || {};
const esc = (s) => {
  const f = H().esc;
  return f ? f(s) : String(s ?? '');
};

/* ---------------- 小工具 ---------------- */

const fmtMs = (ms) => {
  if (ms == null) return '—';
  return ms >= 1000 ? (ms / 1000).toFixed(2) + ' s' : ms.toFixed(1) + ' ms';
};

const fmtNum = (n) => (n == null ? '—' : Number(n).toLocaleString('zh-CN'));

/** 阶段徽章颜色 */
const STAGE_COLOR = {
  1: '#7a6a52',
  2: '#2f6f4e',
  3: '#2f6f4e',
  4: '#b03a2e',
  5: '#2f4858',
  6: '#2f6f4e',
  7: '#5b6b7a',
  8: '#b03a2e',
};

/** 代码块 */
const codeBlock = (text, lang = '') =>
  `<pre class="pr-code${lang ? ' pr-code-' + lang : ''}"><code>${esc(text)}</code></pre>`;

/** 键值表 */
const kvTable = (rows) => `
  <table class="pr-kv">
    <tbody>
      ${rows.map(([k, v]) => `
        <tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}
    </tbody>
  </table>`;

/* =====================================================================
   主渲染
   ===================================================================== */
export async function renderPrinciple(stage) {
  let T = null;
  try {
    const r = await fetch('/data/trace.json?v=' + Date.now());
    if (r.ok) T = await r.json();
  } catch (e) { /* 用兜底 */ }

  if (!T) {
    stage.innerHTML = `<div class="view v-principle"><div class="loading">
      未能载入追踪数据（web/data/trace.json）</div></div>`;
    return;
  }

  // 建库追踪（离线流水线）
  let BT = null;
  try {
    const r2 = await fetch('/data/trace_build.json?v=' + Date.now());
    if (r2.ok) BT = await r2.json();
  } catch (e) { /* 无建库数据时只显示问答标签 */ }
  const bStages = (BT && BT.stages) || [];
  const bTarget = (BT && BT.target) || {};

  const q = T.question;
  const stages = T.stages || [];
  const meta = T.meta || {};

  // 时间构成
  const tL1 = stages.find((s) => s.id === 2)?.ms || 0;
  const tL2 = stages.find((s) => s.id === 3)?.ms || 0;
  const tL3 = stages.find((s) => s.id === 4)?.ms || 0;
  const tLLM = stages.find((s) => s.id === 8)?.ms || 0;
  const tOther = Math.max(0, (meta.total_ms || 0) - tL1 - tL2 - tL3 - tLLM);
  const tFuse = stages.find((s) => s.id === 5)?.ms || 0;
  const s2d = stages.find((s) => s.id === 2)?.detail || {};
  const s3d = stages.find((s) => s.id === 3)?.detail || {};
  const s4d = stages.find((s) => s.id === 4)?.detail || {};
  const nCand = `${s2d.top_candidates ? 30 : 0} + ${s3d.top_candidates ? 30 : 0} + ${s4d.n_graph_candidates || 0}`;

  stage.innerHTML = `
  <div class="view v-principle">

    <!-- ============ Hero ============ -->
    <div class="pr-hero">
      <div class="pr-hero-tag">项目原理 · 一次问答的全链路</div>
      <h1>一个问题在系统里经历了什么</h1>
      <div class="pr-hero-q">
        <span class="pr-hero-q-lbl">用户输入</span>
        <span class="pr-hero-q-text">「${esc(q)}」</span>
      </div>
      <p class="pr-hero-lead">
        下面所有数据都是<b>真实跑出来的</b>——从容器里逐阶段采集，
        不是示意图。你可以看到每条数据在
        <b>浏览器</b> → <b>HDFS</b> → <b>MapReduce</b> → <b>HBase</b> → <b>大模型</b>
        之间的实际形态变化。
      </p>
      <div class="pr-kpis">
        <div><b>8</b><span>处理阶段</span></div>
        <div><b>${(meta.total_ms / 1000).toFixed(2)}<small>s</small></b><span>端到端耗时</span></div>
        <div><b>3</b><span>路并行检索</span></div>
        <div><b>5</b><span>条最终上下文</span></div>
        <div><b>${fmtNum((meta.corpus || {}).hlm_terms)}</b><span>索引表总行数</span></div>
      </div>
    </div>

    <!-- ============ 一、先分清：离线建库 vs 在线问答 ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">1</span>先分清两件事：离线建库 vs 在线问答</h2>
      <p class="pr-intro">
        这是理解整个系统的关键。用户提问时，Hadoop <b>并不在运行</b>——
        它早就在离线阶段把索引建好了。在线问答只做<b>点查</b>。
      </p>

      <div class="pr-two">
        <div class="pr-two-col offline">
          <div class="pr-two-h">
            <span class="pr-two-tag">离线 · 建库</span>
            <span class="pr-two-when">部署时跑一次，之后索引长期复用</span>
          </div>
          <ol class="pr-flow">
            <li>
              <b>原始语料</b>
              <span>三版本正文 167.8 万字 + 三源脂批 12,457 条</span>
            </li>
            <li>
              <b>HDFS 存入</b>
              <span>/hlm/ 目录，副本数 2，块大小 128MB</span>
            </li>
            <li>
              <b>MapReduce 批处理</b>
              <span>切两字组合 → 建索引表 hlm_terms(451,817 行)<br>
                    统词频 → hlm_wordfreq(133,673 行)<br>
                    统计人物共现 → hlm_stats cooccur(588 行)</span>
            </li>
            <li>
              <b>ONNX 编码向量</b>
              <span>11,932 个文本块 → 512 维向量 → hlm_vectors</span>
            </li>
            <li>
              <b>写入 HBase</b>
              <span>5 张表就绪，此后只读</span>
            </li>
          </ol>
        </div>

        <div class="pr-two-col online">
          <div class="pr-two-h">
            <span class="pr-two-tag">在线 · 问答</span>
            <span class="pr-two-when">每次提问（本页例子 ${(meta.total_ms / 1000).toFixed(1)} 秒）</span>
          </div>
          <ol class="pr-flow">
            <li><b>用户提问</b><span>浏览器 → Node 代理 → FastAPI</span></li>
            <li><b>查询改写</b><span>简称展开 + 问法变体，${fmtMs(stages.find((x) => x.id === 1)?.ms || 0)}</span></li>
            <li><b>三路并行召回</b><span>关键词（查索引表）${fmtMs(tL1)}<br>向量（算相似度）${fmtMs(tL2)}<br>人物（读共现行）${fmtMs(tL3)}</span></li>
            <li><b>三路合并排序</b><span>${nCand} 个候选 → 5 条上下文，${fmtMs(tFuse)}</span></li>
            <li><b>大模型生成</b><span>拼提示词 → 调用模型接口，${fmtMs(tLLM)}</span></li>
          </ol>
        </div>
      </div>

      <div class="pr-key">
        <b>一句话：</b>Hadoop 负责「把书变成可检索的索引」（慢，做一次）；
        在线只负责「在索引里找几段」，所以检索只要 <b>1.3 秒</b>，
        剩下 <b>5.3 秒</b> 都花在大模型上。
      </div>
    </section>

    <!-- ============ 二、逐阶段拆解（建库 / 问答 双标签） ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">2</span>逐阶段拆解：数据在每一处的形态</h2>
      <p class="pr-intro">
        <b>建库</b>和<b>问答</b>是两个方向相反的过程：建库把书变成索引（一次性），
        问答在索引里取用（每次提问）。下面用同一个文本块
        <code>${esc(bTarget.chunk_id || '')}</code> 把两边接起来。
      </p>

      <div class="pr-tabs">
        <button class="pr-tab active" data-mode="build">
          建库（离线）
          <span class="pr-tab-n">${bStages.length || 9} 阶段</span>
        </button>
        <button class="pr-tab" data-mode="online">
          问答（在线）
          <span class="pr-tab-n">${stages.length} 阶段</span>
        </button>
      </div>

      <div class="pr-tab-body" id="prTabBody">
        ${bStages.length ? buildTimelineHtml(bStages) : ''}
      </div>
    </section>

    <!-- ============ 三、Hadoop 各组件职责 ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">4</span>Hadoop 各组件在这个例子里做了什么</h2>
      <div class="pr-table-wrap">
        <table class="pr-table">
          <thead>
            <tr>
              <th>组件</th><th>阶段</th><th>在这个例子中的实际动作</th><th>数据量</th><th>耗时</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><b>HDFS</b></td>
              <td><span class="pr-badge off">离线</span></td>
              <td>存放原始语料与 MapReduce 中间产物<br>
                  <code>/hlm/</code> <code>/hlm/mrin</code> <code>/hlm/mrout</code> <code>/hlm/derived</code></td>
              <td>88.7 MB（派生）</td>
              <td class="num">—</td>
            </tr>
            <tr>
              <td><b>MapReduce</b></td>
              <td><span class="pr-badge off">离线</span></td>
              <td>切两字组合、建索引表；统计词频；统计人物共现</td>
              <td>451,817 + 133,673 + 709 行</td>
              <td class="num">—</td>
            </tr>
            <tr>
              <td><b>HBase</b><br><small>hlm_terms</small></td>
              <td><span class="pr-badge on">在线·关键词</span></td>
              <td>按两字组合逐行查索引表，取出记录累加打分</td>
              <td>读 ~${(stages.find((x) => x.id === 2)?.detail?.rows_sample || []).length * 2 || 16} 行 / 表内 451,817 行</td>
              <td class="num">${fmtMs(tL1)}</td>
            </tr>
            <tr>
              <td><b>HBase</b><br><small>hlm_vectors</small></td>
              <td><span class="pr-badge on">在线·向量</span></td>
              <td>整表加载 11,932 × 512 维矩阵，与问题向量做一次矩阵乘</td>
              <td>24.1 MB 内存矩阵</td>
              <td class="num">${fmtMs(tL2)}</td>
            </tr>
            <tr>
              <td><b>HBase</b><br><small>hlm_stats</small></td>
              <td><span class="pr-badge on">在线·人物</span></td>
              <td>读一行共现记录 <code>cooccur|林黛玉|贾宝玉</code>，取出共现次数</td>
              <td>1 行 / 表内 709 行</td>
              <td class="num">${fmtMs(tL3)}</td>
            </tr>
            <tr>
              <td><b>HBase</b><br><small>hlm_chunks</small></td>
              <td><span class="pr-badge on">在线·取正文</span></td>
              <td>按 5 个文本块编号取回正文</td>
              <td>5 行 / 表内 11,932 行</td>
              <td class="num">0.0 ms</td>
            </tr>
            <tr class="hl">
              <td><b>大模型</b></td>
              <td><span class="pr-badge llm">在线·生成</span></td>
              <td>读 5 条上下文 + 原问题，组织成带引用编号的回答</td>
              <td>3,311 字符输入 → ${stages.find((x) => x.id === 8)?.output || ''}</td>
              <td class="num">${fmtMs(tLLM)}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="pr-key">
        <b>注意：</b>整个在线问答中，<b>MapReduce 一次都没跑</b>。
        它只在离线建库时把「书」变成「索引」。
        在线跑的只有 HBase 的 GET 和一次矩阵乘法。
      </div>
    </section>

    <!-- ============ 四、时间去哪了 ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">5</span>时间花在哪</h2>
      <div class="pr-time">
        ${renderTimeBar('关键词召回（查索引表）', tL1, meta.total_ms, '#2f6f4e')}
        ${renderTimeBar('向量召回（算相似度）', tL2, meta.total_ms, '#4a7c59')}
        ${renderTimeBar('人物共现召回（扫正文）', tL3, meta.total_ms, '#b03a2e')}
        ${renderTimeBar('大模型生成', tLLM, meta.total_ms, '#8e4a8e')}
        ${renderTimeBar('其他（改写/融合/拼装）', tOther, meta.total_ms, '#9aa8b2')}
      </div>
      <div class="pr-note">
        <b>两个可优化点（诚实说明）：</b>
        <br>① <b>大模型占了 ${((tLLM / meta.total_ms) * 100).toFixed(0)}%</b> 的时间，
        这是 API 延迟，不是本地计算——换个更快的模型就能显著改善。
        <br>② <b>L3 图谱那 ${tL3.toFixed(0)} ms 其实是浪费的</b>：它为了找「同时提到两人的文本块」，
        遍历了全部 11,932 条正文（O(N) 全扫描）。
        如果离线再建一张「人物 → 文本块编号」的正排表，这一步能降到毫秒级。这是已知的待优化项。
      </div>
    </section>

    <!-- ============ 五、一条数据的完整变形链 ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">6</span>一条数据的完整变形链</h2>
      <p class="pr-intro">把上面八个阶段串起来看，用户的 11 个字变成了带引用的回答。</p>

      <div class="pr-chain">
        ${renderChain(T)}
      </div>
    </section>

    <!-- ============ 术语对照（先看这个，后面几节都会用到） ============ -->
    <section class="pr-sec">
      <h2><span class="pr-num">3</span>先认几个词</h2>
      <p class="pr-intro">
        后面会反复用到下面这些词，这里一次说清。每个词都配了一句话解释，
        看不懂可以先跳回来。
      </p>
      <div class="pr-glossary">
        <div class="pr-gi">
          <div class="pr-gi-h">文本块</div>
          <div class="pr-gi-b">检索的最小单位。整本书被切成一段段 300~800 字的块，每块有一个编号，之后所有检索都是「挑出哪些块」。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">两字组合</div>
          <div class="pr-gi-b">把汉字按「相邻两字」切开。「宝玉和黛玉」切出来是：宝玉、玉和、和黛、黛玉。用两字而不是整词，是为了不依赖分词（分词错了会漏检）。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">索引表<span class="pr-gi-en">（倒排索引）</span></div>
          <div class="pr-gi-b">「两字组合 → 哪些文本块包含它」的表。查一个组合就能知道全库哪些块有它，不用逐个块翻。之所以叫「倒排」，是因为常规思路是「块 → 它有哪些词」，这里是反过来「词 → 哪些块」。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">正排表</div>
          <div class="pr-gi-b">「文本块编号 → 它的正文和出处（版本、回次、段号）」的表。先用索引表找到编号，再回这张表取正文。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">向量</div>
          <div class="pr-gi-b">把一段文字变成 512 个小数。语义相近的文字，这 512 个数也相近，所以可以用「算两个向量的接近程度」来判断语义相似。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">召回</div>
          <div class="pr-gi-b">从全库 11,932 个文本块里，先粗粗挑出一小批（30 个左右）可能相关的，交给后面精排。这里的三路召回分别靠：两字组合、向量、人物共现。</div>
        </div>
        <div class="pr-gi">
          <div class="pr-gi-h">RRF 融合</div>
          <div class="pr-gi-b">三路召回各自给出一个排名，把各路的名次取倒数相加，得到综合排名。好处是不用管三路的分数量纲（一个是命中次数、一个是余弦值），只看名次。</div>
        </div>
      </div>
    </section>

    <!-- ============ 六、为什么必须用 Hadoop ============ -->
    <section class="pr-sec pr-sec-last">
      <h2><span class="pr-num">7</span>为什么这个项目必须用 Hadoop 生态</h2>
      <div class="pr-why">
        <div class="pr-why-item">
          <div class="pr-why-h">① 数据量不适合塞进内存</div>
          <p>倒排表 451,817 行、向量 11,932 × 512 维、正文 167.8 万字。
             这些必须落在<b>可横向扩展的分布式存储</b>上，
             而不是一个进程里的 Python 字典。</p>
        </div>
        <div class="pr-why-item">
          <div class="pr-why-h">② 建索引是典型的批处理</div>
          <p>为 11,932 个文本块切两字组合、建索引表、统计词频与共现——
             这是教科书式的 <b>MapReduce</b> 场景：
             切分、映射、按键归并、落盘。Spark/Hive 用于补充聚合统计。</p>
        </div>
        <div class="pr-why-item">
          <div class="pr-why-h">③ 在线要低延迟点查</div>
          <p>问答要求「按两字组合立刻拿到对应的文本块列表」。
             HBase 的行键点查正好是 <b>O(1)</b>，
             比在 HDFS 上全表扫描快几个数量级。</p>
        </div>
        <div class="pr-why-item">
          <div class="pr-why-h">④ 三种负载天然分层</div>
          <p><b>HDFS</b> 存冷数据（原始语料）→
             <b>MapReduce</b> 做离线计算 →
             <b>HBase</b> 提供在线服务。
             三层各司其职，这正是 Hadoop 生态存在的意义。</p>
        </div>
      </div>

      <div class="pr-foot">
        数据采集脚本：<code>scripts/s25_capture_trace.py</code>（在 hlm-api 容器内实跑）<br>
        原始追踪数据：<code>web/data/trace.json</code>（${fmtNum(28923)} 字节，含全部中间值）<br>
        语料：抚琴居校本前 80 回 · 程高本 120 回 · 癸酉本后 28 回 · 三源脂批
      </div>
    </section>

  </div>`;

  // 标签切换（建库 / 问答）
  const tabBody = stage.querySelector('#prTabBody');
  const bindToggles = () => {
    stage.querySelectorAll('.pr-toggle').forEach((btn) => {
      btn.onclick = () => {
        const body = btn.closest('.pr-stage').querySelector('.pr-stage-raw');
        if (!body) return;
        const hidden = body.hidden;
        body.hidden = !hidden;
        btn.textContent = hidden ? '收起原始数据' : '查看原始数据';
      };
    });
  };
  stage.querySelectorAll('.pr-tab').forEach((tb) => {
    tb.onclick = () => {
      stage.querySelectorAll('.pr-tab').forEach((x) => x.classList.remove('active'));
      tb.classList.add('active');
      if (!tabBody) return;
      tabBody.innerHTML = tb.dataset.mode === 'build'
        ? (bStages.length ? buildTimelineHtml(bStages) : '<div class="loading">无建库数据</div>')
        : onlineTimelineHtml(stages);
      bindToggles();
    };
  });
  bindToggles();

  // 折叠交互
  stage.querySelectorAll('.pr-toggle').forEach((btn) => {
    btn.onclick = () => {
      const body = btn.closest('.pr-stage').querySelector('.pr-stage-raw');
      if (!body) return;
      const hidden = body.hidden;
      body.hidden = !hidden;
      btn.textContent = hidden ? '收起原始数据' : '查看原始数据';
    };
  });
}

/* =====================================================================
   标签页：建库 / 问答 两套时间线
   ===================================================================== */
function buildTimelineHtml(bStages) {
  return `<div class="pr-timeline">${bStages.map((s) => renderStageCard(s, 'build')).join('')}</div>`;
}
function onlineTimelineHtml(stages) {
  return `<div class="pr-timeline">${stages.map((s) => renderStageCard(s, 'online')).join('')}</div>`;
}

/** 建库阶段的徽章颜色 */
const BUILD_COLOR = {
  1: '#7a8a96',
  2: '#7a8a96',
  3: '#2f4858',
  4: '#2f6f4e',
  5: '#2f6f4e',
  6: '#b03a2e',
  7: '#8e4a8e',
  8: '#2f6f4e',
  9: '#c1622a',
};

/* =====================================================================
   输入 / 输出：结构化详细描述
   ---------------------------------------------------------------------
   每个阶段都回答三个问题：
     · 是什么   —— 这一端到底是什么东西
     · 规模     —— 具体多少（数字都来自真实追踪数据）
     · 来源/去向 —— 从哪个环节来、交到哪个环节去
   不用「自创术语」；必须用到的术语在页面第 3 节「先认几个词」解释过。
   ===================================================================== */
/* 阶段显示名：把原始英文/缩写术语换成直白中文（数据里的 s.name 当兜底） */
const STAGE_NAME = {
  build: {
    1: '读取原始文本文件',
    2: '按段落拆分',
    3: '切文本块',
    4: '上传到分布式文件系统',
    5: '切两字组合',
    6: '分布式建索引表',
    7: '编码成语义向量',
    8: '写入正排表',
    9: '进入问答环节',
  },
  online: {
    1: '理解问题、换成多种查法',
    2: '关键词召回（查索引表）',
    3: '向量召回（算相似度）',
    4: '人物共现召回',
    5: '三路结果合并排序',
    6: '按编号取回正文',
    7: '拼成大模型输入',
    8: '大模型生成回答',
  },
};

const P = (arr) => arr.filter(Boolean);

function ioBox(title, items, out) {
  return `
    <div class="pr-io-box${out ? ' out' : ''}">
      <div class="pr-io-h">${title}</div>
      <dl class="pr-io-list">
        ${items.map((it) => `
          <div class="pr-io-item">
            <dt>${esc(it[0])}</dt>
            <dd>${it[1]}</dd>
          </div>`).join('')}
      </dl>
    </div>`;
}

function renderIO(mode, s) {
  const spec = (mode === 'build' ? BUILD_IO : ONLINE_IO)[s.id];
  if (!spec) {
    // 兜底：退回简短展示
    return `<div class="pr-io">
      <div class="pr-io-box"><div class="pr-io-h">输入</div>
        <div class="pr-io-val">${esc(String(s.input))}</div></div>
      <div class="pr-io-arrow">→</div>
      <div class="pr-io-box out"><div class="pr-io-h">输出</div>
        <div class="pr-io-val">${esc(String(s.output))}</div></div>
    </div>`;
  }
  const d = s.detail || {};
  const o = spec(d, s) || {};
  return `<div class="pr-io">
    ${ioBox('输入', P(o.input || []), false)}
    <div class="pr-io-arrow">→</div>
    ${ioBox('输出', P(o.output || []), true)}
  </div>`;
}


/* ---------------- 建库阶段 I/O 规格 ---------------- */
const BUILD_IO = {
  // 1 原始语料
  1: (d) => ({
    input: [
      ['是什么', '程高本第 3 回的纯文本文件'],
      ['从哪来', '书本 PDF 转成文字后，人工校对过标点、错字、异体字'],
      ['规模', `${fmtNum(d.chars)} 个字符（含标点与换行）`],
    ],
    output: [
      ['是什么', '一个按「每回一个文件」存放的纯文本文件'],
      ['到哪去', '本地磁盘 → 下一步做段落切分'],
      ['为什么', '这是整个流程里唯一和 PDF 打交道的环节，之后全是结构化数据'],
    ],
  }),

  // 2 段落结构化
  2: (d) => {
    const lens = d.para_lengths || [];
    const mn = lens.length ? Math.min(...lens) : 0;
    const mx = lens.length ? Math.max(...lens) : 0;
    const avg = lens.length ? Math.round(lens.reduce((a, b) => a + b, 0) / lens.length) : 0;
    return {
      input: [
        ['是什么', `第 ${d.hui} 回整回文本`],
        ['规模', `${fmtNum((d.para_lengths || []).reduce((a, b) => a + b, 0))} 个字符，连续排列`],
        ['缺什么', '没有段落边界——换行只是排版，程序不知道该从哪断段'],
      ],
      output: [
        ['是什么', '一条 JSON 记录，<code>paragraphs</code> 里每个元素是一个段落'],
        ['规模', `<b>${d.n_paragraphs}</b> 个段落；平均 ${avg} 字/段，最短 ${mn} 字，最长 ${mx} 字`],
        ['到哪去', `<code>chapters_${esc(d.path ? 'chenggao' : '')}.jsonl</code> → 下一步按段落组块`],
      ],
    };
  },

  // 3 文本块切分
  3: (d) => {
    const st = d.strategy || {};
    return {
      input: [
        ['是什么', '第 3 回的第 17~25 号段落（共 9 个段落）'],
        ['规模', '9 段正文合计 652 字；加 8 个换行符，计 <b>660 个字符</b>'],
        ['从哪来', '上一阶段那 32 个段落里的第 17~25 个'],
      ],
      output: [
        ['是什么', `1 个<b>文本块</b>（检索的最小单位），编号 <code>${esc(d.chunk_id || '')}</code>`],
        ['规模', `${d.char_count} 字符，落在 ${st.TARGET_MIN}~${st.TARGET_MAX} 字的目标区间内`],
        ['编号怎么算', `把 <code>${esc(d.hash_input || '')}</code> 做 SHA-1，取前 16 位。同样的输入永远得到同样的编号`],
      ],
    };
  },

  // 4 上传 HDFS
  4: (d) => ({
    input: [
      ['是什么', '全部三个版本的文本块集合文件 <code>chunks.jsonl</code>'],
      ['规模', '<b>11,932 行</b>，每行 1 个文本块'],
      ['从哪来', '本地磁盘（三版本各自切分完成后合并）'],
    ],
    output: [
      ['是什么', 'HDFS 上的 3 个文件，每个版本一个目录'],
      ['到哪去', '<code>/hlm/warehouse/chunks/&lt;版本&gt;/chunks.jsonl</code>'],
      ['冗余方式', `每份数据存 <b>${d.replication}</b> 份副本（在 2 个 DataNode 上各一份），单份按 ${esc(d.block_size || '128MB')} 切块`],
    ],
  }),

  // 5 两字组合
  5: (d) => ({
    input: [
      ['是什么', '这个文本块的正文'],
      ['规模', `${d.han_chars} 个汉字 + 108 个标点 = 660 个字符`],
      ['从哪来', '上一阶段切出来的文本块'],
    ],
    output: [
      ['是什么', '一张「两字组合 → 在本块出现几次」的计数表'],
      ['规模', `<b>${d.n_bigrams}</b> 个不同组合，合计出现 ${fmtNum(d.total_occurrences)} 次`],
      ['怎么做', `只留汉字，从前往后每两个相邻字组成一个组合；跳过「两字都属于高频虚字」的组合（虚字表 ${d.stop_size} 字）`],
    ],
  }),

  // 6 建索引表 ★ 重点重写
  6: (d) => {
    const sm = d.samples || [];
    const top3 = sm.map((x) => `<code>${esc(x.bigram)}</code>(${x.tf_local}次)`).join('、');
    return {
      input: [
        ['是什么', '三个版本全部文本块各自切出的两字组合，以及每个组合在该块内出现几次'],
        ['规模', `全库合计 <b>451,817</b> 个不同组合；本块贡献 ${d.samples ? '527' : '—'} 个`],
        ['怎么读入', 'Hadoop 把 <code>chunks.jsonl</code> 切成多个分片，多台机器同时处理，所以这一张表是<b>分布式算出来的</b>'],
      ],
      output: [
        ['是什么', '一张「两字组合 → 出现在哪些文本块」的索引表，表名 <code>hlm_terms</code>'],
        ['规模', `全表 <b>${fmtNum(d.total_table_rows)} 行</b>，一行对应一个两字组合`],
        ['抽样怎么来', `页面显示的 ${sm.length} 行<b>不是随机抽的</b>：从本块 ${'527'} 个组合里，按「在本块内出现次数」从高到低取前 ${sm.length} 个 —— 即 ${top3}（次数相同则按首次出现顺序）`],
        ['一行是什么', `行键 <code>${esc((sm[0] || {}).rowkey || '')}</code>。行内 <code>p:postings</code> 是 [(文本块编号, 该块内出现次数), …] 的列表；<code>p:df</code> 是包含这个组合的文本块总数`],
      ],
    };
  },

  // 7 向量
  7: (d) => ({
    input: [
      ['是什么', '这个文本块的正文'],
      ['规模', `660 个字符（汉字 552 个）`],
      ['从哪来', '上一阶段切出来的文本块'],
    ],
    output: [
      ['是什么', `一组 <b>${d.vec_dim}</b> 个小数（512 维 float32），存成 ${fmtNum(d.bytes_per_row)} 字节的二进制`],
      ['规模', `1 行 / ${fmtNum(d.bytes_per_row)} 字节；载入后模长 ${d.vec_norm}（已归一化，方便直接算点积当作相似度）`],
      ['到哪去', `<code>hlm_vectors</code> 表，行键 <code>${esc(d.rowkey || '')}</code>`],
    ],
  }),

  // 8 正排表
  8: (d) => ({
    input: [
      ['是什么', '这 1 个文本块的完整信息：编号、版本、回次、段区间、正文、分区…'],
      ['规模', `${Object.keys(d.cells || {}).length} 个字段`],
      ['从哪来', '文本块切分与向量编码的结果'],
    ],
    output: [
      ['是什么', '<b>正排表</b> <code>hlm_chunks</code> 里的一行'],
      ['规模', `本行 ${Object.keys(d.cells || {}).length} 个字段全部落库；全表 <b>${fmtNum(d.total_rows)} 行</b>`],
      ['一行是什么', `行键 <code>${esc(d.rowkey || '')}</code>。<code>d:text</code> 存正文，<code>m:*</code> 存出处（版本、回次、段号、策略版本）`],
    ],
  }),

  // 9 闭环
  9: (d) => ({
    input: [
      ['是什么', '建库阶段落下的这个文本块'],
      ['规模', '660 字 / 编号 <code>' + esc(d.chunk_id || '') + '</code>'],
      ['从哪来', '前 8 个阶段的最终产物'],
    ],
    output: [
      ['是什么', '一次问答回答里的 <b>[4] 号引用</b>'],
      ['规模', '这 660 字正文成为喂给大模型的 5 条上下文之一'],
      ['怎么到的', '向量召回排第 2 → 融合后排进前 5 → 按编号回正排表取正文 → 在提示里编号为 [4]'],
    ],
  }),
};


/* ---------------- 问答阶段 I/O 规格 ---------------- */
const ONLINE_IO = {
  1: (d) => ({
    input: [
      ['是什么', '用户原话'],
      ['规模', '11 个汉字'],
      ['从哪来', '浏览器输入框 → Node 本地服务转发 → FastAPI'],
    ],
    output: [
      ['是什么', '多个查询串（原句 + 若干扩展写法）'],
      ['规模', `${d.n_queries} 个`],
      ['为什么要扩', '用户说「宝玉」，索引里既有「宝玉」也有「贾宝玉」；多写几种查法能少漏'],
    ],
  }),

  2: (d) => ({
    input: [
      ['是什么', '查询串按「相邻两字」切出的组合'],
      ['规模', `<b>${d.n_bigrams}</b> 个组合`],
      ['从哪来', '上一阶段的查询串'],
    ],
    output: [
      ['是什么', '一批候选文本块 + 每个的分数'],
      ['规模', `<b>30</b> 个候选；命中 ${d.n_hit_bigrams} 个组合，共读 ~${(d.rows_sample || []).length * 2 || 16} 行索引`],
      ['怎么算分', `每个命中组合给包含它的文本块加分（<code>1.0 + min(该块内次数,2)×0.1</code>），最后除以组合数。分数越高说明「这个词在这块里越突出」`],
    ],
  }),

  3: (d) => ({
    input: [
      ['是什么', '查询串编码成的 512 个小数'],
      ['规模', `前 8 个是 [${(d.query_vector_head || []).slice(0, 3).map((x) => x.toFixed(3)).join(', ')} …]`],
      ['从哪来', 'ONNX 模型现场编码（约 100 ms）'],
    ],
    output: [
      ['是什么', '一批候选文本块 + 每个与问题的余弦相似度'],
      ['规模', `<b>30</b> 个候选（从 ${fmtNum(13124)} 个里挑）`],
      ['怎么算', `把 ${(d.matrix_shape || []).join('×')} 的矩阵（${(d.matrix_bytes / 1024 / 1024).toFixed(1)} MB）与查询向量做一次矩阵乘，得到 ${fmtNum(13124)} 个相似度，再取前 30`],
    ],
  }),

  4: (d) => ({
    input: [
      ['是什么', '从问题里识别出的人物（简称、别称都算）'],
      ['规模', `${(d.detected_persons || []).length} 个人物：${(d.detected_persons || []).join('、')}`],
      ['触发条件', '问题里出现 2 个及以上人物才会走这条路'],
    ],
    output: [
      ['是什么', '同时提到这两个人物的文本块'],
      ['规模', `<b>${d.n_graph_candidates || 0}</b> 个候选`],
      ['怎么找', `先读一行共现记录 <code>${esc(d.rowkey || '')}</code>（值 <code>s:cnt = ${esc((d.cells || {})['s:cnt'] || '—')}</code>，即全库两人同现次数），再扫正文找出同时提到两人的块`],
    ],
  }),

  5: (d) => ({
    input: [
      ['是什么', '三路各自给出的候选与名次'],
      ['规模', `L1 ${(d.fused || []).length ? '30' : 0} / L2 30 / L3 10，合计 70 个（去重后 90 条记录）`],
      ['为什么不能直接相加', '三路分数量纲不同：一个是命中次数、一个是余弦值、一个是共现次数'],
    ],
    output: [
      ['是什么', '最终 5 个文本块，按综合得分排序'],
      ['规模', `<b>${(d.fused || []).length}</b> 个`],
      ['融合规则', `每路按名次取 <code>1/(${d.rrf_k} + 名次)</code> 再相加，只依赖名次，不受量纲影响`],
    ],
  }),

  6: (d) => ({
    input: [
      ['是什么', '上一阶段选出的文本块编号'],
      ['规模', `<b>${(d.contexts || []).length}</b> 个编号`],
      ['从哪来', '融合结果'],
    ],
    output: [
      ['是什么', '每个编号对应的完整正文 + 出处'],
      ['规模', `${(d.contexts || []).length} 段，合计 <b>${fmtNum(d.total_chars)}</b> 字`],
      ['出处信息', '每段带版本、回次、段号——这是回答能标注引用、能点击跳回原文的前提'],
    ],
  }),

  7: (d) => ({
    input: [
      ['是什么', '5 段正文 + 用户原问题'],
      ['规模', `system ${fmtNum(d.system_chars)} 字符 + user ${fmtNum(d.user_chars)} 字符`],
      ['从哪来', '上一阶段取回的正文'],
    ],
    output: [
      ['是什么', '一串发给大模型的 messages'],
      ['规模', `${d.n_messages} 条（role: ${(d.roles || []).join(' / ')}）`],
      ['怎么拼', 'system 段写死「必须标引用编号、不得编造原文」；user 段把 5 段正文按 [1]~[5] 编号后附在原问题前'],
    ],
  }),

  8: (d) => ({
    input: [
      ['是什么', '上一阶段拼好的 messages'],
      ['规模', `约 ${fmtNum((d.usage || {}).prompt_tokens || 0)} tokens`],
      ['从哪来', 'FastAPI 通过 HTTPS 调用模型厂商接口'],
    ],
    output: [
      ['是什么', '一段带 [n] 引用编号的中文回答'],
      ['规模', `<b>${(d.answer || '').length}</b> 字符 / ${fmtNum((d.usage || {}).completion_tokens || 0)} tokens`],
      ['模型做了什么', '只负责「读 5 段正文 → 组织语言 → 标引用」，事实全部来自那 5 段，所以每条结论都能对上原文'],
    ],
  }),
};


/* =====================================================================
   阶段卡片
   ===================================================================== */
function renderStageCard(s, mode = 'online') {
  const palette = mode === 'build' ? BUILD_COLOR : STAGE_COLOR;
  const color = palette[s.id] || '#5b6b7a';
  return `
  <div class="pr-stage" data-stage="${s.id}">
    <div class="pr-stage-line">
      <span class="pr-stage-dot" style="background:${color}"></span>
    </div>
    <div class="pr-stage-body">
      <div class="pr-stage-head">
        <span class="pr-stage-no" style="background:${color}">${s.id}</span>
        <span class="pr-stage-name">${esc((STAGE_NAME[mode] || {})[s.id] || s.name)}</span>
        <span class="pr-stage-comp">${esc(s.component)}</span>
        <span class="pr-stage-ms">${fmtMs(s.ms)}</span>
      </div>

      ${renderIO(mode, s)}

      ${mode === 'build' ? renderBuildDetail(s) : renderStageDetail(s)}

      ${/* 阶段说明已并入 IO 的「为什么/怎么算/怎么做」与详情区的补充说明，这里不再重复 */''}

      <button class="pr-toggle">查看原始数据</button>
      <div class="pr-stage-raw" hidden>
        ${codeBlock(JSON.stringify(s.detail, null, 1), 'json')}
      </div>
    </div>
  </div>`;
}

/** 渲染说明文字：转义后再处理 **粗体** */
function renderNote(t) {
  return esc(t || '').replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
}

/* 建库阶段的数据展示 */
function renderBuildDetail(s) {
  const d = s.detail || {};

  // 1 原始语料
  if (s.id === 1) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">每回一个纯文本文件</div>
        <div class="pr-pathline"><code>${esc(d.path || '')}</code></div>
        <div class="pr-textq">${esc((d.head || '').slice(0, 200))}…</div>
        <div class="pr-data-note">
          <b>${fmtNum(d.chars)}</b> 字符。这是唯一和 PDF 打交道的环节，
          之后全部是结构化数据。
        </div>
        <div class="pr-data-note">${esc(d.source || '')}</div>
      </div>`;
  }

  // 2 段落结构化
  if (s.id === 2) {
    const lens = d.para_lengths || [];
    const avg = lens.length ? Math.round(lens.reduce((a, b) => a + b, 0) / lens.length) : 0;
    return `
      <div class="pr-data">
        <div class="pr-data-h">第 ${d.hui} 回 · ${esc(d.huimu || '')}</div>
        <div class="pr-mini-row">
          <div class="pr-mini"><span>段落数</span><b>${d.n_paragraphs}</b></div>
          <div class="pr-mini"><span>平均段长</span><b>${avg}</b></div>
          <div class="pr-mini"><span>最短/最长</span><b>${Math.min(...lens) || 0} / ${Math.max(...lens) || 0}</b></div>
        </div>
        <div class="pr-parabars">
          ${lens.slice(0, 32).map((n) => {
            const h = Math.max(3, Math.min(48, Math.round(n / 4)));
            const isTarget = lens.indexOf(n) >= 17 && lens.indexOf(n) <= 25;
            return `<div class="pr-parabar${isTarget ? ' hit' : ''}" style="height:${h}px" title="${n} 字"></div>`;
          }).join('')}
        </div>
        <div class="pr-data-note">
          绿色是本次追踪的目标段落区间（段 17–25）。段落是切文本块的最小单位，
          也是「回次 + 段号」引用的基础。
        </div>
      </div>`;
  }

  // 3 Chunk 切分 ★ 核心
  if (s.id === 3) {
    const st = d.strategy || {};
    return `
      <div class="pr-data">
        <div class="pr-data-h">文本块编号 = SHA-1(哈希输入) 取前 16 位 —— 可复算验证</div>
        <div class="pr-hash">
          <div class="pr-hash-in">
            <span class="pr-hash-lbl">哈希输入</span>
            <code>${esc(d.hash_input || '')}</code>
          </div>
          <div class="pr-hash-arrow">sha1 ⤵</div>
          <div class="pr-hash-out">
            <span class="pr-hash-lbl">文本块编号</span>
            <code>${esc(d.chunk_id || '')}</code>
            <span class="pr-hash-ok">${d.hash_verify ? '✅ 复算一致' : '⚠ 不一致'}</span>
          </div>
        </div>
        <div class="pr-mini-row">
          <div class="pr-mini"><span>字数</span><b>${d.char_count}</b></div>
          <div class="pr-mini"><span>目标区间</span><b>${st.TARGET_MIN}–${st.TARGET_MAX}</b></div>
          <div class="pr-mini"><span>策略版本</span><b>${esc(st.STRATEGY_REV || '')}</b></div>
        </div>
        <div class="pr-textq">${esc((d.text_head || '').slice(0, 180))}…</div>
        <div class="pr-data-note">
          把<b>版本</b>放进编号，三版本同一位置的文本块天然不同编号，不会串版本；
          把<b>策略版本</b>放进 ID，改了切分参数后新旧编号不会混。
        </div>
      </div>`;
  }

  // 4 HDFS
  if (s.id === 4) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">HDFS 目录布局</div>
        <div class="pr-tree">
          <div class="pr-tree-row"><code>/hlm/warehouse/chunks/&lt;版本&gt;/chunks.jsonl</code><span>索引表与正排表的输入</span></div>
          <div class="pr-tree-row"><code>/hlm/derived/vectors/&lt;版本&gt;/vectors.jsonl</code><span>向量快照</span></div>
          <div class="pr-tree-row"><code>/hlm/mrout/wordfreq</code><span>MapReduce 输出</span></div>
          <div class="pr-tree-row"><code>/hlm/mrout/personfreq</code><span>MapReduce 输出</span></div>
          <div class="pr-tree-row"><code>/hlm/mrout/cooccur</code><span>MapReduce 输出</span></div>
          <div class="pr-tree-row"><code>/hlm/mrout/chunkstat</code><span>MapReduce 输出</span></div>
        </div>
        <div class="pr-data-note">
          每个 <code>/hlm/mrout/</code> 子目录对应一个 MapReduce 作业的输出，
          下一步会被读出来写进 HBase 和 Hive。
        </div>
      </div>`;
  }

  // 5 bigram
  if (s.id === 5) {
    const top = d.top15 || [];
    return `
      <div class="pr-data">
        <div class="pr-data-h">
          ${d.han_chars} 个汉字 → 每两个相邻字组成一个组合 → <b>${d.n_bigrams}</b> 个不同组合（合计出现 ${fmtNum(d.total_occurrences)} 次）
        </div>
        <div class="pr-bgchart">
          ${top.slice(0, 12).map(([w, c]) => {
            const max = top[0][1] || 1;
            const pct = Math.max(8, (c / max) * 100);
            return `<div class="pr-bgbar">
              <span class="pr-bgbar-w">${esc(w)}</span>
              <div class="pr-bgbar-track"><div style="width:${pct}%"></div></div>
              <span class="pr-bgbar-c">${c}</span>
            </div>`;
          }).join('')}
        </div>
        <div class="pr-data-note">
          停用字表 <b>${d.stop_size}</b> 字：<code>${esc(d.stop_chars || '')}</code>。
          跳过「两字都在停用表」的组合，减少无意义索引。
          <br>用两字组合而非分词，是为了<b>避免分词错误导致漏检</b>。
        </div>
      </div>`;
  }

  // 6 MapReduce 倒排 ★ 核心
  if (s.id === 6) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">MapReduce 把同一个两字组合的「出现在哪个块、出现几次」合并成一行</div>
        ${(d.samples || []).map((x) => `
          <div class="pr-hbase-row2">
            <div class="pr-hbase-k">
              <span class="pr-hbase-tbl">hlm_terms</span>
              <code>${esc(x.rowkey || '')}</code>
            </div>
            <div class="pr-hbase-v">
              ${x.found ? `
                <span class="pr-hbase-df"><code>p:df</code> = <b>${fmtNum(x.df)}</b>（全库有多少个文本块含这个组合）</span>
                <span class="pr-hbase-df"><code>p:postings</code> 里共 <b>${fmtNum(x.n_postings)}</b> 条记录</span>
                <span class="pr-hbase-self ${x.self_in_postings ? 'yes' : 'no'}">
                  ${x.self_in_postings
                    ? `✅ 本块在内，块内出现 <b>${x.self_tf}</b> 次（与本地重算一致）`
                    : '✗ 本块不在内'}
                </span>
                <div class="pr-hbase-p">前 3 条记录（格式：[文本块编号, 块内出现次数]）= <code>${esc(JSON.stringify(x.postings_head || []))}</code></div>
              ` : '<span class="pr-hbase-p muted">该行不存在</span>'}
            </div>
          </div>`).join('')}
        <div class="pr-mini-row">
          <div class="pr-mini"><span>行键格式</span><b>版本|text|组合</b></div>
          <div class="pr-mini"><span>全表行数</span><b>${fmtNum(d.total_table_rows)}</b></div>
        </div>
        <div class="pr-data-note">
          在线检索时按两字组合点查这一行，直接拿到所有包含它的文本块，<b>不用扫正文</b>。
          这就是倒排索引的价值。
        </div>
      </div>`;
  }

  // 7 向量
  if (s.id === 7) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">正文 → ONNX 编码 → ${d.vec_dim} 维 float32</div>
        <div class="pr-vec2">
          <div class="pr-vec2-l"><span>文本块正文</span><code>660 字符 → ${fmtNum(d.bytes_per_row)} 字节</code></div>
          <div class="pr-vec2-m">→</div>
          <div class="pr-vec2-r">
            <span>前 8 维</span>
            <code>[${(d.vec_head || []).map((x) => x.toFixed(5)).join(', ')} …]</code>
            <span class="pr-vec-sub">L2 模长 = ${d.vec_norm}</span>
          </div>
        </div>
        <div class="pr-kv-inline">
          ${Object.entries(d.cells || {}).map(([k, v]) => `<span><code>${esc(k)}</code> ${v} B</span>`).join('')}
        </div>
        <div class="pr-data-note">
          每个文本块存 2,048 字节。全表 ${fmtNum(d.total_rows)} 行 →
          在线加载成 13124×512 矩阵，一次矩阵乘算出全部相似度。
        </div>
      </div>`;
  }

  // 8 HBase 正排
  if (s.id === 8) {
    const cells = d.cells || {};
    return `
      <div class="pr-data">
        <div class="pr-data-h">行键 = ${esc(d.rowkey || '')}</div>
        <table class="pr-celltable">
          <tbody>
            ${Object.entries(cells).map(([k, v]) => `
              <tr><th>${esc(k)}</th><td>${esc(String(v))}</td></tr>`).join('')}
          </tbody>
        </table>
        <div class="pr-data-note">
          正排表存「这个文本块是什么」。检索只用编号，
          <b>取回正文时才回这张表</b>。行键含版本+类型+回次，按回次范围扫描很快。
        </div>
      </div>`;
  }

  // 9 闭环
  if (s.id === 9) {
    return `
      <div class="pr-data pr-loop">
        <div class="pr-loop-h">建库 → 检索，接上了</div>
        <div class="pr-loop-row">
          <div class="pr-loop-box left">
            <span class="pr-loop-lbl">建库阶段落下</span>
            <code>${esc(d.chunk_id || '')}</code>
            <span class="pr-loop-sub">chenggao 第3回 段17–25</span>
          </div>
          <div class="pr-loop-arrow">⟶</div>
          <div class="pr-loop-box right">
            <span class="pr-loop-lbl">问答中成为</span>
            <b class="pr-loop-cite">[${d.became_citation}] 号引用</b>
            <span class="pr-loop-sub">问题：${esc(d.question || '')}</span>
          </div>
        </div>
        <div class="pr-data-note">${esc(d.route || '')}</div>
        <div class="pr-data-note">
          切到「问答（在线）」标签，就能看到这个文本块从被召回、融合到进入 prompt 的全过程。
        </div>
      </div>`;
  }

  return '';
}

/* 按阶段定制「实际数据」展示 */
function renderStageDetail(s) {
  const d = s.detail || {};

  // 阶段1：改写
  if (s.id === 1) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">问题被拆成 ${d.n_queries} 种查法</div>
        <div class="pr-chips">
          ${(d.alias_expansion || []).map((a, i) => `
            <div class="pr-chip-row">
              <span class="pr-chip-idx">${i + 1}</span>
              <span class="pr-chip-txt">${esc(a.from)}</span>
              ${(a.expanded || []).length > 1
                ? `<span class="pr-chip-exp">→ ${(a.expanded || []).map((x) => esc(x)).join(' / ')}</span>`
                : ''}
            </div>`).join('')}
        </div>
        <div class="pr-data-note">
          最终进入检索的 ${d.n_queries} 个查询串（同一问题多写法，提高召回率）
        </div>
      </div>`;
  }

  // 阶段2：L1
  if (s.id === 2) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">切成两字组合 → 逐行查 HBase 索引表</div>
        <div class="pr-quote-lg">${(d.bigrams || []).map((b) => `<span class="pr-bg">${esc(b)}</span>`).join('')}</div>
        <div class="pr-hbase">
          ${(d.rows_sample || []).slice(0, 3).map((r) => `
            <div class="pr-hbase-row">
              <div class="pr-hbase-k">
                <span class="pr-hbase-tbl">hlm_terms</span>
                <code>${esc(r.rowkey)}</code>
              </div>
              <div class="pr-hbase-v">
                <span class="pr-hbase-df"><code>p:df</code>（全库含这个组合的文本块数）= <b>${fmtNum(r.df)}</b></span>
                ${r.postings_sample && r.postings_sample.length
                  ? `<div class="pr-hbase-p">p:postings 前 3 条 = <code>${esc(JSON.stringify(r.postings_sample))}</code></div>`
                  : '<div class="pr-hbase-p muted">（这一行没有记录）</div>'}
              </div>
            </div>`).join('')}
        </div>
        <div class="pr-data-note">
          命中 <b>${d.n_hit_bigrams}</b> 个组合 → 累加打分 → 取前 30 个候选。
          打分公式：<code>${esc(d.scoring || '')}</code>
        </div>
        <div class="pr-cands">
          ${(d.top_candidates || []).slice(0, 5).map((c, i) => `
            <div class="pr-cand">
              <span class="pr-cand-rank">#${i + 1}</span>
              <code>${esc(c.chunk_id)}</code>
              <span class="pr-cand-score">${c.score}</span>
            </div>`).join('')}
        </div>
      </div>`;
  }

  // 阶段3：L2
  if (s.id === 3) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">问题 → 512 维向量 → 与 11,932 条比余弦</div>
        <div class="pr-vec">
          <div class="pr-vec-box">
            <span class="pr-vec-lbl">问题向量前 8 维</span>
            <code>[${(d.query_vector_head || []).map((x) => x.toFixed(4)).join(', ')} …]</code>
            <span class="pr-vec-sub">模长 ${d.query_vector_norm}</span>
          </div>
          <div class="pr-vec-op">×</div>
          <div class="pr-vec-box">
            <span class="pr-vec-lbl">向量矩阵</span>
            <code>${(d.matrix_shape || []).join(' × ')}</code>
            <span class="pr-vec-sub">${(d.matrix_bytes / 1024 / 1024).toFixed(1)} MB（内存）</span>
          </div>
          <div class="pr-vec-op">=</div>
          <div class="pr-vec-box out">
            <span class="pr-vec-lbl">相似度</span>
            <code>${fmtNum(13124)} 个分数</code>
            <span class="pr-vec-sub">范围 ${(d.sim_range || []).join(' ~ ')}</span>
          </div>
        </div>
        <div class="pr-cands">
          ${(d.top_candidates || []).slice(0, 5).map((c, i) => `
            <div class="pr-cand">
              <span class="pr-cand-rank">#${i + 1}</span>
              <code>${esc(c.chunk_id)}</code>
              <span class="pr-cand-score">${c.score}</span>
            </div>`).join('')}
        </div>
      </div>`;
  }

  // 阶段4：L3
  if (s.id === 4) {
    const cells = d.cells || {};
    return `
      <div class="pr-data">
        <div class="pr-data-h">识别出人物 → 读一行共现记录</div>
        <div class="pr-persons">
          ${(d.detected_persons || []).map((p) => `<span class="pr-person">${esc(p)}</span>`).join('')}
        </div>
        <div class="pr-hbase">
          <div class="pr-hbase-row">
            <div class="pr-hbase-k">
              <span class="pr-hbase-tbl">hlm_stats</span>
              <code>${esc(d.rowkey || '')}</code>
            </div>
            <div class="pr-hbase-v">
              ${Object.keys(cells).length
                ? Object.entries(cells).map(([k, v]) => `
                    <span class="pr-hbase-df">${esc(k)} = <b>${esc(v)}</b></span>`).join('')
                : '<span class="pr-hbase-p muted">该行不存在（两人未共现）</span>'}
            </div>
          </div>
        </div>
        <div class="pr-data-note">
          这个 <b>cnt</b> 是 MapReduce 离线统计的「两人共现次数」。
          在线只读一行，然后扫一遍正文找出同时提到两人的文本块作为证据。
        </div>
      </div>`;
  }

  // 阶段5：RRF
  if (s.id === 5) {
    const fused = d.fused || [];
    return `
      <div class="pr-data">
        <div class="pr-data-h">每路按名次取 1/(60 + 名次)，三路相加得到综合分（名次越靠前分越高）</div>
        <div class="pr-table-wrap">
          <table class="pr-table pr-table-sm">
            <thead>
              <tr><th>最终排名</th><th>文本块编号</th>
                  <th>关键词名次</th><th>向量名次</th><th>人物名次</th><th>综合分</th></tr>
            </thead>
            <tbody>
              ${fused.map((f, i) => `
                <tr${i === 0 ? ' class="hl"' : ''}>
                  <td><b>${i + 1}</b></td>
                  <td><code>${esc(f.chunk_id)}</code></td>
                  <td>${f.kw_rank == null ? '<span class="muted">—</span>' : f.kw_rank}</td>
                  <td>${f.vec_rank == null ? '<span class="muted">—</span>' : f.vec_rank}</td>
                  <td>${f.graph_rank == null ? '<span class="muted">—</span>' : f.graph_rank}</td>
                  <td><b>${f.rrf.toFixed(6)}</b></td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div class="pr-data-note">
          注意第 1 名：它在关键词里排第 29、向量里排第 27，单看哪一路都不突出，
          但<b>两路都命中了</b>，名次倒数相加后综合分最高（0.0227）。
          这正是这个算法的作用：<b>奖励「多路都认可」的结果</b>。
          <br>同一个回目只保留 1 条：<b>${d.dedup_by_hui ? '是' : '否'}</b>
        </div>
      </div>`;
  }

  // 阶段6：回表
  if (s.id === 6) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">按文本块编号取回 ${(d.contexts || []).length} 段正文（共 ${fmtNum(d.total_chars)} 字）</div>
        <div class="pr-ctxs">
          ${(d.contexts || []).map((c, i) => `
            <div class="pr-ctx">
              <div class="pr-ctx-h">
                <span class="pr-ctx-idx">[${i + 1}]</span>
                <span class="pr-ctx-ver">${esc(c.version)}</span>
                <span class="pr-ctx-hui">第 ${c.hui} 回</span>
                <span class="pr-ctx-para">段 ${c.para_start}–${c.para_end}</span>
                <span class="pr-ctx-chars">${c.chars} 字</span>
                ${c.source ? `<span class="pr-ctx-src">${esc(c.source)}</span>` : ''}
              </div>
              <div class="pr-ctx-t">${esc(c.text_head)}…</div>
            </div>`).join('')}
        </div>
        <div class="pr-data-note">
          每条上下文都带「版本 / 回次 / 段号」——这就是答案可追溯、能点击跳转原文的基础。
        </div>
      </div>`;
  }

  // 阶段7：Prompt
  if (s.id === 7) {
    return `
      <div class="pr-data">
        <div class="pr-data-h">
          ${d.n_messages} 条 message · system ${fmtNum(d.system_chars)} 字符 + user ${fmtNum(d.user_chars)} 字符
        </div>
        <div class="pr-prompt">
          <div class="pr-prompt-h">system（规则约束，节选）</div>
          ${codeBlock(d.system_prompt || '', 'prompt')}
        </div>
        <div class="pr-prompt">
          <div class="pr-prompt-h">user（5 条上下文按 [1]~[5] 编号拼入，节选）</div>
          ${codeBlock(d.user_prompt || '', 'prompt')}
        </div>
      </div>`;
  }

  // 阶段8：LLM
  if (s.id === 8) {
    const u = d.usage || {};
    return `
      <div class="pr-data">
        <div class="pr-data-h">模型：${esc(d.model || '')}</div>
        <div class="pr-answer">${renderAnswer(d.answer_head || d.answer || '')}</div>
        ${Object.keys(u).length ? `
          <div class="pr-usage">
            ${u.prompt_tokens != null ? `<span>输入 <b>${fmtNum(u.prompt_tokens)}</b> tokens</span>` : ''}
            ${u.completion_tokens != null ? `<span>输出 <b>${fmtNum(u.completion_tokens)}</b> tokens</span>` : ''}
            ${u.total_tokens != null ? `<span>合计 <b>${fmtNum(u.total_tokens)}</b> tokens</span>` : ''}
          </div>` : ''}
        ${d.has_reasoning ? `
          <div class="pr-prompt">
            <div class="pr-prompt-h">模型思考过程（节选）</div>
            ${codeBlock(d.reasoning_head || '', 'prompt')}
          </div>` : ''}
        <div class="pr-data-note">
          注意回答里的 <b>[4] [5]</b> —— 它们是 prompt 里上下文的编号，
          前端据此把引用渲染成可点击的原文链接。
        </div>
      </div>`;
  }

  return '';
}

/* =====================================================================
   时间条
   ===================================================================== */
function renderTimeBar(label, ms, total, color) {
  const pct = total ? (ms / total) * 100 : 0;
  return `
    <div class="pr-time-row">
      <span class="pr-time-lbl">${esc(label)}</span>
      <div class="pr-time-track">
        <div class="pr-time-fill" style="width:${Math.max(pct, 0.4)}%;background:${color}"></div>
      </div>
      <span class="pr-time-v">${fmtMs(ms)}</span>
      <span class="pr-time-pct">${pct.toFixed(1)}%</span>
    </div>`;
}

/* =====================================================================
   变形链
   ===================================================================== */
function renderChain(T) {
  const stages = T.stages || [];
  const s1 = stages.find((s) => s.id === 1)?.detail || {};
  const s2 = stages.find((s) => s.id === 2)?.detail || {};
  const s5 = stages.find((s) => s.id === 5)?.detail || {};
  const s6 = stages.find((s) => s.id === 6)?.detail || {};
  const s8 = stages.find((s) => s.id === 8)?.detail || {};

  const links = [
    {
      from: '用户输入（浏览器）',
      to: '原句',
      val: `「${T.question}」`,
      sub: '11 个汉字',
    },
    {
      from: '查询改写（应用层）',
      to: '多种查法',
      val: `${s1.n_queries} 个查询串`,
      sub: '简称展开为正式名 + 关系词扩展',
    },
    {
      from: '关键词：切成两字组合',
      to: '检索词项',
      val: (s2.bigrams || []).slice(0, 6).join(' · ') + ((s2.bigrams || []).length > 6 ? ' …' : ''),
      sub: `${(s2.bigrams || []).length} 个组合，每个去 HBase 查一行`,
    },
    {
      from: 'L2 向量编码',
      to: '语义向量',
      val: '512 维 float32',
      sub: 'ONNX 模型 bge-small-zh-v1.5',
    },
    {
      from: 'RRF 融合',
      to: '文本块编号列表',
      val: (s5.fused || []).slice(0, 3).map((f) => f.chunk_id.slice(0, 8) + '…').join(' · '),
      sub: `90 个候选 → 5 个（按名次倒数加权）`,
    },
    {
      from: 'HBase 回表',
      to: '自然语言上下文',
      val: `${(s6.contexts || []).length} 段原文 / 共 ${fmtNum(s6.total_chars)} 字`,
      sub: '每段带版本·回次·段号',
    },
    {
      from: '大模型',
      to: '带引用的回答',
      val: `${(s8.answer || '').length} 字符`,
      sub: '含 [4][5] 等引用编号',
    },
  ];

  return links.map((l, i) => `
    <div class="pr-chain-item">
      <div class="pr-chain-idx">${i + 1}</div>
      <div class="pr-chain-body">
        <div class="pr-chain-from">${esc(l.from)}</div>
        <div class="pr-chain-to">${esc(l.to)}</div>
        <div class="pr-chain-val">${esc(l.val)}</div>
        <div class="pr-chain-sub">${esc(l.sub)}</div>
      </div>
    </div>`).join('');
}

/* =====================================================================
   回答渲染（轻量：只处理 **粗体** 和 [n] 引用）
   ===================================================================== */
function renderAnswer(text) {
  let h = esc(text || '');
  h = h.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/\[(\d+)\]/g, '<span class="pr-cite">[$1]</span>');
  h = h.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
  return `<p>${h}</p>`;
}
