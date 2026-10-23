#!/usr/bin/env python3
"""scan.py — 遍历 Obsidian vault，生成 index.json 基线（严格按 index-schema 契约 v1）。

用法：python3 scan.py <vault绝对路径> [-o index.json]
只读操作：不落任何写操作，仅产出 index.json。
"""
import argparse
import datetime
import json
import os
import sys
from collections import defaultdict

# 保证脚本所在目录在 sys.path：隔离模式（-I）、-P、或从其他 cwd 导入时解释器不会自动
# 加入脚本目录，分发到用户机器后必须自带这层保障，否则 from common 直接崩。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    EXCLUDE_DIRS, classify, detect_date_stream_dirs, extract_links, is_junk_file,
    looks_like_archive, looks_like_daily, looks_like_index, looks_like_template,
    normalize_title, parse_frontmatter, read_daily_dirs, read_template_dirs,
    resolve_link_target, sha256_file,
)


def walk_vault(root, stats=None):
    """遍历 vault，排除目录与系统垃圾文件、不跟随 symlink 目录。
    yield (relpath, abspath, is_symlink)。stats 若提供，累加 excluded_junk 计数。"""
    if stats is not None:
        stats.setdefault("excluded_junk", 0)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fn in filenames:
            if is_junk_file(fn):
                if stats is not None:
                    stats["excluded_junk"] += 1
                continue  # .DS_Store / ._xxx / Thumbs.db 等不进入索引
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            yield rel, full, os.path.islink(full)


def build_name_index(files):
    """stem（去扩展名）→ [paths]，用于最短路径消歧。仅 note + attachment 参与。"""
    idx = defaultdict(list)
    for f in files:
        if f["type"] in ("note", "attachment"):
            stem = os.path.splitext(os.path.basename(f["path"]))[0]
            idx[stem].append(f["path"])
    return idx


def build_fullname_index(files):
    """完整 basename（含扩展名）→ [paths]，用于带扩展名的附件/显式 .md 匹配。"""
    idx = defaultdict(list)
    for f in files:
        if f["type"] in ("note", "attachment", "canvas"):
            idx[os.path.basename(f["path"])].append(f["path"])
    return idx


