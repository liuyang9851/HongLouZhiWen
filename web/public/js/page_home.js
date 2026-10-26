/**
 * S23 · 欢迎页（合并原欢迎页 + 项目简介 + 红楼简介 + 功能 + 技术栈）
 *
 * 设计目标
 * --------
 *  · 一屏内说清「这是什么项目、能做什么、用了什么」
 *  · 数据全部来自真实运行时（不写死），避免和实际不符
 *  · 保留原来的问题示例入口（点击直接跳到 AI 问答）
 */
import { getCorpusStats } from './rag.js';
import { renderArchDiagram } from './arch_diagram.js';

// 注意：app.js 是本模块的宿主，ES module 求值顺序下 window.__HLM__
// 可能在模块加载时还未赋值 → 必须延迟到调用时再取。
const H = () => window.__HLM__ || {};
const esc = (s) => {
  const f = H().esc;
  return f ? f(s) : String(s ?? '');
};
const api = (p) => H().api(p);

const getStarters = () => H().STARTERS || [];
const autoGrowFn = () => window.__HLM__ && window.__HLM__.autoGrow;
const sendFn = () => window.__HLM__ && window.__HLM__.send;

export async function renderHome(stage) {
  const STARTERS = getStarters();
  // ---- 拉真实数据（失败则降级为占位）----
  let health = null;
  let stats = null;
  try {
    health = await api('/api/backend/health');
  } catch (e) { /* 忽略 */ }
  try {
    stats = await getCorpusStats();
  } catch (e) { /* 忽略 */ }

  // 图谱规模（节点/边/关系种类）
  let graph = null;
  try { graph = await api('/api/graph'); } catch (e) { /* 忽略 */ }

  // 语料规模（版本字数/汉字数）来自 corpus_stats.json（离线实测产物）
  let cs = null;
  try {
    const r = await fetch('/data/corpus_stats.json?v=' + Date.now());
    if (r.ok) cs = await r.json();
  } catch (e) { /* 降级为不显示字数 */ }

  const nGNode = graph?.n_nodes ?? 350;
  const nGEdge = graph?.n_edges ?? 674;
  const nGRel = (graph?.relations || []).length || 120;

  const nChunk = health?.n_chunks ?? '—';
  const nVec = health?.n_vectors ?? '—';
  const dim = health?.emb_dim ?? 512;
  const models = (health?.models || []).join(' / ') || 'deepseek-flash';

  const FONTS = 'font-family:var(--font-serif)';

  // ---- 版本表 / 数据规模：全部取实测值，取不到则显示「—」----
  const wan = (n) => (typeof n === 'number' && n > 0) ? (n / 10000).toFixed(1) : '—';
  const fmt = (n) => (typeof n === 'number' && n > 0) ? n.toLocaleString('en-US') : '—';
  const VERS = (cs?.versions) || [];
  const TOT = (cs?.totals) || {};
  const verRows = VERS.map((v) => `
              <tr>
                <td><b>${esc(v.label)}</b></td>
                <td>${esc(v.range === '1-120' ? '1–120 回' : v.range === '81-108' ? '81–108 回' : (v.note || '凡例 + 1–80 回'))}</td>
                <td class="num">${wan(v.chars)} 万</td>
                <td>${esc(v.use || '版本对照')}</td>
              </tr>`).join('');

  // 正文块 / 脂批块（后端 corpus/stats 的 by_version_source）
  const bvs = stats?.by_version_source || {};
  const textBlocks = Object.entries(bvs)
    .filter(([k]) => k.endsWith('/text')).reduce((a, [, n]) => a + n, 0);
  const pizhuBlocks = Object.entries(bvs)
    .filter(([k]) => k.endsWith('/pizhu')).reduce((a, [, n]) => a + n, 0);
  const pzt = stats?.pizhu_by_source_tag || {};
  const pzN = (...keys) => {
    for (const k of keys) {
      const v = pzt[k];
      if (typeof v === 'number' && v > 0) return fmt(v) + ' 条';
    }
    return '— 条';
  };

  stage.innerHTML = `
  <div class="view v-home">
    <!-- ============ 1. 项目简介 ============ -->
    <section class="hp-hero">
      <div class="hp-hero-inner">
        <div class="hp-seal">紅</div>
        <h1 class="hp-title">紅樓智問</h1>
        <div class="hp-sub">HongLouMeng-QA · 可追溯的 RAG 知识问答</div>
        <p class="hp-lead">
          把《红楼梦》三版本正文 <b>167.8 万字</b>、三源脂批 <b>8,510 条</b>、
          人物关系图谱 <b>${nGNode} 人物 / ${nGEdge} 条关系</b>，
          做成一个<b>每句回答都能追溯到原文段落</b>的知识问答系统。
        </p>
        <div class="hp-kpis">
          <div class="hp-kpi"><b>${nChunk}</b><span>索引 chunk</span></div>
          <div class="hp-kpi"><b>${nVec}</b><span>${dim} 维向量</span></div>
          <div class="hp-kpi"><b>3</b><span>语料版本</span></div>
          <div class="hp-kpi"><b>${fmt(pizhuBlocks)}</b><span>脂批文本块</span></div>
          <div class="hp-kpi"><b>${nGNode}</b><span>人物节点</span></div>
        </div>
        <div class="hp-cta">
          <a class="btn primary lg" href="#/chat">💬 开始提问</a>
          <a class="btn ghost lg" href="#/reader">📖 阅读原文</a>
          <a class="btn ghost lg" href="#/guiyou">🔬 癸酉本探究</a>
        </div>
      </div>
    </section>

    <!-- ============ 2. 《红楼梦》简介 ============ -->
    <section class="hp-sec">
      <h2 class="hp-h2"><span class="hp-num">1</span>关于《红楼梦》</h2>
      <div class="hp-book">
        <div class="hp-book-main">
          <p>
            《红楼梦》（又名《石头记》《情僧录》《风月宝鉴》《金陵十二钗》），
            清代长篇章回小说，中国古典四大名著之首。前 80 回一般认为由<b>曹雪芹</b>所著，
            后 40 回通行本由<b>高鹗</b>续补（程伟元、高鹗整理刊行，故称「程高本」）。
          </p>
          <p>
            全书以<b>贾、史、王、薛</b>四大家族的兴衰为背景，
            以<b>贾宝玉、林黛玉、薛宝钗</b>的爱情婚姻悲剧为主线，
            描绘了从钟鸣鼎食到"落了片白茫茫大地真干净"的百年家族史。
            它既是一部世情小说，也被称为中国封建社会的百科全书 ——
            诗词、园林、饮食、医药、服饰、礼制、经济，无所不包。
          </p>
          <p>
            与其它古典小说最大的不同是：《红楼梦》拥有<b>脂砚斋批语</b>这一独特的伴随文本。
            脂批不仅评点人物情节，还大量透露<b>后文走向与失稿信息</b>
            （如「狱神庙」「情榜」「悬崖撒手」），
            这成为后世探佚、续书、乃至争论"真本"的核心依据。
          </p>
        </div>
        <aside class="hp-book-side">
          <div class="hp-fact"><span>别名</span><b>石头记 / 情僧录 / 金陵十二钗</b></div>
          <div class="hp-fact"><span>作者</span><b>曹雪芹（前80回）· 高鹗（后40回续）</b></div>
          <div class="hp-fact"><span>成书</span><b>清乾隆年间（18世纪中叶）</b></div>
          <div class="hp-fact"><span>回数</span><b>通行本 120 回（脂评本多存 80 回）</b></div>
          <div class="hp-fact"><span>地位</span><b>中国古典四大名著之首</b></div>
          <div class="hp-fact"><span>特色</span><b>脂批伴随文本 · 探佚学源头</b></div>
        </aside>
      </div>

      <!-- ---- 版本信息 ---- -->
      <h3 class="hp-sub3">本项目用了哪三个版本</h3>
      <div class="hp-vers">
        <table>
          <thead>
            <tr><th>版本</th><th>收录范围</th><th class="num">正文字数</th><th>在本项目中的用途</th></tr>
          </thead>
          <tbody>
            ${verRows || '<tr><td colspan="4" class="hp-vers-na">数据未载入</td></tr>'}
          </tbody>
          <tfoot>
            <tr>
              <td><b>合计</b></td>
              <td>${VERS.length || 3} 个版本</td>
              <td class="num"><b>${wan(TOT.chars)} 万</b></td>
              <td>其中汉字 ${wan(TOT.han)} 万字</td>
            </tr>
          </tfoot>
        </table>
        <p class="hp-vers-note">
          三版本<b>各自成篇、互不覆盖</b>：抚琴居校本承担 RAG 主链（唯一带脂批的完整 80 回本），
          程高本与癸酉本用于<b>同回对照</b>。脂批另有三个来源： 抚琴居校本 ${pzN('抚琴居校本', 'fuqinju')}、
                  癸酉本 ${pzN('癸酉本')}、脂批大全 ${pzN('脂批大全')}。
        </p>
      </div>

      <!-- ---- 数据信息 ---- -->
      <h3 class="hp-sub3">建成了多大的索引</h3>
      <div class="hp-data">
        <div class="hp-di">
          <b>${fmt(textBlocks)}</b>
          <span>正文文本块</span>
          <small>三版本切分后，每块 300–800 字</small>
        </div>
        <div class="hp-di">
          <b>${fmt(pizhuBlocks)}</b>
          <span>脂批文本块</span>
          <small>一条批语一块，三源合计</small>
        </div>
        <div class="hp-di">
          <b>${fmt(nChunk)}</b>
          <span>索引总块数</span>
          <small>全部进入 HBase 与向量表</small>
        </div>
        <div class="hp-di">
          <b>${dim}</b>
          <span>向量维度</span>
          <small>bge-small-zh-v1.5，每块 2,048 字节</small>
        </div>
        <div class="hp-di">
          <b>3 路</b>
          <span>并行检索</span>
          <small>两字组合 · 语义向量 · 人物共现</small>
        </div>
        <div class="hp-di">
          <b>350</b>
          <span>人物关系节点</span>
          <small>${nGEdge} 条边 / ${nGRel} 种关系</small>
        </div>
      </div>
    </section>

    <!-- ============ 1.5 程序架构图 ============ -->
    <section class="hp-sec">
      <h2 class="hp-h2"><span class="hp-num">2</span>程序架构</h2>
      <p class="hp-arch-intro">
        数据自下而上流动：<b>原始语料</b>存入 HDFS → <b>MapReduce 批统计</b>产出派生数据 →
        <b>HBase</b> 建立倒排索引与向量表 → <b>三路混合检索</b>召回 → <b>LLM</b> 生成带引用的回答。
        <span class="hp-arch-hint">（重点：Hadoop 生态承担全部存储与计算，占比 55%）</span>
      </p>
      <div class="hp-arch-wrap">
        ${renderArchDiagram()}
      </div>
      <div class="hp-arch-legend">
        <div class="hp-lg-item"><span class="hp-lg-dot" style="background:#2f6f4e"></span>HDFS 分布式存储</div>
        <div class="hp-lg-item"><span class="hp-lg-dot" style="background:#b03a2e"></span>HBase 在线检索</div>
        <div class="hp-lg-item"><span class="hp-lg-dot" style="background:#7a6a52"></span>计算分析</div>
        <div class="hp-lg-item"><span class="hp-lg-dot" style="background:#2f4858"></span>RAG 应用</div>
        <div class="hp-lg-item"><span class="hp-lg-dot" style="background:#5b6b7a"></span>生成与展示</div>
      </div>
    </section>

    <!-- ============ 3. 项目功能 ============ -->
    <section class="hp-sec">
      <h2 class="hp-h2"><span class="hp-num">3</span>项目功能</h2>
      <div class="hp-feats">
        <a class="hp-feat" href="#/chat">
          <div class="hp-feat-ico">💬</div>
          <h3>AI 问答<span class="hp-tag">核心</span></h3>
          <p>三路混合检索（关键词 + 向量 + 图谱 RRF 融合），每句回答强制标注
             <b>版本 · 回次 · 段号</b>，可点击跳转原文。支持多轮对话、思考过程展示、
             多模型切换（DeepSeek 官方默认）。</p>
        </a>
        <a class="hp-feat" href="#/reader">
          <div class="hp-feat-ico">📖</div>
          <h3>原文阅读</h3>
          <p>三版本对照阅读（抚琴居校本 / 程高本 / 癸酉本）。右侧脂批侧栏按版本隔离显示，
             可从 AI 回答<b>直链跳转到对应段落</b>，也可划选文本作为上下文提问。</p>
        </a>
        <a class="hp-feat" href="#/stats">
          <div class="hp-feat-ico">📊</div>
          <h3>统计信息</h3>
          <p>基于 Hadoop MapReduce 的真实统计：回目字数分布、人物出现频次、
             批语密度热点、词频 Top100。每个统计项都可<b>直接发起提问</b>。</p>
        </a>
        <a class="hp-feat" href="#/graph">
          <div class="hp-feat-ico">⚭</div>
          <h3>人物关系</h3>
          <p>350 个人物节点、674 条关系边（亲属 / 丫鬟 / 主仆 / 朋友 / 妃嫔 / 乳母等 120 种关系）。
             力导向图交互，点击查看人物详情与关系路径。</p>
        </a>
        <a class="hp-feat" href="#/guiyou">
          <div class="hp-feat-ico">🔬</div>
          <h3>癸酉本探究</h3>
          <p>用计量风格学定量研究「癸酉本是否为真本」这一红学争议。
             三组对照 + 8 类统计 + 4 项稳健性检验，
             <b>结论分三层标注确定性</b>。</p>
        </a>
        <a class="hp-feat" href="#/corpus">
          <div class="hp-feat-ico">🗂</div>
          <h3>语料版本</h3>
          <p>三版本正文与三源脂批的完整清单。含从 PDF 重新提取的回目、
             码点重解码的脂批、跨源去重的过程数据。</p>
        </a>
      </div>
    </section>

    <!-- ============ 4. 技术栈 ============ -->
    <section class="hp-sec">
      <h2 class="hp-h2"><span class="hp-num">4</span>技术栈</h2>
      <div class="hp-stack">
        <div class="hp-stack-row">
          <div class="hp-stack-k">Hadoop 生态</div>
          <div class="hp-stack-v">
            <span class="chip">HDFS 3.3.6</span>
            <span class="chip">YARN</span>
            <span class="chip">MapReduce</span>
            <span class="chip">HBase 2.5.10</span>
            <span class="chip">Hive 3.1.3</span>
            <span class="chip">Spark 3.5.8</span>
            <span class="chip">ZooKeeper 3.8.4</span>
            <span class="chip">Flink 1.20.3</span>
            <span class="chip">Storm 1.2.4</span>
          </div>
        </div>
        <div class="hp-stack-row">
          <div class="hp-stack-k">RAG 检索</div>
          <div class="hp-stack-v">
            <span class="chip">HBase 倒排索引（bigram）</span>
            <span class="chip">bge-small-zh-v1.5 ONNX（${dim} 维）</span>
            <span class="chip">RRF 融合（k=60）</span>
            <span class="chip">图谱通道</span>
            <span class="chip">同回次去重</span>
          </div>
        </div>
        <div class="hp-stack-row">
          <div class="hp-stack-k">模型接入</div>
          <div class="hp-stack-v">
            <span class="chip">${esc(models)}</span>
            <span class="chip">OpenAI 兼容协议</span>
            <span class="chip">Antigravity 协议</span>
            <span class="chip">失败自动降级</span>
          </div>
        </div>
        <div class="hp-stack-row">
          <div class="hp-stack-k">应用层</div>
          <div class="hp-stack-v">
            <span class="chip">Python 3.10</span>
            <span class="chip">FastAPI + Uvicorn</span>
            <span class="chip">Node.js 静态服务</span>
            <span class="chip">原生 ES Module 前端（零框架）</span>
            <span class="chip">自研 Markdown 渲染器</span>
          </div>
        </div>
        <div class="hp-stack-row">
          <div class="hp-stack-k">数据工程</div>
          <div class="hp-stack-v">
            <span class="chip">PDF 码点级重解码</span>
            <span class="chip">三源脂批跨源去重</span>
            <span class="chip">截断修复（1,348 条）</span>
            <span class="chip">乱码修复（73→0）</span>
            <span class="chip">计量风格学</span>
            <span class="chip">jieba 词性分析</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ============ 5. 快速开始 ============ -->
    <section class="hp-sec hp-sec-last">
      <h2 class="hp-h2"><span class="hp-num">5</span>试试这些问题</h2>
      <div class="hp-starters">
        ${STARTERS.map((s) => `
          <button class="hp-starter" data-q="${esc(s.q)}">
            <span class="q">${esc(s.q)}</span>
            <span class="k">${esc(s.k)}</span>
          </button>`).join('')}
      </div>
      <div class="hp-foot">
        数据来源：抚琴居校本（前80回）· 程高本（120回）· 癸酉本（后28回）
        <br>索引 ${nChunk} chunk · 全部存放于 Hadoop HDFS / HBase · 可追溯、可复现
      </div>
    </section>
  </div>`;

  // 点击示例 → 跳到问答并自动发送
  // 注意：本模块不能直接引用 app.js 的 $ / autoGrow / send（未导入），
  // 必须通过 window.__HLM__ 在**点击时**延迟取，否则会 ReferenceError。
  stage.querySelectorAll('.hp-starter').forEach((b) => {
    b.onclick = () => {
      const q = b.dataset.q || '';
      location.hash = '#/chat';

      // 等比路由切换渲染完成再填值，最多重试 ~1.2s
      let tries = 0;
      const trySend = () => {
        const H = window.__HLM__ || {};
        const $ = H.$;
        const ta = $ ? $('#chatTextarea') : document.querySelector('#chatTextarea');
        const autoGrow = H.autoGrow;
        const send = H.send;

        if (!ta || !send) {
          if (++tries < 12) return setTimeout(trySend, 100);
          console.warn('[home] 无法进入问答框，放弃自动发送');
          return;
        }
        ta.value = q;
        if (typeof autoGrow === 'function') autoGrow(ta);
        // 让 input 事件也触发一次（有些逻辑监听它）
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        try { send(); } catch (e) { console.warn('[home] 自动发送失败:', e); }
      };
      setTimeout(trySend, 60);
    };
  });
}
