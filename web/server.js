/**
 * 红楼智问 —— Node.js 前端服务（阶段 S1：界面设计预览版）
 *
 * 职责分三块，全部零第三方依赖（Node 内置模块足够）：
 *   1. 静态资源服务：public/ 下的 HTML / CSS / JS / 字体
 *   2. 数据接口：把 web/data/ 下的真实语料数据暴露成 REST API
 *   3. 后端反向代理：/api/* 转发到 Python 后端（默认 127.0.0.1:8000）
 *
 * 设计要求（对应需求文档）：
 *   - R-401 ~ R-406 六个界面共用这一套壳
 *   - R-402 原文阅读：按回懒加载 chapter_XXX.json，段落级 id 保证可高亮定位
 *   - R-403 统计：stats.json 一次性加载（< 200 KB）
 *   - R-404 关系图：graph.json（120 节点 / 253 边）
 *   - N-111 安全：仅监听 127.0.0.1，不对外暴露
 */
import http from 'node:http';
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const PUBLIC_DIR = path.join(__dirname, 'public');
const DATA_DIR = path.join(__dirname, 'data');

const HOST = process.env.HLM_WEB_HOST || '127.0.0.1';
const PORT = Number(process.env.HLM_WEB_PORT || 3000);
const BACKEND = process.env.HLM_BACKEND || 'http://127.0.0.1:8000';

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.woff2': 'font/woff2',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};

/* ------------------------------------------------------------------ */
/* 数据缓存：stats / chapters / graph 体积小、变更少，进程内缓存        */
/* ------------------------------------------------------------------ */
const cache = new Map();

async function readData(name) {
  if (cache.has(name)) return cache.get(name);
  const p = path.join(DATA_DIR, name);
  const buf = await fsp.readFile(p);
  const json = JSON.parse(buf.toString('utf8'));
  cache.set(name, json);
  return json;
}

function sendJson(res, obj, status = 200) {
  const body = JSON.stringify(obj);
  res.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Content-Length': Buffer.byteLength(body),
    'Cache-Control': 'no-store',
  });
  res.end(body);
}

function sendText(res, text, status = 200, type = 'text/plain; charset=utf-8') {
  res.writeHead(status, { 'Content-Type': type });
  res.end(text);
}

/* ------------------------------------------------------------------ */
/* 别名归一化                                                          */
/* 实测依据：80 回正文中「贾宝玉」全名仅 2 次，「宝玉」2,626 次。        */
/* 不做别名展开，按人物名检索会得出「贾宝玉只出现 2 次」的荒谬结论。      */
/* ------------------------------------------------------------------ */
const ALIAS = {
  贾宝玉: ['贾宝玉', '宝玉', '宝二爷', '怡红公子', '绛洞花王', '富贵闲人', '混世魔王'],
  林黛玉: ['林黛玉', '黛玉', '颦儿', '颦卿', '林妹妹', '潇湘妃子'],
  薛宝钗: ['薛宝钗', '宝钗', '宝姐姐', '蘅芜君'],
  王熙凤: ['王熙凤', '凤姐儿', '凤姐', '凤辣子', '琏二奶奶', '凤丫头'],
  史湘云: ['史湘云', '湘云', '云妹妹', '枕霞旧友'],
  贾母: ['贾母', '老太太', '史太君', '老祖宗'],
  贾探春: ['贾探春', '探春', '三姑娘', '蕉下客'],
  贾迎春: ['贾迎春', '迎春', '二姑娘', '菱洲'],
  贾惜春: ['贾惜春', '惜春', '四姑娘', '藕榭'],
  李纨: ['李纨', '大奶奶', '稻香老农', '宫裁'],
  袭人: ['袭人', '花袭人', '珍珠'],
  晴雯: ['晴雯'],
  平儿: ['平儿', '平姑娘'],
  香菱: ['香菱', '甄英莲', '英莲', '秋菱'],
  贾琏: ['贾琏', '琏二爷'],
  秦可卿: ['秦可卿', '可卿', '秦氏'],
  贾雨村: ['贾雨村', '雨村'],
  甄士隐: ['甄士隐', '士隐'],
  薛蟠: ['薛蟠', '呆霸王', '薛大傻子'],
  刘姥姥: ['刘姥姥', '刘老老'],
  蒋玉菡: ['蒋玉菡', '琪官'],
  焦大: ['焦大'],
};

/** 把查询词展开成「等价写法」列表；不是已知人物就原样返回 */
function expandAliases(q) {
  for (const [canon, names] of Object.entries(ALIAS)) {
    if (names.includes(q) || canon === q) {
      // 长名优先，避免「宝玉」把「贾宝玉」吃掉后重复统计
      return [...names].sort((a, b) => b.length - a.length);
    }
  }
  return [q];
}

/* ------------------------------------------------------------------ */
/* 静态文件                                                            */
/* ------------------------------------------------------------------ */
/**
 * 读取 web/data/ 下的静态 JSON（只允许 .json，防目录逃逸）
 */
