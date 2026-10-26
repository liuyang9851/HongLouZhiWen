# -*- coding: utf-8 -*-
"""
S7-3 · 生成三版本的前端数据
============================

解决的问题
----------
  问题 2：原文阅读只能选抚琴居本
  问题 3：第 64/78 回回目乱码（构建时已修，这里确保写进前端数据）

产出（写进 web/data/）
----
  chapters_{ver}.json           某版本的回目清单（含每回字数/段数）
  {ver}/chapter_XXX.json        某版本某回的正文段落

目录结构
--------
  web/data/
    chapters.json               抚琴居（兼容旧路径）
    chapter_000..080.json       抚琴居（兼容旧路径）
    versions/
      chapters_fuqinju.json
      chapters_chenggao.json
      chapters_guiyouben.json
      fuqinju/chapter_001.json …
      chenggao/chapter_001.json …
      guiyouben/chapter_081.json …

运行（Windows 本地）：
    python s7_3_build_webdata.py
"""
import os
import sys
import json
import shutil

sys.stdout.reconfigure(encoding="utf-8")

PIPE_OUT = os.path.join(r"D:\学校事务\大数据原理与应用", "红楼智问", "pipeline", "out")
WEB_DATA = os.path.join(r"D:\学校事务\大数据原理与应用", "红楼智问", "web", "data")
VDIR = os.path.join(WEB_DATA, "versions")

VERSIONS = [
    ("fuqinju", "抚琴居校本", 80),
    ("chenggao", "程高本", 120),
    ("guiyouben", "癸酉本", 108),
]


def main():
    os.makedirs(VDIR, exist_ok=True)

    # 脂批（只有抚琴居本有，按回分组一次即可）
    pizhu_by_ver = {}       # {ver: {hui: [...]}}
    pizhu_by_hui = {}
    pizhu_note = ""
    pz_path = os.path.join(WEB_DATA, "pizhu.json")
    if os.path.exists(pz_path):
        with open(pz_path, encoding="utf-8") as f:
            pz = json.load(f)
        pizhu_by_ver = {k: v.get("by_hui", {})
                        for k, v in (pz.get("by_version") or {}).items()}
        pizhu_by_hui = pz.get("by_hui", {})
        pizhu_note = pz.get("note", "")
    if not pizhu_by_ver:
        # 兼容旧格式：只有全局 by_hui
        pizhu_by_ver = {"fuqinju": pizhu_by_hui}
    for _v, _d in pizhu_by_ver.items():
        print(f"  脂批[{_v}]: {sum(len(x) for x in _d.values()):,} 条 / {len(_d)} 回")

    summary = {}
    for ver, label, max_hui in VERSIONS:
        src = os.path.join(PIPE_OUT, f"chapters_{ver}.jsonl")
        if not os.path.exists(src):
            print(f"⚠ 缺 {src}，跳过 {ver}")
            continue

        chapters = []
        with open(src, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    chapters.append(json.loads(line))
        chapters.sort(key=lambda c: c["hui"])

        odir = os.path.join(VDIR, ver)
        os.makedirs(odir, exist_ok=True)

        toc = []
        n_fixed = 0
        for c in chapters:
            hui = c["hui"]
            # 段落：id = p{hui}_{idx}，idx 从 0 起（与前端的 data-para-id 一致）
            paras = []
            for i, t in enumerate(c["paragraphs"]):
                t2 = str(t).replace("\ufffd", "□")
                if t2 != t:
                    n_fixed += 1
                paras.append({"i": i, "id": f"p{hui}_{i}", "t": t2})

            huimu_txt = (c.get("huimu") or "").strip()
            if not huimu_txt:
                huimu_txt = "凡例" if hui == 0 else f"第{hui}回"
            obj = {
                "version": ver,
                "version_label": label,
                "hui": hui,
                "huimu": huimu_txt,
                "char_count": c["char_count"],
                "paragraphs": paras,
            }
            # 每个版本都写入自己的批语（癸酉本 223 条 / 程高本空）
            _pz_for_ver = pizhu_by_ver.get(ver, {})
            obj["pizhu"] = _pz_for_ver.get(str(hui), [])
            obj["pizhu_note"] = pizhu_note
            obj["pizhu_source"] = ("癸酉本" if ver == "guiyouben"
                                   else ("程高本（删改本，无脂批）" if ver == "chenggao"
                                         else "抚琴居校本"))

            with open(os.path.join(odir, f"chapter_{hui:03d}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)

            # 注意：s7_1 已把癸酉本的 hui 映射成 81..108，
            # 这里**不能**再加 80（否则变成 161..188，实测踩过重复叠加）。
            toc.append({
                "hui": hui,
                "huimu": huimu_txt,
                "chars": c["char_count"],
                "paras": len(paras),
            })

        toc_obj = {"version": ver, "version_label": label,
                   "max_hui": max_hui, "chapters": toc}
        with open(os.path.join(VDIR, f"chapters_{ver}.json"), "w",
                  encoding="utf-8") as f:
            json.dump(toc_obj, f, ensure_ascii=False)

        # 抚琴居同时也覆盖旧路径（保持向后兼容）
        if ver == "fuqinju":
            with open(os.path.join(WEB_DATA, "chapters.json"), "w",
                      encoding="utf-8") as f:
                json.dump(toc, f, ensure_ascii=False)
            for hui in (0, 78):
                pass
            for c in chapters:
                h = c["hui"]
                srcf = os.path.join(odir, f"chapter_{h:03d}.json")
                dstf = os.path.join(WEB_DATA, f"chapter_{h:03d}.json")
                if os.path.exists(srcf):
                    shutil.copyfile(srcf, dstf)

        bad = [c["hui"] for c in chapters if "\ufffd" in c["huimu"]]
        summary[ver] = {
            "label": label, "n_hui": len(toc),
            "n_para": sum(t["paras"] for t in toc),
            "n_chars": sum(t["chars"] for t in toc),
            "huimu_bad": bad, "para_fixed": n_fixed,
        }
        print(f"{ver:10s} {label:8s} 回数={len(toc):3d} "
              f"段数={summary[ver]['n_para']:5d} "
              f"字符={summary[ver]['n_chars']:>8,} "
              f"回目乱码={'无 ✅' if not bad else bad}")

    # 重点验证
    print("\n" + "=" * 70)
    print("重点验证：第 64 / 78 回回目（原乱码处）")
    print("=" * 70)
    for ver, label, _ in VERSIONS:
        p = os.path.join(VDIR, f"chapters_{ver}.json")
        if not os.path.exists(p):
            continue
        toc = json.load(open(p, encoding="utf-8"))["chapters"]
        for h in (64, 78):
            rec = [x for x in toc if x["hui"] == h]
            if rec:
                t = rec[0]["huimu"]
                print(f"  {ver:10s} 第{h}回 {'✅' if chr(0xfffd) not in t else '❌'} {t}")

    with open(os.path.join(VDIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": "2026-10-24", "versions": summary},
                  f, ensure_ascii=False, indent=2)
    print(f"\n产出目录: {VDIR}")


if __name__ == "__main__":
    main()
