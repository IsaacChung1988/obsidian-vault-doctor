#!/usr/bin/env python3
"""gen_alt_vaults.py — 生成「另一种库结构」的压测库，验证 role 分档是否只在 PARA 库有效。

为什么需要它：
  第一轮压测只覆盖 PARA 风格（多级目录 + 项目档案 + 领域知识）。
  但 Obsidian 用户里另有两类完全不同的库，role 判定逻辑（日记/归档/索引豁免）
  在这两类库上很可能失效：
    ① Zettelkasten：全平铺、无目录、文件名带时间戳 ID、双链密集
    ② 纯时间流：几乎全是日记，按日期命名

已知风险（第一轮报告 §七 自己写的）：
  · 纯时间流库上 role 分档可能失效
  · Zettelkasten 用「日期前缀」命名原子卡片时，会被误判成日记 → 孤儿被隐藏

用法：
  python3 gen_alt_vaults.py zettel <目标目录> [--gt gt.json]
  python3 gen_alt_vaults.py timeline <目标目录> [--gt gt.json]
"""
import argparse
import datetime
import json
import random
import re
import shutil
import sys
from pathlib import Path

TRAP_NOTE = "日期前缀原子卡（ZK 常见命名）是否被误判成日记"


def w(root: Path, rel: str, content: str):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def fm(title, tags, extra=""):
    body = f"---\ntitle: {title}\ntags: [{', '.join(tags)}]\n"
    if extra:
        body += extra
    body += "---\n\n"
    return body


def write_gt(root, gt, path=None):
    gt_path = path or str(root.parent / f"gt-{root.name}.json")
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)
    return gt_path