async function serveData(res, urlPath) {
  const rel = decodeURIComponent(urlPath.replace(/^\/data\//, '')).replace(/^\.\.?\//g, '');
  if (!/\.json$/.test(rel)) return sendText(res, '403 Forbidden', 403);
  const target = path.resolve(DATA_DIR, rel);
  if (!target.startsWith(DATA_DIR)) return sendText(res, '403 Forbidden', 403);
  try {
    const buf = await fsp.readFile(target);
    res.writeHead(200, {
      'Content-Type': 'application/json; charset=utf-8',
      'Content-Length': buf.length,
      'Cache-Control': 'no-store, must-revalidate',
    });
    res.end(buf);
  } catch {
    return sendText(res, '404 Not Found', 404);
  }
}

async function serveStatic(req, res, urlPath) {
  // 归一化，阻止 ../ 逃逸
  const rel = decodeURIComponent(urlPath).replace(/^\/+/, '');
  const target = path.resolve(PUBLIC_DIR, rel);
  if (!target.startsWith(PUBLIC_DIR)) return sendText(res, '403 Forbidden', 403);

  let filePath = target;
  let stat;
  try {
    stat = await fsp.stat(filePath);
    if (stat.isDirectory()) {
      filePath = path.join(filePath, 'index.html');
      stat = await fsp.stat(filePath);
    }
  } catch {
    return sendText(res, '404 Not Found', 404);
  }

  const ext = path.extname(filePath).toLowerCase();
  const etag = `W/"${stat.size}-${stat.mtimeMs}"`;
  if (req.headers['if-none-match'] === etag) {
    res.writeHead(304);
    return res.end();
  }
  // 开发/演示期禁用静态资源强缓存，避免改了代码浏览器还跑旧版
  const noCache = process.env.HLM_NO_CACHE !== '0';
  res.writeHead(200, {
    'Content-Type': MIME[ext] || 'application/octet-stream',
    'Content-Length': stat.size,
    'Cache-Control': noCache ? 'no-store, must-revalidate' : (ext === '.html' ? 'no-cache' : 'public, max-age=300'),
    ETag: etag,
  });
  fs.createReadStream(filePath).pipe(res);
}

/* ------------------------------------------------------------------ */
/* API 路由                                                            */
/* ------------------------------------------------------------------ */
async function handleApi(req, res, url) {
  const { pathname, searchParams } = url;

  // --- 元信息 ---
  if (pathname === '/api/health') {
    return sendJson(res, {
      ok: true,
      stage: 'S1-界面设计',
      time: new Date().toISOString(),
      backend: BACKEND,
    });
  }

  if (pathname === '/api/manifest') {
    return sendJson(res, await readData('manifest.json'));
  }

  // --- 章节（支持三版本，问题 2 修复）---
  // ver=fuqinju | chenggao | guiyouben，默认 fuqinju（向后兼容）
  const VER_MAX = { fuqinju: 80, chenggao: 120, guiyouben: 108 };
  const VER_MIN = { fuqinju: 0, chenggao: 1, guiyouben: 81 };

  if (pathname === '/api/chapters') {
    const ver = searchParams.get('ver') || 'fuqinju';
    if (!VER_MAX[ver]) return sendJson(res, { error: 'unknown ver' }, 400);
    try {
      const toc = await readData(`versions/chapters_${ver}.json`);
      return sendJson(res, toc);
    } catch {
      // 兜底：老路径
      const legacy = await readData('chapters.json');
      return sendJson(res, { version: 'fuqinju', max_hui: 80, chapters: legacy });
    }
  }

  if (pathname === '/api/chapter') {
    const ver = searchParams.get('ver') || 'fuqinju';
    const maxHui = VER_MAX[ver];
    const minHui = VER_MIN[ver] ?? 0;
    if (!maxHui) return sendJson(res, { error: 'unknown ver' }, 400);

    const hui = Number(searchParams.get('hui') ?? (ver === 'guiyouben' ? 81 : 1));
    if (!Number.isInteger(hui) || hui < minHui || hui > maxHui) {
      return sendJson(res, {
        error: `hui must be ${minHui}..${maxHui} for ${ver}`,
      }, 400);
    }
    const name = `versions/${ver}/chapter_${String(hui).padStart(3, '0')}.json`;
    try {
      const chapter = await readData(name);
      return sendJson(res, chapter);
    } catch {
      // 兼容老路径（只有抚琴居有）
      if (ver === 'fuqinju') {
        try {
          const chapter = await readData(`chapter_${String(hui).padStart(3, '0')}.json`);
          const pizhu = await readData('pizhu.json');
          chapter.pizhu = pizhu.by_hui[String(hui)] || [];
          chapter.pizhu_note = pizhu.note;
          return sendJson(res, chapter);
        } catch { /* 落到 404 */ }
      }
      return sendJson(res, { error: 'chapter not found', ver, hui }, 404);
    }
  }

  // --- 统计 ---
  if (pathname === '/api/stats') {
    return sendJson(res, await readData('stats.json'));
  }

  // --- 关系图 ---
  if (pathname === '/api/graph') {
    const g = await readData('graph.json');
    // 可选：按家族过滤
    const fam = searchParams.get('family');
    if (fam && fam !== 'all') {
      const keep = new Set(g.nodes.filter((n) => n.family === fam).map((n) => n.id));
      // 保留一跳邻居，避免图断裂
      for (const e of g.edges) {
        if (keep.has(e.source)) keep.add(e.target);
        if (keep.has(e.target)) keep.add(e.source);
      }
      return sendJson(res, {
        ...g,
        nodes: g.nodes.filter((n) => keep.has(n.id)),
        edges: g.edges.filter((e) => keep.has(e.source) && keep.has(e.target)),
        filtered_by: fam,
      });
    }
    return sendJson(res, g);
  }

  // --- 版本覆盖 ---
  if (pathname === '/api/version-matrix') {
    return sendJson(res, await readData('version_matrix.json'));
  }

  // --- 原文检索（供划选提问 / 引用定位验证）---
  // 关键：必须做「别名归一化」。实测「贾宝玉」全名在 80 回正文里只出现 2 次，
  // 而「宝玉」出现 2,600+ 次。不做别名展开，按人物名检索几乎必然 0 命中。
  if (pathname === '/api/locate') {
    const q = (searchParams.get('q') || '').trim();
    if (!q) return sendJson(res, { hits: [] });

    const variants = expandAliases(q);
    const toc = await readData('chapters.json');
    const seen = new Set();
    const hits = [];

    for (const ch of toc.chapters) {
      if (hits.length >= 20) break;
      if (ch.hui === 0) continue;
      const c = await readData(ch.file);
      for (const p of c.paragraphs) {
        if (hits.length >= 20) break;
        // 逐个变体找；取最早出现的那个变体作为高亮锚点
        let best = null;
        for (const v of variants) {
          const at = p.t.indexOf(v);
          if (at > -1 && (best === null || at < best.at)) best = { at, v };
        }
        if (!best) continue;
        const key = `${ch.hui}:${p.i}`;
        if (seen.has(key)) continue;
        seen.add(key);
        hits.push({
          hui: ch.hui,
          huimu: ch.huimu,
          para_id: p.id,
          para_index: p.i,
          matched: best.v,
          char_start: best.at,
          char_end: best.at + best.v.length,
          snippet: p.t.slice(Math.max(0, best.at - 40), best.at + best.v.length + 80),
        });
      }
    }
    return sendJson(res, { q, variants, hits });
  }

  // --- 后端代理（RAG / 图谱问答 / 统计问答，待 Python 服务实装）---
  if (pathname.startsWith('/api/backend/')) {
    const target = BACKEND + pathname.replace('/api/backend', '') + url.search;
    try {
      const upstream = await fetch(target, {
        method: req.method,
        headers: { 'Content-Type': req.headers['content-type'] || 'application/json' },
        body: ['GET', 'HEAD'].includes(req.method) ? undefined : req,
        duplex: 'half',
      });
      res.writeHead(upstream.status, {
        'Content-Type': upstream.headers.get('content-type') || 'application/json; charset=utf-8',
        'Cache-Control': 'no-store',
        'X-Accel-Buffering': 'no',
      });
      if (upstream.body) {
        for await (const chunk of upstream.body) res.write(chunk);
      }
      return res.end();
    } catch (err) {
      return sendJson(res, {
        error: 'backend_unavailable',
        message: `Python 后端（${BACKEND}）尚未启动：${err.message}`,
        hint: '阶段 S1 只做界面设计；RAG 后端在 S4 实装。',
      }, 503);
    }
  }

  return sendJson(res, { error: 'not_found', path: pathname }, 404);
}

/* ------------------------------------------------------------------ */
/* 服务器                                                              */
/* ------------------------------------------------------------------ */
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || HOST}`);
  try {
    if (url.pathname.startsWith('/api/')) return await handleApi(req, res, url);
    // S23：允许前端读取 data/ 下的静态 JSON（如 s22_guiyou.json）
    if (url.pathname.startsWith('/data/')) return await serveData(res, url.pathname);
    return await serveStatic(req, res, url.pathname);
  } catch (err) {
    console.error('[error]', err);
    if (!res.headersSent) sendJson(res, { error: 'internal', message: err.message }, 500);
    else res.end();
  }
});

server.listen(PORT, HOST, () => {
  console.log(`红楼智问 · 前端已启动  http://${HOST}:${PORT}`);
  console.log(`  数据目录  ${DATA_DIR}`);
  console.log(`  后端代理  ${BACKEND}`);
});
