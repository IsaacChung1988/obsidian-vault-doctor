#!/usr/bin/env python3
"""plan.py — 治理计划生成器（只读，不执行任何写操作）。

用法：
  python3 plan.py <index.json> [--dup dup.json] [-o plan.md] [--json plan.json]

与 health.py 的分工（不重复造轮子，也不重复刷屏）：
  health.py 回答「我的库有什么问题」——按类别列全。
  plan.py   回答「我该先干什么」——按可执行性排序，标风险，算做完能到几分。

为什么要多这一层：一个 300 篇的库体检出 30 项问题，用户会直接关掉窗口。
真正有用的是「先做这 3 件（零风险），再看这 5 件（要判断），最后 4 类别碰」。
所以这里把问题按**有没有唯一正确答案**分成三级：

  A 级 立刻能修 —— 有唯一正确答案，做完不会后悔（垃圾文件/完全重复/真断链）
  B 级 要你判断 —— 没有标准答案，取决于你怎么用这个库（相似笔记/孤儿/元数据）
  C 级 不要动   —— 看着像问题其实是常态，动了反而坏（模板占位/日记无入链/同名异内容）

本脚本纯标准库、自包含（不 import 引擎其他脚本），因为它要能单独分发。
"""
import argparse
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict


def _disp_width(s):
    """中文按 2 列算。用 str.ljust 对齐中文会错位——'正文断链'.ljust(16) 只补 12 个空格，
    而 '系统垃圾文件' 补 4 个，视觉上差 4 列。必须按显示宽度补。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in s)


def pad(s, width):
    return s + " " * max(0, width - _disp_width(s))

# ── 与引擎保持一致的排除口径（自包含，故内联）────────────────────
JUNK_BASENAMES = {".DS_Store", "Thumbs.db", "desktop.ini", ".localized"}
JUNK_PREFIXES = ("._", "~$")


def is_junk(basename):
    if basename in JUNK_BASENAMES:
        return True
    return any(basename.startswith(p) for p in JUNK_PREFIXES)


def load_json(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return json.load(f)


def scan_junk(vault_root):
    """重新走一遍库找垃圾文件（只读）。scan.py 只统计了数量没存名单，
    而用户要动手删就必须知道删哪几个。成本：一次目录遍历，可忽略。"""
    found = []
    if not vault_root or not os.path.isdir(vault_root):
        return found
    for dirpath, dirnames, filenames in os.walk(vault_root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".obsidian", ".trash", ".git", "node_modules")]
        for fn in filenames:
            if is_junk(fn):
                rel = os.path.relpath(os.path.join(dirpath, fn), vault_root)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)


# ── 分级 ────────────────────────────────────────────────────────

def analyse(index, dup_payload=None):
    files = index["files"]
    links = index["links"]
    notes = [f for f in files if f["type"] == "note"]
    by_path = {f["path"]: f for f in notes}
    role = {f["path"]: f.get("role", "content") for f in notes}

    content_notes = [p for p, r in role.items() if r == "content"]

    # 断链：模板里的 [[示例名]] 是待填占位，不是病
    broken_real, broken_placeholder = [], []
    for l in links:
        if l["resolved"] is not None:
            continue
        fr = l.get("from_role") or role.get(l["from"], "content")
        (broken_placeholder if fr == "template" else broken_real).append(l)

    # 孤儿：只有正文无入链才算
    incoming = {l["resolved"] for l in links if l["resolved"]}
    orphans, orphan_exempt = [], defaultdict(list)
    for p in sorted(role):
        if p in incoming:
            continue
        r = role[p]
        (orphans.append(p) if r == "content" else orphan_exempt[r].append(p))

    # 缺元数据：归档/模板缺是常态
    missing_fm, missing_fm_archive, missing_fm_template = [], [], []
    for p in sorted(role):
        if by_path[p].get("frontmatter"):
            continue
        r = role[p]
        if r == "archive":
            missing_fm_archive.append(p)
        elif r == "template":
            missing_fm_template.append(p)
        else:
            missing_fm.append(p)

    # 同名分组：同内容=真重复；异内容=正常分类（各目录一份「项目卡」）
    stem_groups = defaultdict(list)
    for p in role:
        stem_groups[os.path.splitext(os.path.basename(p))[0]].append(p)
    dup_names, same_name_diff = {}, {}
    for stem, paths in stem_groups.items():
        if len(paths) < 2:
            continue
        roles = {role[p] for p in paths}
        hashes = {by_path[p].get("content_hash") for p in paths}
        if len(hashes) == 1 and not ("template" in roles and len(roles) > 1):
            dup_names[stem] = paths
        else:
            same_name_diff[stem] = paths

    # 完全重复（文件名不同）：只算纯笔记组，canvas/附件不混进来
    note_set = set(role)
    content_dups = []
    for g in index.get("duplicates", []):
        paths = g.get("paths", [])
        if not paths or not all(p in note_set for p in paths):
            continue
        if len({os.path.splitext(os.path.basename(p))[0] for p in paths}) > 1:
            content_dups.append(paths)

    # 近似重复：由 dup.py --json 提供；没提供就标「未检测」而不是假装 0
    near = {"AUTO": [], "PROPOSE": [], "DRAFT": [], "LIST": []}
    dup_available = dup_payload is not None
    if dup_available:
        for c in dup_payload.get("candidates", []):
            near.setdefault(c.get("tier"), []).append(c)

    junk = scan_junk(index.get("vault_root"))

    # 目录集中度：某个目录占正文过半 → 可能是「什么都往里扔」的垃圾桶目录
    dir_counts = Counter()
    for p in content_notes:
        parts = p.split("/")
        dir_counts["/".join(parts[:2]) if len(parts) > 2 else (parts[0] if len(parts) > 1 else "(根目录)")] += 1
    hot_dirs = []
    if content_notes:
        for d, c in dir_counts.most_common(3):
            if c >= max(20, len(content_notes) * 0.5):
                hot_dirs.append((d, c))

    return {
        "counts": {
            "notes": len(notes),
            "content": len(content_notes),
            "links": len(links),
            "junk": len(junk),
            "broken": len(broken_real),
            "broken_placeholder": len(broken_placeholder),
            "orphans": len(orphans),
            "missing_fm": len(missing_fm),
            "dup_names": len(dup_names),
            "content_dups": len(content_dups),
            "roles": dict(Counter(role.values())),
        },
        "detail": {
            "junk": junk,
            "broken": broken_real,
            "orphans": orphans,
            "missing_fm": missing_fm,
            "dup_names": dup_names,
            "content_dups": content_dups,
            "same_name_diff": same_name_diff,
            "orphan_exempt": {k: sorted(v) for k, v in orphan_exempt.items()},
            "broken_placeholder": broken_placeholder,
            "missing_fm_archive": missing_fm_archive,
            "missing_fm_template": missing_fm_template,
            "near": near,
            "hot_dirs": hot_dirs,
        },
        "dup_available": dup_available,
    }


# ── 健康分（口径透明，写死在这里，不玩黑箱）──────────────────────
# 四项固定权重，按「发生率」线性扣分，但每项有个「扣满临界点」：
# 断链率到 15%、孤儿率到 60%、重复率到 30%、缺元数据到 70% 时该项扣光。
#
# 为什么不是纯线性：第一版用「率 × 权重」直接扣，319 篇的库有 90 篇孤儿 + 15 条断链
# 却算出 85 分，跟用户实际感受完全不符（那库明显挺乱的）。纯线性下 5% 的断链率
# 只扣 1.6 分——但 5% 断链意味着每 20 个链接就有一个是坏的，一点都不轻。
# 给每项设临界点后，同一个库算出 64 分，和「有明显问题但没塌」的直觉一致。
#
# 分母设下限（链接至少按 20 条、正文至少按 10 篇），避免 9 篇的小库被单条问题放大。
CAP_BROKEN, CAP_ORPHAN, CAP_DUP, CAP_FM = 0.15, 0.60, 0.30, 0.70


def health_score(index, a):
    c = a["counts"]
    content_n = max(1, c["content"])

    broken = min(1.0, c["broken"] / max(20, c["links"]) / CAP_BROKEN)
    orphan = min(1.0, c["orphans"] / max(10, content_n) / CAP_ORPHAN)
    dup_notes = (len({p for g in a["detail"]["content_dups"] for p in g})
                 + 2 * c["dup_names"])
    dup = min(1.0, dup_notes / max(10, content_n) / CAP_DUP)
    fm = min(1.0, c["missing_fm"] / max(10, c["notes"]) / CAP_FM)

    penalty = broken * 30 + orphan * 25 + dup * 25 + fm * 20
    return max(0, min(100, round(100 - penalty)))


def score_after(index, a, fix_levles=("A",)):
    """模拟「做完某一级之后」的分数。只改计数，不改库。"""
    c = dict(a["counts"])
    d = a["detail"]
    if "A" in fix_levles:
        c["junk"] = 0
        c["broken"] = 0
        c["content_dups"] = 0
        c["dup_names"] = 0
    if "B" in fix_levles:
        c["orphans"] = 0
        c["missing_fm"] = 0
    fake = {"counts": c, "detail": {**d, "content_dups": []}}
    return health_score(index, fake)


# ── 输出 ────────────────────────────────────────────────────────

LEVEL_A_HINT = {
    "junk": "这些文件 Obsidian 自己也不认，由你移入 .trash 或删除均不影响任何笔记（本工具只给建议，不代删）",
    "content_dups": "字节级一致，合并后一个字都不会丢",
    "broken": "每条都有明确的修复目标，不是猜",
    "dup_names": "同名且内容一致，留一份就够",
}


def render_plan(index, a, score):
    c = a["counts"]
    d = a["detail"]
    s = index["stats"]
    out = []

    role_label = [("content", "正文"), ("daily", "日记"), ("template", "模板"),
                  ("archive", "归档"), ("index", "索引页")]
    comp = " · ".join(f"{lb} {c['roles'][k]}" for k, lb in role_label if c["roles"].get(k))
    out.append("=" * 52)
    out.append(f"你的库：{c['notes']} 篇笔记（{comp}）")
    out.append(f"　　　　附件 {s.get('total_attachments', 0)} · Canvas {s.get('total_canvas', 0)}"
               f" · 链接 {c['links']} 条")
    # 小库不给「分数」的错觉：7 篇里 6 篇没链接，按比例算就是 41 分，
    # 但刚建的库本来就没链接，报低分只会让用户以为工具在骂人。
    score_line = f"健康分：{score} / 100"
    if c["content"] < 20:
        score_line += f"　（正文仅 {c['content']} 篇，样本太小，分数参考意义有限）"
    out.append(score_line)
    out.append("=" * 52)

    # ── A 级 ──
    # 先收集再统一编号：写死 A1/A2/A3/A4 会在某项缺失时出现 A1→A2→A4 的断号，
    # 用户会以为漏了一步。
    a_items = []
    if c["junk"]:
        a_items.append(("系统垃圾文件", f"{c['junk']} 个", "零风险", LEVEL_A_HINT["junk"]))
    if c["content_dups"]:
        a_items.append(("完全重复的笔记", f"{c['content_dups']} 组", "零风险", LEVEL_A_HINT["content_dups"]))
    if c["dup_names"]:
        a_items.append(("同名同内容的笔记", f"{c['dup_names']} 组", "零风险", LEVEL_A_HINT["dup_names"]))
    if c["broken"]:
        a_items.append(("正文断链", f"{c['broken']} 条", "低风险", LEVEL_A_HINT["broken"]))

    out.append("")
    if a_items:
        out.append(f"A 级 · 立刻能修（{len(a_items)} 项）")
        out.append("  这几项有唯一正确答案，修完不会后悔。")
        out.append("")
        for i, (name, num, risk, hint) in enumerate(a_items, 1):
            out.append(f"  A{i}  {pad(name, 20)}{num:>7}   {risk}")
            out.append(f"      {hint}")
    else:
        out.append("A 级 · 立刻能修（0 项）")
        out.append("  ✓ 没有这类问题，可以直接看 B 级。")

    # ── B 级 ──
    b_items = []
    if a["dup_available"]:
        if d["near"].get("PROPOSE"):
            b_items.append(("高度相似笔记（≥0.95）", f"{len(d['near']['PROPOSE'])} 组",
                            "逐组看一遍，多半该合并"))
        if d["near"].get("DRAFT"):
            b_items.append(("中度相似笔记（0.85–0.95）", f"{len(d['near']['DRAFT'])} 组",
                            "可能是同一主题的不同侧面，别急着合"))
        if d["near"].get("LIST"):
            b_items.append(("低度相似（0.70–0.85）", f"{len(d['near']['LIST'])} 组",
                            "只列出，不建议动"))
    if c["orphans"]:
        b_items.append(("没人链接的笔记", f"{c['orphans']} 篇",
                        "要么链进索引，要么归档——孤立着最没用"))
    if c["missing_fm"]:
        b_items.append(("缺元数据的笔记", f"{c['missing_fm']} 篇",
                        "补 tags/status 才能被检索和看板用到"))

    out.append("")
    if b_items:
        out.append(f"B 级 · 要你判断（{len(b_items)} 项）")
        out.append("  这几项没有标准答案，取决于你怎么用这个库。")
        out.append("")
        for i, (name, num, hint) in enumerate(b_items, 1):
            out.append(f"  B{i}  {pad(name, 24)}{num:>7}")
            out.append(f"      {hint}")
    else:
        out.append("B 级 · 要你判断（0 项）")
        out.append("  ✓ 没有这类问题。")

    # 目录集中度单独成段：它是结构建议，不是「问题」，塞进 B 级表格会破坏对齐
    if d["hot_dirs"]:
        out.append("")
        out.append("  另外，这几个目录装得有点满，可以考虑拆子目录：")
        for dn, cnt in d["hot_dirs"]:
            out.append(f"    · {dn}/ → {cnt} 篇")

    if not a["dup_available"]:
        out.append("")
        out.append(f"  ⚠️ 近似重复未检测（没给 dup.json）。B 级里少了「相似笔记」一项，")
        out.append(f"     不代表没有。要看就跑：dup.py index.json --json dup.json")

    # ── C 级 ──
    c_items = []
    if c["broken_placeholder"]:
        c_items.append(f"模板占位链接 {c['broken_placeholder']} 条 —— 模板里的 [[示例]] 等着被填，不是断链")
    for rk, reason in (("daily", "按日期命名，天生没人回链"),
                       ("index", "库规/MOC 是链接的起点，不需要被链"),
                       ("archive", "归档是留存副本，不参与链接网络"),
                       ("template", "模板按设计不被链接")):
        if d["orphan_exempt"].get(rk):
            lb = {"daily": "日记", "index": "索引页", "archive": "归档", "template": "模板"}[rk]
            c_items.append(f"{lb}无入链 {len(d['orphan_exempt'][rk])} 篇 —— {reason}")
    if d["missing_fm_archive"]:
        c_items.append(f"归档缺元数据 {len(d['missing_fm_archive'])} 篇 —— 快照性质，补了也没人看")
    if d["missing_fm_template"]:
        c_items.append(f"模板缺元数据 {len(d['missing_fm_template'])} 篇 —— 模板是骨架，不算笔记")
    if d["same_name_diff"]:
        names = "、".join(list(d["same_name_diff"].keys())[:3])
        c_items.append(f"同名但内容不同 {len(d['same_name_diff'])} 组（{names}）—— "
                       f"这是分类设计，合并就错了")

    if c_items:
        out.append("")
        out.append(f"C 级 · 不要动（{len(c_items)} 类）")
        out.append("  下面这些看着像问题，其实是 Obsidian 库的常态。动了反而坏。")
        out.append("")
        for it in c_items:
            out.append(f"  · {it}")

    # ── 建议顺序 + 分数模拟 ──
    # A 级内部按「零风险 → 低风险」排：先删垃圾建立信心，再动有内容的东西。
    order = ([f"A{i}" for i in range(1, len(a_items) + 1)]
             + [f"B{i}" for i in range(1, len(b_items) + 1)])
    after_a = score_after(index, a, ("A",))
    after_ab = score_after(index, a, ("A", "B"))

    out.append("")
    out.append("-" * 52)
    if order:
        out.append(f"建议顺序：{' → '.join(order)}")
        if after_a == score:
            out.append(f"  做完 A 级：{score} 分（不变——垃圾文件不算进健康分，"
                       f"这一项是清理体感，不是提分）")
        else:
            out.append(f"  做完 A 级：{score} → {after_a} 分")
        out.append(f"  再做 B 级：{after_a} → {after_ab} 分")
        out.append("")
        if a["dup_available"] and any(d["near"].get(t) for t in ("PROPOSE", "DRAFT", "LIST")):
            out.append("  注：B 级里的「相似笔记」需要你逐组确认，不确认不要动。")
        out.append("      A 级里要动文件的那几项，先让引擎生成备份再执行，别手动删。")
    else:
        out.append("你的库没有需要修的问题。")
    out.append("-" * 52)

    return "\n".join(out), after_a, after_ab


def render_full(index, a, score, after_a, after_ab):
    """导出用的完整版：把每条明细都列出来，可逐条核对。"""
    c, d = a["counts"], a["detail"]
    L = []
    L.append("# 库治理计划（完整版）")
    L.append("")
    L.append(f"- 库路径：`{index.get('vault_root', '?')}`")
    L.append(f"- 规模：{c['notes']} 篇笔记 · {c['links']} 条链接")
    L.append(f"- **健康分 {score} / 100**（做完 A 级 {after_a}，做完 A+B {after_ab}）")
    L.append("")
    L.append("> 健康分口径：四项加权从 100 分里扣，每项有「扣满临界点」。")
    L.append(">")
    L.append("> | 项 | 权重 | 扣满条件 |")
    L.append("> |---|---|---|")
    L.append("> | 断链率 | 30 | 链接的 15% 断掉 |")
    L.append("> | 孤儿率 | 25 | 正文的 60% 无入链 |")
    L.append("> | 重复率 | 25 | 正文的 30% 是重复 |")
    L.append("> | 缺元数据率 | 20 | 笔记的 70% 无 frontmatter |")
    L.append(">")
    L.append("> 分母设了下限（链接至少按 20 条、正文至少按 10 篇算），避免小库被单条问题放大。")
    L.append("> **这是我定的口径，不是行业标准**，只用来对比治理前后，别拿去和别人的库比高低。")
    L.append("")

    L.append("## A 级 · 立刻能修")
    L.append("")
    if d["junk"]:
        L.append(f"### A1 系统垃圾文件（{len(d['junk'])} 个）")
        L.append("这些文件 Obsidian 不认，直接删。")
        L.append("")
        for p in d["junk"]:
            L.append(f"- `{p}`")
        L.append("")
    if d["content_dups"]:
        L.append(f"### A2 完全重复的笔记（{len(d['content_dups'])} 组）")
        L.append("字节完全一致，保留一份即可。合并前确认哪份在索引页里被引用。")
        L.append("")
        for g in d["content_dups"]:
            L.append(f"- {' ⇄ '.join(g)}")
        L.append("")
    if d["dup_names"]:
        L.append(f"### A3 同名同内容的笔记（{len(d['dup_names'])} 组）")
        L.append("")
        for stem, paths in d["dup_names"].items():
            L.append(f"- **{stem}**：{' / '.join(paths)}")
        L.append("")
    if d["broken"]:
        L.append(f"### A4 正文断链（{len(d['broken'])} 条）")
        L.append("每条都要人判断：是改成正确目标，还是删掉这个链接。")
        L.append("")
        for l in d["broken"]:
            L.append(f"- `{l['from']}` → `[[{l['to']}]]`")
        L.append("")

    L.append("## B 级 · 要你判断")
    L.append("")
    if a["dup_available"]:
        for tier, label in (("PROPOSE", "B1 高度相似 ≥0.95"), ("DRAFT", "B2 中度相似 0.85–0.95"),
                            ("LIST", "B3 低度相似 0.70–0.85")):
            items = d["near"].get(tier, [])
            if not items:
                continue
            L.append(f"### {label}（{len(items)} 组）")
            L.append("")
            for it in items:
                L.append(f"- {it['score']:.4f}　`{it['paths'][0]}` ⇄ `{it['paths'][1]}`")
            L.append("")
    if d["orphans"]:
        L.append(f"### B4 没人链接的笔记（{len(d['orphans'])} 篇）")
        L.append("不是必须处理，但一直孤立着的笔记等于不存在。二选一：链进索引页，或移进归档目录。")
        L.append("")
        for p in d["orphans"]:
            L.append(f"- `{p}`")
        L.append("")
    if d["missing_fm"]:
        L.append(f"### B5 缺元数据的笔记（{len(d['missing_fm'])} 篇）")
        L.append("")
        for p in d["missing_fm"]:
            L.append(f"- `{p}`")
        L.append("")

    L.append("## C 级 · 不要动")
    L.append("")
    L.append("| 现象 | 数量 | 为什么不用管 |")
    L.append("|---|---|---|")
    if c["broken_placeholder"]:
        L.append(f"| 模板占位链接 | {c['broken_placeholder']} | 模板里的 `[[示例名]]` 等着被填 |")
    for rk, reason, lb in (("daily", "按日期命名，天生没人回链", "日记无入链"),
                           ("index", "MOC/库规是链接起点", "索引页无入链"),
                           ("archive", "归档不参与链接网络", "归档无入链"),
                           ("template", "模板按设计不被链接", "模板无入链")):
        if d["orphan_exempt"].get(rk):
            L.append(f"| {lb} | {len(d['orphan_exempt'][rk])} | {reason} |")
    if d["missing_fm_archive"]:
        L.append(f"| 归档缺元数据 | {len(d['missing_fm_archive'])} | 快照性质 |")
    if d["missing_fm_template"]:
        L.append(f"| 模板缺元数据 | {len(d['missing_fm_template'])} | 模板是骨架 |")
    if d["same_name_diff"]:
        L.append(f"| 同名但内容不同 | {len(d['same_name_diff'])} | 分类设计，合并就错了 |")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="生成库治理计划（只读）")
    ap.add_argument("index", help="index.json 路径")
    ap.add_argument("--dup", default=None, help="可选：dup.py --json 产出的 dup.json")
    ap.add_argument("-o", "--output", default=None, help="可选：导出完整计划 markdown")
    ap.add_argument("--json", default=None, help="可选：导出结构化计划 JSON")
    args = ap.parse_args()

    index = load_json(args.index)
    dup_payload = load_json(args.dup) if args.dup else None

    a = analyse(index, dup_payload)
    score = health_score(index, a)
    text, after_a, after_ab = render_plan(index, a, score)
    print(text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(render_full(index, a, score, after_a, after_ab) + "\n")
        print(f"\n✅ 完整计划已写入 {args.output}")

    if args.json:
        payload = {
            "vault_root": index.get("vault_root"),
            "health_score": score,
            "score_after_A": after_a,
            "score_after_AB": after_ab,
            "counts": a["counts"],
            "dup_available": a["dup_available"],
            "detail": {
                "junk": a["detail"]["junk"],
                "broken": a["detail"]["broken"],
                "orphans": a["detail"]["orphans"],
                "missing_fm": a["detail"]["missing_fm"],
                "content_dups": a["detail"]["content_dups"],
                "dup_names": a["detail"]["dup_names"],
                "same_name_diff": a["detail"]["same_name_diff"],
                "orphan_exempt": a["detail"]["orphan_exempt"],
                "near_counts": {k: len(v) for k, v in a["detail"]["near"].items()},
            },
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"✅ 结构化计划已写入 {args.json}")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
