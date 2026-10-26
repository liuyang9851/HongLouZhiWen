# -*- coding: utf-8 -*-
"""
S22-6b · 重新抓取并正确清洗对照语料
====================================

上一轮（s22_6）的严重错误
------------------------
我用「找第一个『第X回』」来定位原文起点。但实测发现：

  · 该库的 HTML **只有正文，没有章节标题**
  · 章节顺序由**文件名**决定（1.html = 第1回，2.html = 第2回…）

结果「第X回」定位全部失败：
  · ershinian 被砍掉 308,165 字（只剩 7.6%！）
  · jinpingmei / rulinwaishi 定位到正文中间的偶然字串

这是方法错误，结论作废。

正确做法
--------
  1. 按文件名数字排序（1,2,3...100）
  2. 跳过 0.html（目录/封面页）
  3. 清洗每个 HTML：去标签、去网站导航噪音
  4. 拼接时保留分隔

另外，之前的「现代词密度」指标也不成立：
  红楼三组 56-60/万字 vs 对照 7-16/万字
  原因是「我们/这个」在《红楼梦》里本来就很常见（口语化）
  这个指标**不能用来判断现代性**，弃用。

运行：
  python s22_6b_refetch.py
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

TARGETS = [
    ("世态人情", "金瓶梅", "jinpingmei"),
    ("谴责公案", "儒林外史", "rulinwaishi"),
    ("世态人情", "醒世姻缘传", "xingshiyinyuan"),
    ("谴责公案", "二十年目睹之怪现状", "ershinian"),
]


def log(m):
    print(m, flush=True)


def pct_encode(url):
    p = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit((
        p.scheme, p.netloc,
        urllib.parse.quote(p.path, safe="/%"),
        urllib.parse.quote(p.query, safe="=&%"),
        p.fragment))


def http_get(url, timeout=40, retry=3):
    url = pct_encode(url)
    for i in range(retry):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hlm-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:
            if i == retry - 1:
                log(f"      失败: {e}")
                return None
            time.sleep(1.5 * (i + 1))
    return None


class Ext(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("br", "p", "div"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip > 0:
            self.skip -= 1
        elif tag in ("p", "div"):
            self.parts.append("\n")

    def handle_data(self, d):
        if not self.skip:
            self.parts.append(d)


NAV_WORDS = ("上一页", "下一页", "目录", "首页", "返回", "版权",
             "小说网", "最新章节", "加入书签", "投推荐票", "手机阅读")


def clean_html(raw):
    e = Ext()
    try:
        e.feed(raw)
    except Exception:
        pass
    t = "".join(e.parts)
    t = t.replace("\u3000", "")
    lines = []
    for ln in t.split("\n"):
        s = ln.strip()
        if not s or len(s) < 4:
            continue
        if any(w in s for w in NAV_WORDS):
            continue
        if s.startswith(("http", "www.", "//")):
            continue
        lines.append(s)
    return "\n".join(lines)


def main():
    log("=" * 74)
    log("S22-6b 重新抓取并正确清洗对照语料")
    log("=" * 74)
    log("关键修正：章节顺序由文件名决定，HTML 内无章节标题")

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = []

    for cat, name, slug in TARGETS:
        log(f"\n--- {name} ---")
        listing_url = f"{API}/{urllib.parse.quote(cat)}/{urllib.parse.quote(name)}"
        raw = http_get(listing_url, timeout=30)
        if not raw:
            log("  列目录失败")
            continue
        try:
            items = json.loads(raw)
        except Exception:
            log("  JSON 解析失败")
            continue

        # 按文件名数字排序，跳过 0.html
        files = []
        for it in items:
            if it.get("type") != "file":
                continue
            fn = it.get("name", "")
            m = re.match(r"^(\d+)\.html$", fn)
            if not m:
                continue
            idx = int(m.group(1))
            if idx == 0:
                continue          # 目录/封面
            if it.get("size", 0) < 300:
                continue
            files.append((idx, it["download_url"]))
        files.sort()
        log(f"  {len(files)} 个正文文件（已跳过 0.html）")

        chapters = []
        for n, (idx, dl) in enumerate(files, 1):
            h = http_get(dl)
            if not h:
                continue
            txt = clean_html(h)
            if len(txt) < 200:
                continue
            chapters.append((idx, txt))
            if n % 25 == 0:
                log(f"    已处理 {n}/{len(files)}")

        if not chapters:
            log("  ⚠ 无内容")
            continue

        # 拼接（章间用空行）
        full = "\n\n".join(t for _, t in chapters)
        out = os.path.join(OUT_DIR, f"{slug}.txt")
        with open(out, "w", encoding="utf-8") as f:
            f.write(full)

        chars = len(re.findall(r"[\u4e00-\u9fff]", full))
        log(f"  ✅ {out}")
        log(f"     {len(chapters)} 章 / {len(full):,} 字符 / {chars:,} 汉字")
        log(f"     章号范围: {chapters[0][0]} ~ {chapters[-1][0]}")
        log(f"     首章开头: {chapters[0][1][:60]!r}")

        manifest.append({
            "name": name, "slug": slug, "path": out,
            "n_chapters": len(chapters),
            "chars": len(full), "han_chars": chars,
            "chap_range": [chapters[0][0], chapters[-1][0]],
            "source": "https://github.com/luoxuhai/chinese-novel",
        })

    mp = os.path.join(OUT_DIR, "_manifest.json")
    with open(mp, "w", encoding="utf-8") as f:
        json.dump({"note": "公版古籍，仅用于学术统计对比，不进入产品语料库",
                   "items": manifest}, f, ensure_ascii=False, indent=1)
    log(f"\n清单: {mp}")

    log("\n=== 汇总 ===")
    log(f"  {'书名':<16}{'章数':>6}{'汉字数':>12}")
    for m in manifest:
        log(f"  {m['name']:<16}{m['n_chapters']:>6}{m['han_chars']:>12,}")


if __name__ == "__main__":
    main()