# ══════════════════════════════════════════════════════════════════
# ① Zettelkasten：全平铺 · 时间戳 ID · 双链密集
# ══════════════════════════════════════════════════════════════════
def gen_zettel(root: Path, random_seed=42):
    random.seed(random_seed)
    N_LINKED = 200          # 时间戳 ID 原子卡（无日期分隔符 → 不该被判成日记）
    N_DATE_PREFIX = 40      # 日期前缀原子卡（ZK 陷阱：形似日记，实为内容）
    N_ORPHAN_TS = 25        # 时间戳卡里真正无任何链接的（真孤儿）
    N_ORPHAN_DATE = 10      # 日期前缀卡里真正无链接的（真孤儿，用于测漏报）
    N_BROKEN = 8
    N_DUP = 5
    N_JUNK = 8

    gt = {
        "vault": str(root), "structure": "zettelkasten",
        "generated_at": datetime.datetime.now().isoformat(),
        "injected": {}, "expected_orphans": [], "expected_broken": [],
        "expected_duplicates": [], "expected_template_placeholders": [],
        "expected_missing_fm": [], "expected_junk": [],
        "expected_content": [], "expected_daily": [],
        "known_trap": TRAP_NOTE,
    }

    # 索引页（MOC）：唯一入口
    links = "\n".join(f"- [[26{800+i:05d} 卡片{i}]]" for i in range(1, 41))
    w(root, "000 卡片索引.md",
      fm("卡片索引", ["moc"], "type: moc\n") +
      "# 卡片索引\n\n## 起步卡\n" + links +
      "\n## 闪念入口\n- [[2026-09-01 闪念1]]\n")
    gt["expected_content"].append("000 卡片索引.md")  # role=index 也算非孤儿，见 eval

    # 时间戳 ID 原子卡：2608NNNNN 卡片N
    # 注意：链式引用只在「非孤儿集合内」闭合，否则孤儿会意外获得入链，
    # ground truth 就会标错（上一版正是如此，把 176/177 号卡错标成孤儿）。
    LAST_LINKED = N_LINKED - N_ORPHAN_TS
    for i in range(1, N_LINKED + 1):
        rel = f"26{800+i:05d} 卡片{i}.md"
        is_orphan = i > LAST_LINKED
        refs = ""
        if not is_orphan and i + 2 <= LAST_LINKED:
            refs = (f"\n关联：[[26{800+i+1:05d} 卡片{i+1}]] · "
                    f"[[26{800+i+2:05d} 卡片{i+2}]]\n")
        w(root, rel,
          fm(f"卡片{i}", ["卡片", "zk"]) +
          f"# 卡片 {i}\n\n第 {i} 张原子卡的内容，主张编号 #{i}，"
          f"与相邻卡片存在概念上的邻接关系。\n{refs}")
        gt["expected_content"].append(rel)
        if is_orphan:
            gt["expected_orphans"].append(rel)

    # 日期前缀原子卡（陷阱）：2026-09-01 闪念N — 内容是原子卡，不是日记
    LAST_DATE_LINKED = N_DATE_PREFIX - N_ORPHAN_DATE
    for i in range(1, N_DATE_PREFIX + 1):
        day = datetime.date(2026, 9, 1) + datetime.timedelta(days=(i - 1) % 28)
        rel = f"{day.isoformat()} 闪念{i}.md"
        is_orphan = i > LAST_DATE_LINKED
        refs = ""
        if not is_orphan and i + 1 <= LAST_DATE_LINKED:
            nxt = datetime.date(2026, 9, 1) + datetime.timedelta(days=(i) % 28)
            refs = f"\n延伸：[[{nxt.isoformat()} 闪念{i+1}]]\n"
        w(root, rel,
          fm(f"闪念{i}", ["闪念", "zk"]) +
          f"# 闪念 {i}\n\n这是一条独立的原子思考（不是日记）："
          f"关于知识网络第 {i} 个观察。\n{refs}")
        gt["expected_content"].append(rel)
        if is_orphan:
            gt["expected_orphans"].append(rel)

    # 模板：目录名故意用非标准 `_素材`（测启发式）
    tpls = [
        ("_素材/卡片模板.md",
         "---\ntype: template\ntags: [卡片]\n---\n\n# {{title}}\n\n"
         "关联：[[{{related}}]] · 来源 [[待补充来源]]\n"),
        ("_素材/闪念模板.md",
         "---\ntype: template\ntags: [闪念]\n---\n\n# {{date}} {{title}}\n\n"
         "延伸：[[相关内容]]\n"),
    ]
    for rel, content in tpls:
        w(root, rel, content)
        for line in content.splitlines():
            for tgt in re.findall(r"\[\[([^\[\]]+?)(?:\|[^\[\]]+)?\]\]", line):
                gt["expected_template_placeholders"].append({"from": rel, "target": tgt})

    # 真断链：8 条，落在链接充分的卡片上
    for b in range(1, N_BROKEN + 1):
        rel = f"26{800+b:05d} 卡片{b}.md"
        p = root / rel
        assert p.exists(), rel
        p.write_text(p.read_text(encoding="utf-8") +
                     f"\n引用 [[不存在的卡片{b:02d}]] 作为反例。\n", encoding="utf-8")
        gt["expected_broken"].append({"from": rel, "target": f"不存在的卡片{b:02d}"})

    # 真重复：5 组，字节一致
    for g in range(1, N_DUP + 1):
        body = fm(f"重复卡 {g}", ["卡片"]) + \
               f"# 重复卡 {g}\n\n同一段被粘贴两次的原子内容，第 {g} 组。\n"
        a, b = f"_重复-A{g}.md", f"_重复-B{g}.md"
        w(root, a, body)
        w(root, b, body)
        gt["expected_duplicates"].append([a, b])
        gt["expected_orphans"].extend([a, b])

    # 垃圾文件：8 个，路径必须互不相同
    # （上一轮压测的教训：同名同目录会互相覆盖，实际落盘比记账少 → 得出假结论）
    junk_rels = [".DS_Store", "Thumbs.db", "._卡片3.md", "._卡片4.md",
                 "desktop.ini", "._未命名.md", ".~lock.md", "临时.tmp"]
    assert len(set(junk_rels)) == N_JUNK
    for rel in junk_rels:
        w(root, rel, "junk")
        gt["expected_junk"].append(rel)

    gt["injected"] = {
        "total_notes": len(gt["expected_content"]) + len(tpls) + 2 * N_DUP,
        "true_orphans": len(gt["expected_orphans"]),
        "true_broken_links": N_BROKEN,
        "template_placeholders": len(gt["expected_template_placeholders"]),
        "duplicate_groups": N_DUP,
        "junk_files": len(gt["expected_junk"]),
        "same_name_diff_groups": 0,
        "missing_fm_content": 0,
        "archive_no_fm": 0,
        "content_notes": len(gt["expected_content"]),
    }
    return gt


