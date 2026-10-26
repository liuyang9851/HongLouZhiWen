# -*- coding: utf-8 -*-
"""
S22-4a · 抓取对照语料（其他明清小说）
=====================================

目的
----
S22 主研究的局限之一是「没有对照组」——
无法判断癸酉本的异常（长句、"被"字多、四字格少）
是「曹雪芹独有特征」还是「明清通俗小说的通病」。

本脚本从 GitHub 开源库 luoxuhai/chinese-novel 抓取对照语料：
  · 金瓶梅      （明代，世情小说，与红楼题材最接近）
  · 儒林外史    （清代，讽刺小说，同为白话）
  · 醒世姻缘传  （清代，世情小说）
  · 镜花缘      （清代）
  · 二十年目睹之怪现状（晚清，用来定位"晚清白话"特征）

⚠️ 数据来源与许可
----------------
  仓库：https://github.com/luoxuhai/chinese-novel
  这些是公版古籍（作者逝世逾百年），仅用于学术统计对比。
  抓取的内容只做字数/词频等聚合统计，不进入产品语料库。

运行：
  python s22_4a_fetch_comparison.py
"""
import os
import re
import sys
import json
import time
import urllib.request
import urllib.parse
from html.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")

OUT_DIR = r"D:\Programming\Red\comparison_corpus"
API = "https://api.github.com/repos/luoxuhai/chinese-novel/contents/resources"

# (分类, 书名, 简称)
TARGETS = [
    ("世态人情", "金瓶梅", "jinpingmei"),
    ("谴责公案", "儒林外史", "rulinwaishi"),
    ("世态人情", "醒世姻缘传", "xingshiyinyuan"),
    ("世态人情", "镜花缘", "jinghuayuan"),
    ("谴责公案", "二十年目睹之怪现状", "ershinian"),
]


def log(m):
    print(m, flush=True)


def api_get(url, retry=3):
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "hlm-research/1.0",
                "Accept": "application/vnd.github+json",
            })
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if i == retry - 1:
                raise
            time.sleep(2 * (i + 1))
    return None


def pct_encode_url(url):
    """
    把 URL 里的非 ASCII 字符（中文路径）做百分号编码。
    urllib 遇到中文会报 'ascii' codec can't encode。
    """
    import urllib.parse as _up
    parts = _up.urlsplit(url)
    path = _up.quote(parts.path, safe="/%")
    query = _up.quote(parts.query, safe="=&%")
    return _up.urlunsplit((parts.scheme, parts.netloc, path, query, parts.fragment))


def fetch_raw(url, retry=3):
    url = pct_encode_url(url)
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hlm-research/1.0"})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i == retry - 1:
                log(f"      下载失败: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


class TextExtractor(HTMLParser):
    """从 HTML 提取纯文本，丢弃 script/style"""

    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "div", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip > 0:
            self.skip -= 1
        elif tag in ("p", "div", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        t = "".join(self.parts)
        # 压缩空白
        t = re.sub(r"[ \t\u3000]+", "", t)
        t = re.sub(r"\n{2,}", "\n", t)
        return t.strip()


def clean_text(html):
    ex = TextExtractor()
    try:
        ex.feed(html)
    except Exception:
        pass
    t = ex.text()
    # 去掉常见的网站导航噪音行
    lines = []
    for ln in t.split("\n"):
        ln = ln.strip()
        if not ln:
            continue
        # 过滤明显的导航/版权行
        if any(k in ln for k in ("上一页", "下一页", "目录", "首页", "返回",
                                 "版权", "www.", "http", "小说网", "最新章节")):
            continue
        if len(ln) < 4:
            continue
        lines.append(ln)
    return "\n".join(lines)


def main():
    log("=" * 74)
    log("S22-4a 抓取对照语料")
    log("=" * 74)
    log("来源: https://github.com/luoxuhai/chinese-novel （公版古籍）")

    os.makedirs(OUT_DIR, exist_ok=True)

    manifest = []
    for cat, name, slug in TARGETS:
        log(f"\n--- {name} ({cat}) ---")
        out_path = os.path.join(OUT_DIR, f"{slug}.txt")
        if os.path.exists(out_path) and os.path.getsize(out_path) > 50000:
            sz = os.path.getsize(out_path)
            log(f"  已存在，跳过（{sz:,} B）")
            manifest.append({"name": name, "slug": slug, "path": out_path,
                             "bytes": sz, "cached": True})
            continue

        # 列目录
        try:
            url = f"{API}/{urllib.parse.quote(cat)}/{urllib.parse.quote(name)}"
            listing = api_get(url)
        except Exception as e:
            log(f"  列目录失败: {e}")
            continue

        if not isinstance(listing, list):
            log(f"  目录异常: {type(listing)}")
            continue

        htmls = [x for x in listing
                 if x.get("type") == "file" and x.get("name", "").endswith(".html")]
        # 按章节号排序（0,1,2,...,10,11）
        def chapno(x):
            m = re.match(r"^(\d+)", x["name"])
            return int(m.group(1)) if m else 99999
        htmls.sort(key=chapno)
        log(f"  {len(htmls)} 个 HTML 文件")

        chunks = []
        total_bytes = 0
        for i, h in enumerate(htmls):
            if h.get("size", 0) < 200:
                continue      # 跳过封面/目录页
            raw = fetch_raw(h["download_url"])
            if not raw:
                continue
            txt = clean_text(raw)
            if len(txt) < 100:
                continue
            chunks.append(txt)
            total_bytes += len(raw)
            if (i + 1) % 20 == 0:
                log(f"    已下载 {i+1}/{len(htmls)}，累计 {total_bytes:,} B")

        full = "\n".join(chunks)
        if len(full) < 10000:
            log(f"  ⚠ 内容过少（{len(full)} 字），跳过")
            continue

        with open(out_path, "w", encoding="utf-8") as f:
            f.write(full)
        log(f"  ✅ 写出 {out_path}  ({len(full):,} 字)")
        manifest.append({"name": name, "slug": slug, "path": out_path,
                         "chars": len(full), "n_files": len(chunks),
                         "source_url": f"https://github.com/luoxuhai/chinese-novel/tree/master/resources/{urllib.parse.quote(cat)}/{urllib.parse.quote(name)}"})

    # 写清单
    mp = os.path.join(OUT_DIR, "_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"source": "https://github.com/luoxuhai/chinese-novel",
                   "license_note": "公版古籍，仅用于学术统计对比，不进入产品语料库",
                   "items": manifest}, f, ensure_ascii=False, indent=1)
    log(f"\n清单: {mp}")
    log(f"\n=== 汇总 ===")
    for m in manifest:
        log(f"  {m['name']:16s} {m.get('chars', m.get('bytes', 0)):>10,}")


if __name__ == "__main__":
    main()
