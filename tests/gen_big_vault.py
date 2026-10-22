#!/usr/bin/env python3
"""gen_big_vault.py — 生成一个「已知标准答案」的模拟 Obsidian 库，用于引擎压测。

为什么需要它：
  在真实库上跑只能看「报了什么」，看不出「该报的有没有漏」。
  这里每个问题都是**故意注入**的，所以有 ground truth，
  可以量化两个硬指标：
    召回率 = 抓到的真问题 / 注入的真问题
    精确率 = 报出的问题里真是问题的比例（衡量噪音）

模拟的真实形态（对齐典型 PARA/项目流水库）：
  · 多级目录（3-4 层）、中文文件名、空格文件名
  · 20 个项目，每个一份同名「项目卡」（同名不同内容 = 正常分类）
  · 100 篇领域知识卡、60 篇对话日记（时间流）、3 个模板、15 个归档快照
  · 附件图片 / PDF / Canvas
  · 注入：真断链 15、模板占位 8、真孤儿、真重复 10 组、缺 frontmatter、
          归档缺元数据、系统垃圾文件

用法：
  python3 gen_big_vault.py <目标目录> [--gt <ground_truth.json>]
"""
import argparse
import datetime
import json
import os
import random
import shutil
import sys
from pathlib import Path

random.seed(42)  # 可复现：每次生成的库与 ground truth 一致

# ── 规模参数（默认 300+ 篇，超过真实"治理期"下限 100 篇 3 倍）──
N_PROJECTS = 20
NOTES_PER_PROJECT = 5
N_CARDS = 100
N_DIARY = 60
N_ARCHIVE = 15
N_IMAGES = 25
N_PDFS = 5
N_CANVAS = 3

# ── 注入的问题数量 ──
N_TRUE_BROKEN = 15
N_TEMPLATE_PLACEHOLDER = 8
N_TRUE_ORPHANS = 20
N_DUP_GROUPS = 10
N_MISSING_FM = 30
N_JUNK = 20

TOPICS = ["调研", "方案", "复盘", "选型", "排期", "验收", "对话", "指标", "风险", "知识"]
DOMAINS = ["产品设计", "数据分析", "工程实践", "团队协作", "内容运营"]
TAGS = ["项目", "知识", "日记", "复盘", "方法论"]