# ══════════════════════════════════════════════════════════════════
# ② 纯时间流：几乎全是日记
# ══════════════════════════════════════════════════════════════════
def gen_timeline(root: Path, random_seed=42):
    random.seed(random_seed)
    N_DAILY = 180
    N_WEEKLY = 20
    N_CONTENT = 30
    N_ORPHAN_CONTENT = 6
    N_BROKEN = 8
    N_DUP = 4
    N_JUNK = 6

    gt = {
        "vault": str(root), "structure": "timeline",
        "generated_at": datetime.datetime.now().isoformat(),
        "injected": {}, "expected_orphans": [], "expected_broken": [],
        "expected_duplicates": [], "expected_template_placeholders": [],
        "expected_missing_fm": [], "expected_junk": [],
        "expected_content": [], "expected_daily": [],
        "known_trap": "全库都是日记时，孤儿检测是否整体失效",
    }

    # Obsidian 核心「日记」插件配置（引擎应读取而非靠猜测）
    w(root, ".obsidian/daily-notes.json",
      json.dumps({"folder": "日记", "format": "YYYY-MM-DD", "template": "99-模板/日记模板"}, ensure_ascii=False))

    # 日记：180 篇，全部无入链（时间流天然如此）
    start = datetime.date(2026, 1, 1)
    for d in range(N_DAILY):
        day = start + datetime.timedelta(days=d)
        rel = f"日记/{day.isoformat()}.md"
        body = fm(f"{day.isoformat()} 日记", ["日记"], "type: daily\n") + \
               f"# {day.isoformat()}\n\n今日记录 {d + 1}。\n"
        # 最后 10 篇缺 frontmatter（测「缺元数据」是否会把日记算进去）
        if d >= N_DAILY - 10:
            body = f"# {day.isoformat()}\n\n今日记录 {d + 1}（未按模板建）。\n"
            gt["expected_missing_fm"].append(rel)
        w(root, rel, body)
        gt["expected_daily"].append(rel)

    # 周复盘：20 篇，靠 MOC 索引 → 有入链
    week_links = "\n".join(f"- [[2026-W{wk:02d} 周复盘]]" for wk in range(1, N_WEEKLY + 1))
    # 专题 01-24 在总览里有入口（有入链）；25-30 故意不进总览 → 真孤儿。
    # 上一版只让专题出链、没给入链，导致 24 篇真孤儿被 gt 漏标，
    # 误判成引擎"孤儿误报 24 篇"，精确率虚低到 36.8%。这是压测工具的错，不是引擎的错。
    topic_links = "\n".join(f"- [[主题{i:02d}]]" for i in range(1, N_CONTENT - N_ORPHAN_CONTENT + 1))
    w(root, "00 周复盘总览.md", fm("周复盘总览", ["索引"], "type: index\n") +
      "# 周复盘总览\n\n## 周复盘\n" + week_links + "\n\n## 专题沉淀\n" + topic_links + "\n")
    for wk in range(1, N_WEEKLY + 1):
        rel = f"周复盘/2026-W{wk:02d} 周复盘.md"
        w(root, rel, fm(f"2026-W{wk:02d} 周复盘", ["复盘"], "type: weekly\n") +
          f"# 2026-W{wk:02d} 周复盘\n\n本周完成 {wk} 项，遗留 {wk % 3} 项。\n")
        gt["expected_content"].append(rel)  # role 应为 content（非纯日记）

    # 项目/主题笔记：30 篇，其中 6 篇真孤儿
    for i in range(1, N_CONTENT + 1):
        rel = f"专题/主题{i:02d}.md"
        is_orphan = i > N_CONTENT - N_ORPHAN_CONTENT
        refs = "" if is_orphan else f"\n参见：[[2026-W{i % N_WEEKLY + 1:02d} 周复盘]]\n"
        w(root, rel, fm(f"主题{i:02d}", ["专题"], "type: note\n") +
          f"# 主题 {i:02d}\n\n第 {i} 个专题的沉淀内容。\n{refs}")
        gt["expected_content"].append(rel)
        if is_orphan:
            gt["expected_orphans"].append(rel)

    # 模板（标准目录名）：2 个含占位符
    tpls = [
        ("99-模板/日记模板.md",
         "---\ntype: template\n---\n\n# {{date}}\n\n## 今日\n- \n\n## 关联\n- [[昨日日记]]\n"),
        ("99-模板/周复盘模板.md",
         "---\ntype: template\n---\n\n# {{date}} 周复盘\n\n- 本周链接：[[本周日记]]\n"),
    ]
    for rel, content in tpls:
        w(root, rel, content)
        for line in content.splitlines():
            for tgt in re.findall(r"\[\[([^\[\]]+?)(?:\|[^\[\]]+)?\]\]", line):
                gt["expected_template_placeholders"].append({"from": rel, "target": tgt})

    # 真断链：8 条，落在专题笔记
    for b in range(1, N_BROKEN + 1):
        rel = f"专题/主题{b:02d}.md"
        p = root / rel
        assert p.exists(), rel
        p.write_text(p.read_text(encoding="utf-8") +
                     f"\n待补：[[未创建的专题{b:02d}]]\n", encoding="utf-8")
        gt["expected_broken"].append({"from": rel, "target": f"未创建的专题{b:02d}"})

    # 真重复：4 组
    for g in range(1, N_DUP + 1):
        body = fm(f"重复主题 {g}", ["专题"]) + \
               f"# 重复主题 {g}\n\n重复保存的同段内容，第 {g} 组。\n"
        a, b = f"专题/重复-A{g}.md", f"专题/重复-B{g}.md"
        w(root, a, body)
        w(root, b, body)
        gt["expected_duplicates"].append([a, b])
        gt["expected_orphans"].extend([a, b])

    # 垃圾文件：6 个互不相同的路径
    junk_rels = ["日记/.DS_Store", "日记/._2026-01-01.md", "日记/Thumbs.db",
                 "专题/.DS_Store", ".DS_Store", "专题/._主题01.md"]
    assert len(set(junk_rels)) == N_JUNK
    for rel in junk_rels:
        w(root, rel, "junk")
        gt["expected_junk"].append(rel)

    gt["injected"] = {
        "total_notes": N_DAILY + N_WEEKLY + N_CONTENT + len(tpls) + 2 * N_DUP + 1,
        "true_orphans": len(gt["expected_orphans"]),
        "true_broken_links": N_BROKEN,
        "template_placeholders": len(gt["expected_template_placeholders"]),
        "duplicate_groups": N_DUP,
        "junk_files": len(gt["expected_junk"]),
        "same_name_diff_groups": 0,
        "missing_fm_content": len(gt["expected_missing_fm"]) - N_DAILY + 10,
        "archive_no_fm": 0,
        "content_notes": len(gt["expected_content"]),
        "daily_notes": N_DAILY,
    }
    return gt


def main():
    ap = argparse.ArgumentParser(description="生成非 PARA 结构的压测库")
    ap.add_argument("mode", choices=["zettel", "timeline"])
    ap.add_argument("root")
    ap.add_argument("--gt", default=None)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    gt = gen_zettel(root) if args.mode == "zettel" else gen_timeline(root)
    gt_path = write_gt(root, gt, args.gt)

    s = gt["injected"]
    print(f"✅ 已生成 {gt['structure']} 库：{root}")
    print(f"   笔记 {s['total_notes']} 篇 · 孤儿注入 {s['true_orphans']} · 断链注入 {s['true_broken_links']}")
    print(f"   ground truth → {gt_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