def main():
    ap = argparse.ArgumentParser(description="扫描 Obsidian vault 生成 index.json")
    ap.add_argument("vault", help="vault 绝对路径")
    ap.add_argument("-o", "--output", default="index.json", help="输出文件路径")
    args = ap.parse_args()

    vault = os.path.abspath(args.vault)
    if not os.path.isdir(vault):
        print(f"❌ vault 路径不存在：{vault}")
        sys.exit(1)

    template_dirs = read_template_dirs(vault)  # .obsidian 配置的核心模板 + Templater
    daily_dirs = read_daily_dirs(vault)        # .obsidian 配置的日记目录

    files = []
    dirs = set()
    walk_stats = {}
    for rel, full, is_symlink in walk_vault(vault, walk_stats):
        dirs.add(os.path.dirname(rel))
        ftype = classify(rel)
        entry = {
            "path": rel,
            "type": ftype,
            "size": os.path.getsize(full),
            "mtime": int(os.path.getmtime(full)),
        }
        if is_symlink:
            entry["type"] = "other"
            entry["symlink"] = True
        if ftype in ("note", "attachment", "canvas"):
            entry["content_hash"] = sha256_file(full)
        if ftype == "note":
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            fm = parse_frontmatter(text)
            entry["frontmatter"] = fm
            title_src = (fm or {}).get("title") or os.path.basename(rel)
            entry["title_normalized"] = normalize_title(title_src)
        files.append(entry)

    # ── 角色判定（第二遍）：需要「整库证据」才能判准 ──────────────
    # 为什么不在第一遍判：
    #   日记的判定不能只看单个文件名。Zettelkasten 用户用「2026-09-01 闪念1.md」
    #   这种日期前缀命名原子卡片，单看文件名像日记，实际是被链接的内容卡。
    #   第一轮 ZK 压测因此把 40 篇内容卡判成日记，孤儿被静默隐藏（召回 73.3%）。
    #   现在改为「目录级证据」：只有所在目录整体是时间流时，日期前缀才算日记。
    note_paths = [f["path"] for f in files if f["type"] == "note"]
    date_stream_dirs = detect_date_stream_dirs(note_paths)
    for f in files:
        if f["type"] != "note":
            continue
        rel, fm = f["path"], f.get("frontmatter")
        with open(os.path.join(vault, rel), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        # 角色：模板 / 归档 / 索引页 / 时间流 / 正文。
        # health.py 依据 role 分档：前四类天然无入链或含占位链接，
        # 若一律报成「孤儿 / 断链」，真实规模下噪音会淹没真问题。
        if looks_like_template(rel, fm, template_dirs, text):
            f["role"] = "template"
        elif looks_like_archive(rel, fm):
            f["role"] = "archive"
        elif looks_like_index(rel, fm):
            f["role"] = "index"
        elif looks_like_daily(rel, fm, daily_dirs, date_stream_dirs):
            f["role"] = "daily"
        else:
            f["role"] = "content"

    note_map = {f["path"]: f for f in files if f["type"] == "note"}
    attachment_set = {f["path"] for f in files if f["type"] == "attachment"}
    all_paths = {f["path"] for f in files}
    name_index = build_name_index(files)
    fullname_index = build_fullname_index(files)

    # 提取链接 + 附件反向引用
    links = []
    attachments_ref = defaultdict(list)
    for f in files:
        if f["type"] != "note":
            continue
        with open(os.path.join(vault, f["path"]), "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for lnk in extract_links(text):
            base, anchor, resolved, ambiguous = resolve_link_target(
                lnk["target"], name_index, fullname_index, note_map, all_paths)
            links.append({
                "from": f["path"],
                "from_role": f.get("role", "content"),  # template 的断链属占位符，非真断链
                "to": lnk["target"],
                "kind": lnk["kind"],
                "raw": lnk["raw"],
                "resolved": resolved,       # None = 断链
                "ambiguous": ambiguous,
            })
            if resolved in attachment_set:
                attachments_ref[resolved].append(f["path"])

    attachments = [{"path": p, "referenced_by": sorted(set(refs))}
                   for p, refs in attachments_ref.items()]

    # 重复组（content_hash 完全相同的文件）
    hash_groups = defaultdict(list)
    for f in files:
        if f.get("content_hash"):
            hash_groups[f["content_hash"]].append(f["path"])
    duplicates = [
        {"group": f"g{i}", "paths": sorted(paths), "score": 1.0}
        for i, paths in enumerate(hash_groups.values()) if len(paths) > 1
    ]

    stats = {
        "total_notes": sum(1 for f in files if f["type"] == "note"),
        "total_attachments": len(attachment_set),
        "total_canvas": sum(1 for f in files if f["type"] == "canvas"),
        "total_dirs": len(dirs),
        "total_templates": sum(1 for f in files if f.get("role") == "template"),
        "total_archives": sum(1 for f in files if f.get("role") == "archive"),
        "total_daily": sum(1 for f in files if f.get("role") == "daily"),
        "total_index": sum(1 for f in files if f.get("role") == "index"),
        "excluded_junk": walk_stats.get("excluded_junk", 0),
        "broken_links": 0,      # health.py 计算
        "orphan_notes": 0,      # health.py 计算
        "missing_frontmatter": sum(1 for f in files if f["type"] == "note" and not f.get("frontmatter")),
        "duplicate_groups": len(duplicates),
    }

    index = {
        "schema_version": "1.0",
        "generated_at": datetime.datetime.now().isoformat(),
        "vault_root": vault,
        "stats": stats,
        "files": files,
        "links": links,
        "attachments": attachments,
        "duplicates": duplicates,
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"✅ 扫描完成：{len(files)} 文件、{len(links)} 链接、"
          f"{stats['total_attachments']} 附件（{len(attachments)} 个被引用） → {args.output}")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