def w(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def fm(title, tags, extra=""):
    t = ", ".join(tags)
    body = f"---\ntitle: {title}\ntags: [{t}]\n"
    if extra:
        body += extra
    body += "---\n\n"
    return body


def main():
    ap = argparse.ArgumentParser(description="生成模拟 Obsidian 库（含 ground truth）")
    ap.add_argument("root", help="目标目录（会被清空重建）")
    ap.add_argument("--gt", default=None, help="ground truth 输出路径（默认 <root>/../gt.json）")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    gt = {
        "vault": str(root),
        "generated_at": datetime.datetime.now().isoformat(),
        "injected": {},
        "expected_orphans": [],
        "expected_duplicates": [],
        "expected_same_name_diff": [],
        "expected_broken": [],
        "expected_template_placeholders": [],
        "expected_missing_fm": [],
        "expected_junk": [],
    }

    note_count = 0

    # ── 0. 库规（MOC 入口，给项目卡与部分知识卡建立入链）──
    # 用带路径的 wikilink：项目卡文件名是「项目卡.md」，与目录名不同，
    # 写 [[项目01]] 是解析不到的（那会是真断链，不是我们想注入的问题）。
    links = []
    for i in range(1, N_PROJECTS + 1):
        links.append(f"- [[10-项目档案/项目{i:02d}/项目卡|项目{i:02d}]]")
    mocs = [f"[[知识卡{k:03d}]]" for k in range(1, 31)]
    w(root, "00-库规.md",
      fm("库规", ["库规"], "type: rule\n") +
      "# 库规\n\n## 项目索引\n" + "\n".join(links) +
      "\n\n## 领域知识索引\n" + "、".join(mocs) + "\n")
    note_count += 1

    # ── 1. 项目档案：每个项目一份同名「项目卡」（同名不同内容）──
    for i in range(1, N_PROJECTS + 1):
        pname = f"项目{i:02d}"
        # 项目卡：链向本项目下的笔记 → 项目笔记获得入链
        note_links = "\n".join(f"- [[{pname}-{TOPICS[j % len(TOPICS)]}{j}]]"
                               for j in range(1, NOTES_PER_PROJECT + 1))
        w(root, f"10-项目档案/{pname}/项目卡.md",
          fm(pname, ["项目"], "type: project\nstatus: 进行中\n") +
          f"# {pname}\n\n## 背景\n项目 {i} 的背景说明。\n\n## 相关笔记\n{note_links}\n")
        note_count += 1
        gt["expected_same_name_diff"].append(f"10-项目档案/{pname}/项目卡.md")

        for j in range(1, NOTES_PER_PROJECT + 1):
            topic = TOPICS[j % len(TOPICS)]
            w(root, f"10-项目档案/{pname}/{pname}-{topic}{j}.md",
              fm(f"{pname} {topic}{j}", ["项目", topic]) +
              f"# {pname} · {topic}{j}\n\n"
              f"所属项目：[[10-项目档案/{pname}/项目卡|{pname}]]\n\n"
              f"这是 {pname} 第 {j} 篇关于{topic}的记录，包含若干结论。\n")
            note_count += 1

    # ── 2. 领域知识卡：100 篇，只有前 30 篇被库规索引（其余 70 篇无入链）──
    for k in range(1, N_CARDS + 1):
        domain = DOMAINS[k % len(DOMAINS)]
        rel = f"20-领域知识/{domain}/知识卡{k:03d}.md"
        w(root, rel,
          fm(f"知识卡{k:03d}", ["知识", domain]) +
          f"# 知识卡 {k:03d}\n\n主题：{domain}\n\n"
          f"这是第 {k} 条领域知识的摘要内容，用于验证检索与链接结构。\n")
        note_count += 1

    # ── 3. 对话日记：60 篇时间流笔记（天然无入链）──
    diary_paths = []
    start = datetime.date(2026, 8, 1)
    for d in range(N_DIARY):
        day = start + datetime.timedelta(days=d)
        rel = f"30-对话日记/{day.isoformat()}.md"
        # 前 N_MISSING_FM 篇故意不写 frontmatter
        if d < N_MISSING_FM:
            body = f"# {day.isoformat()} 对话纪要\n\n今日要点若干。\n"
            gt["expected_missing_fm"].append(rel)
        else:
            body = fm(f"{day.isoformat()} 对话纪要", ["日记"], "type: daily\n") + \
                   f"# {day.isoformat()} 对话纪要\n\n今日要点若干。\n"
        w(root, rel, body)
        note_count += 1
        diary_paths.append(rel)

    # ── 4. 模板：3 个，各含占位符链接（必然断链，属正常）──
    tpl_specs = [
        ("99-模板/项目模板.md",
         "---\ntype: project\nstatus: 进行中\ntags: [项目]\n---\n\n# 项目名\n\n"
         "## 相关链接\n- [[项目卡名]]\n- [[知识卡名]]\n- [[2026-xx-xx 对话纪要]]\n"),
        ("99-模板/知识卡模板.md",
         "---\ntype: knowledge\ntags: [知识]\n---\n\n# 知识卡名\n\n## 相关\n- [[来源项目]]\n"
         "- [[关联知识卡]]\n"),
        ("99-模板/对话日记模板.md",
         "---\ntype: daily\ntags: [日记]\n---\n\n# {{date}} 对话纪要\n\n## 知识产出\n- [[知识卡名]]\n"
         "- [[待办引用]]\n- [[项目代号]]\n"),
    ]
    for rel, content in tpl_specs:
        w(root, rel, content)
        note_count += 1
        for line in content.splitlines():
            if "[[" in line and "]]" in line:
                tgt = line.split("[[", 1)[1].split("]]", 1)[0]
                gt["expected_template_placeholders"].append({"from": rel, "target": tgt})

    # ── 5. 归档快照：15 个，均不写 frontmatter（属正常，应豁免）──
    for a in range(1, N_ARCHIVE + 1):
        rel = f"90-归档/手册-v{a}.0-快照.md"
        w(root, rel, f"# 手册 v{a}.0 快照\n\n归档留存的旧版本内容。\n")
        note_count += 1

    # ── 6. 附件：图片 / PDF / Canvas ──
    for i in range(1, N_IMAGES + 1):
        p = root / f"assets/图{i:03d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + f"img{i}".encode())
    for i in range(1, N_PDFS + 1):
        p = root / f"assets/文档{i:02d}.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.4\n" + f"pdf{i}".encode())
    for i in range(1, N_CANVAS + 1):
        # 每个白板写入不同内容：否则多个空板 hash 相同，会被误判为「重复笔记」
        w(root, f"白板{i}.canvas",
          json.dumps({"nodes": [
              {"id": f"n{i}", "type": "file", "file": "00-库规.md"},
              {"id": f"t{i}", "type": "text", "text": f"白板 {i}"},
          ], "edges": []}, ensure_ascii=False))

    # ── 7. 注入真断链：15 条，落在真实存在的项目笔记里 ──
    # 按生成时的命名公式反推真实路径，确保每条都命中（早期版本算错文件名，
    # 15 条只成功注入 2 条，导致召回率样本失真）。
    for b in range(1, N_TRUE_BROKEN + 1):
        pi = (b - 1) % N_PROJECTS + 1
        j = (b - 1) % NOTES_PER_PROJECT + 1
        pname = f"项目{pi:02d}"
        topic = TOPICS[j % len(TOPICS)]
        rel = f"10-项目档案/{pname}/{pname}-{topic}{j}.md"
        missing = f"不存在的笔记{b:02d}"
        p = root / rel
        assert p.exists(), f"断链注入目标不存在：{rel}"
        content = p.read_text(encoding="utf-8")
        content += f"\n参考 [[{missing}]] 获取更多信息。\n"
        p.write_text(content, encoding="utf-8")
        gt["expected_broken"].append({"from": rel, "target": missing})

    # ── 8. 真重复：10 组，同内容不同文件名 ──
    for g in range(1, N_DUP_GROUPS + 1):
        proj = f"项目{(g % N_PROJECTS) + 1:02d}"
        body = fm(f"重复内容 {g}", ["知识"]) + \
               f"# 重复内容 {g}\n\n这是一段会被重复保存的正文，第 {g} 组。\n"
        a = f"20-领域知识/重复-A{g:02d}.md"
        b = f"20-领域知识/重复-B{g:02d}.md"
        w(root, a, body)
        w(root, b, body)
        note_count += 2
        gt["expected_duplicates"].append([a, b])

    # ── 9. 正文孤儿的 ground truth：按生成结构精确推导，不依赖引擎输出 ──
    # 有入链的正文：项目卡 20（库规链）+ 项目笔记 100（项目卡链）+ 知识卡 1-30（库规链）
    # 无入链的正文：知识卡 31-100（70 篇）+ 重复文件 20 篇
    for k in range(31, N_CARDS + 1):
        domain = DOMAINS[k % len(DOMAINS)]
        gt["expected_orphans"].append(f"20-领域知识/{domain}/知识卡{k:03d}.md")
    for g in range(1, N_DUP_GROUPS + 1):
        gt["expected_orphans"].append(f"20-领域知识/重复-A{g:02d}.md")
        gt["expected_orphans"].append(f"20-领域知识/重复-B{g:02d}.md")

    # ── 10. 系统垃圾文件：20 个（应全部排除）──
    # 每个放在唯一目录下：早期版本按 j%5 轮转目录 + 3 种名字，同名同目录互相覆盖，
    # 实际只落盘 15 个，导致排除率虚低。
    junk_names = [".DS_Store", "Thumbs.db", "desktop.ini"]
    junk_dirs = ([f"10-项目档案/项目{i:02d}" for i in range(1, 11)]      # 10
                 + [f"20-领域知识/{d}" for d in DOMAINS]                 # 5
                 + ["30-对话日记", "90-归档", "assets", "99-模板", ""])   # 5 → 共 20
    for j in range(N_JUNK):
        sub = junk_dirs[j]
        name = junk_names[j % len(junk_names)]
        rel = f"{sub}/{name}" if sub else name
        w(root, rel, "junk")
        gt["expected_junk"].append(rel)

    gt["injected"] = {
        "total_notes": note_count,
        "true_broken_links": len(gt["expected_broken"]),
        "template_placeholders": len(gt["expected_template_placeholders"]),
        "true_orphans": len(gt["expected_orphans"]),
        "duplicate_groups": len(gt["expected_duplicates"]),
        "same_name_diff_groups": 1,  # 「项目卡」一组，20 个文件
        "missing_fm_content": len(gt["expected_missing_fm"]),
        "archive_no_fm": N_ARCHIVE,
        "junk_files": len(gt["expected_junk"]),
        "images": N_IMAGES,
        "pdfs": N_PDFS,
        "canvas": N_CANVAS,
        "diary_notes": N_DIARY,
    }

    gt_path = args.gt or str(root.parent / "gt.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)

    print(f"✅ 已生成模拟库：{root}")
    print(f"   笔记 {note_count} 篇 · 图片 {N_IMAGES} · PDF {N_PDFS} · Canvas {N_CANVAS} · 垃圾文件 {len(gt['expected_junk'])}")
    print(f"   ground truth → {gt_path}")


if __name__ == "__main__":
    main()
