#!/usr/bin/env python3
"""health.py — 基于 index.json 计算健康报告（只读，不落写操作）。

用法：python3 health.py <index.json> [-o 报告.md]

输出分两层：
  🎯 需要处理 —— 真问题（正文断链 / 正文孤儿 / 正文缺元数据 / 内容重复）
  ℹ️ 已豁免   —— 不是库的病（模板占位链接 / 模板无入链 / 归档缺元数据 /
                 同名不同内容 / 系统垃圾文件）

设计原则：**不隐藏噪音，而是分类 + 明示。**
模板里的 [[示例名]] 必然断链、模板本身必然无入链、归档快照常常不写 frontmatter——
这些在每一个 Obsidian 库里都是常态。把它们报成「问题」只会淹没真正的毛病，
让用户觉得工具不懂 Obsidian。所以单独成档、写明原因，用户一眼知道哪些要管。
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict


def load_index(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def compute(index):
    files = index["files"]
    links = index["links"]
    notes = {f["path"]: f for f in files if f["type"] == "note"}
    note_paths = set(notes)

    # 文件角色：content（正文）/ template（模板）/ archive（归档快照）
    role = {p: f.get("role", "content") for p, f in notes.items()}

    # ── 1. 断链分档 ────────────────────────────────────────────
    # 模板里的链接多为 [[知识卡名]] 这类待填占位符，属正常；正文断链才是真问题。
    broken_real, broken_placeholder = [], []
    for l in links:
        if l["resolved"] is not None:
            continue
        fr = l.get("from_role") or role.get(l["from"], "content")
        (broken_placeholder if fr == "template" else broken_real).append(l)

    # ── 2. 孤儿笔记分档 ────────────────────────────────────────
    # 只有「正文」无入链才算孤儿。以下四类天然无入链，报出来是纯噪音：
    #   template 模板按设计不被链接 / archive 归档是留存副本
    #   index    库规·MOC·Home 是链接的起点 / daily 日记按日期命名，无人回链
    # 真实规模下（300+ 篇）这四类往往占「无入链文件」的八成以上。
    incoming = set(l["resolved"] for l in links if l["resolved"])
    orphans = []
    orphan_exempt = defaultdict(list)  # role -> [paths]
    for p in sorted(note_paths):
        if p in incoming:
            continue
        r = role.get(p, "content")
        if r == "content":
            orphans.append(p)
        else:
            orphan_exempt[r].append(p)

    # ── 3. 缺元数据分档 ────────────────────────────────────────
    # 归档/快照类文件（如「手册-v5.0.2-快照.md」）不写 frontmatter 是常见的，归豁免。
    missing_fm, missing_fm_archive, missing_fm_template = [], [], []
    for p in sorted(note_paths):
        if notes[p].get("frontmatter"):
            continue
        r = role.get(p)
        if r == "archive":
            missing_fm_archive.append(p)
        elif r == "template":
            missing_fm_template.append(p)
        else:
            missing_fm.append(p)

    # ── 4. 同名分档 ────────────────────────────────────────────
    # 同名 + 同内容 = 真重复（该合并）；
    # 同名 + 不同内容 = 正常分类设计（如各项目目录下各一份「项目卡」），不该报成病，
    #                 但 [[项目卡]] 这种写法确实有歧义，值得提示。
    stem_groups = defaultdict(list)
    for p in note_paths:
        stem_groups[os.path.splitext(os.path.basename(p))[0]].append(p)
    dup_names, same_name_diff = {}, {}
    for stem, paths in stem_groups.items():
        if len(paths) < 2:
            continue
        roles = {role.get(p) for p in paths}
        hashes = {notes[p].get("content_hash") for p in paths}
        if len(hashes) == 1:
            # 同内容：但若跨模板/正文，属模板与实例的关系，不算重复
            if "template" in roles and len(roles) > 1:
                same_name_diff[stem] = paths
            else:
                dup_names[stem] = paths
        else:
            same_name_diff[stem] = paths

    # ── 5. 内容完全重复（不同文件名，同一内容）────────────────
    # 只统计**纯笔记组**。白板(.canvas)内容一致通常是初始化默认状态、复制出来的空板，
    # 没有「合并」意义；附件重复另属「附件治理」范畴，不该混进笔记重复里污染结论。
    note_set = set(note_paths)
    content_dups = []
    for g in index.get("duplicates", []):
        paths = g.get("paths", [])
        if not paths or not all(p in note_set for p in paths):
            continue
        stems = {os.path.splitext(os.path.basename(p))[0] for p in paths}
        if len(stems) > 1:  # 不同名 → 独立成档；同名已在前一档处理
            content_dups.append(paths)

    # ── 6. 标签熵 ──────────────────────────────────────────────
    all_tags = []
    for f in files:
        if f["type"] == "note" and f.get("frontmatter"):
            t = f["frontmatter"].get("tags")
            if isinstance(t, list):
                all_tags.extend(t)
            elif isinstance(t, str):
                all_tags.append(t)
    tag_counter = Counter(all_tags)

    return {
        "broken_links": broken_real,
        "broken_placeholder": broken_placeholder,
        "orphan_notes": orphans,
        "orphan_exempt": {k: sorted(v) for k, v in orphan_exempt.items()},
        "missing_frontmatter": missing_fm,
        "missing_fm_archive": missing_fm_archive,
        "missing_fm_template": missing_fm_template,
        "duplicate_names": dup_names,
        "same_name_diff": same_name_diff,
        "content_duplicates": content_dups,
        "tags": dict(tag_counter),
        "tag_total": len(all_tags),
        "tag_unique": len(tag_counter),
    }


def _group_by_dir(paths, depth=2):
    """按目录聚合（默认两级），用于大库时避免刷屏。
    两级而非一级：只报「20-领域知识/ → 90 篇」太粗，用户不知道该去哪个子目录；
    「20-领域知识/产品设计/ → 14 篇」才是可行动的定位。"""
    groups = defaultdict(list)
    for p in paths:
        parts = p.split("/")
        if len(parts) >= depth + 1:
            key = "/".join(parts[:depth])
        elif len(parts) > 1:
            key = parts[0]
        else:
            key = "(根目录)"
        groups[key].append(p)
    return groups


def render(result, index, verbose=False):
    """verbose=False 用于终端（超过阈值按目录聚合，避免刷屏）；
    verbose=True 用于 -o 导出（完整清单，用户要逐条看）。"""
    s = index["stats"]
    lines = []
    lines.append("=" * 44)
    lines.append("Obsidian Vault 健康体检卡")
    lines.append("=" * 44)

    # 笔记构成：正文 / 模板 / 归档分列，用户一看就知道基数里含什么
    role_counts = Counter(f.get("role", "content") for f in index["files"] if f["type"] == "note")
    seg = f"笔记 {s['total_notes']}"
    extra = []
    for key, label in (("content", "正文"), ("daily", "日记"), ("template", "模板"),
                       ("archive", "归档"), ("index", "索引页")):
        if role_counts.get(key):
            extra.append(f"{label} {role_counts[key]}")
    if extra:
        seg += "（" + " · ".join(extra) + "）"
    lines.append(seg + f" · 附件 {s['total_attachments']} · Canvas {s['total_canvas']}")
    if s.get("excluded_junk"):
        lines.append(f"（已排除系统垃圾文件 {s['excluded_junk']} 个：.DS_Store / Thumbs.db 等）")

    # ── 第一层：真问题 ────────────────────────────────────────
    real_issues = (len(result["broken_links"]) + len(result["orphan_notes"])
                   + len(result["missing_frontmatter"])
                   + len(result["duplicate_names"]) + len(result["content_duplicates"]))
    lines.append(f"\n🎯 需要处理（真问题 {real_issues} 项）")

    if not real_issues:
        lines.append("   ✓ 没有发现问题，库是健康的")

    if result["broken_links"]:
        n = len(result["broken_links"])
        lines.append(f"\n  🔗 断链：{n}")
        shown = result["broken_links"] if verbose else result["broken_links"][:15]
        for l in shown:
            lines.append(f"     - {l['from']} → [[{l['to']}]]")
        if not verbose and n > 15:
            lines.append(f"     … 另有 {n - 15} 条")

    if result["orphan_notes"]:
        n = len(result["orphan_notes"])
        lines.append(f"\n  👻 孤儿笔记（无入链）：{n}")
        if verbose or n <= 15:
            for p in result["orphan_notes"]:
                lines.append(f"     - {p}")
        else:
            # 大库按目录聚合：90 篇孤儿刷 20 行文件名，用户看不出该干什么
            for dirname, ps in sorted(_group_by_dir(result["orphan_notes"]).items(),
                                      key=lambda kv: -len(kv[1])):
                lines.append(f"     - {dirname}/ → {len(ps)} 篇")
            lines.append(f"     （按目录聚合显示；-o 导出报告可看逐篇清单）")

    if result["missing_frontmatter"]:
        n = len(result["missing_frontmatter"])
        lines.append(f"\n  📋 缺 frontmatter：{n}")
        if verbose or n <= 15:
            for p in result["missing_frontmatter"]:
                lines.append(f"     - {p}")
        else:
            for dirname, ps in sorted(_group_by_dir(result["missing_frontmatter"]).items(),
                                      key=lambda kv: -len(kv[1])):
                lines.append(f"     - {dirname}/ → {len(ps)} 篇")
            lines.append(f"     （按目录聚合显示；-o 导出报告可看逐篇清单）")

    if result["duplicate_names"]:
        lines.append(f"\n  🔁 同名同内容（建议合并）：{len(result['duplicate_names'])}")
        for stem, paths in list(result["duplicate_names"].items())[:10]:
            lines.append(f"     - {stem}: {', '.join(paths)}")

    if result["content_duplicates"]:
        lines.append(f"\n  🔁 内容完全重复（文件名不同）：{len(result['content_duplicates'])}")
        for paths in result["content_duplicates"][:10]:
            lines.append(f"     - {', '.join(paths)}")

    # ── 第二层：已豁免 ────────────────────────────────────────
    # 每项为 (标题, [明细行])；类数只数标题，不数明细，避免「8 类」这类虚数。
    exempt_items = []
    if result["broken_placeholder"]:
        details = [f"{l['from']} → [[{l['to']}]]" for l in result["broken_placeholder"][:5]]
        exempt_items.append(
            (f"模板占位链接 {len(result['broken_placeholder'])}"
             f"（模板里的 [[示例名]] 待填写，属正常）", details))
    orphan_exempt = result.get("orphan_exempt", {})
    role_label = {"template": "模板", "archive": "归档", "index": "索引/库规页", "daily": "日记/时间流"}
    for rk in ("template", "archive", "index", "daily"):
        paths = orphan_exempt.get(rk, [])
        if paths:
            reason = {
                "template": "模板按设计不被链接",
                "archive": "归档是留存副本，不参与链接网络",
                "index": "库规/MOC 是链接的起点，无需被链",
                "daily": "日记按日期命名，天然无回链",
            }[rk]
            exempt_items.append((f"{role_label[rk]}无入链 {len(paths)}（{reason}）", []))
    if result["missing_fm_archive"]:
        exempt_items.append((f"归档/快照缺元数据 {len(result['missing_fm_archive'])}（快照性质，可忽略）", []))
    if result["missing_fm_template"]:
        exempt_items.append((f"模板缺元数据 {len(result['missing_fm_template'])}", []))
    if result["same_name_diff"]:
        names = "、".join(list(result["same_name_diff"].keys())[:5])
        exempt_items.append(
            (f"同名但内容不同 {len(result['same_name_diff'])}（属正常分类；"
             f"{names} —— 用 [[文件名]] 写链接会有歧义，建议带路径）", []))
    if s.get("excluded_junk"):
        exempt_items.append((f"系统垃圾文件 {s['excluded_junk']}（.DS_Store 等，已排除统计）", []))

    if exempt_items:
        lines.append(f"\nℹ️ 已豁免（{len(exempt_items)} 类，不是库的病）")
        for title, details in exempt_items:
            lines.append(f"   · {title}")
            for d in details:
                lines.append(f"       {d}")

    # ── 标签 ──────────────────────────────────────────────────
    tag_entropy = 0.0
    if result["tag_total"] > 0:
        tag_entropy = result["tag_unique"] / result["tag_total"]
    lines.append(f"\n🏷️  标签：{result['tag_total']} 个（去重 {result['tag_unique']}，熵 {tag_entropy:.2f}）")

    # ── 结论 ──────────────────────────────────────────────────
    lines.append("")
    lines.append("-" * 60)
    if real_issues == 0:
        lines.append(f"结论：无真问题。{len(exempt_items)} 类已豁免项属 Obsidian 库常态，无需处理。")
    else:
        lines.append(f"结论：{real_issues} 个真问题需处理；{len(exempt_items)} 类已豁免（非缺陷）。")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="计算 vault 健康报告")
    ap.add_argument("index", help="index.json 路径")
    ap.add_argument("-o", "--output", default=None, help="可选：输出报告文件路径")
    args = ap.parse_args()

    index = load_index(args.index)
    result = compute(index)
    # 终端用聚合版（不刷屏），导出文件用完整版（逐条可查）
    print(render(result, index, verbose=False))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(render(result, index, verbose=True) + "\n")
        print(f"\n✅ 完整报告已写入 {args.output}")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
