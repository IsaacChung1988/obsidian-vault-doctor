#!/usr/bin/env python3
"""move.py — 移动/重命名文件 + 重写全库链接（纯标准库，零依赖）。

用法：
  单次移动：python3 move.py <index.json> --src Note.md --dst Archive/Note.md
  批量移动：python3 move.py <index.json> --plan plan.json   # plan.json: {"operations":[{"src","dst"}]}

前置：先跑 scan.py 生成 index.json（move 依赖 index 的 files/links/attachments 做反向定位）。

产物（回滚三件套之二）：
  - affected.zip   受影响文件改前备份（源 + 引用者 + 目标原文件 + canvas）
  - change-log.csv 动作日志（action,src,dst），restore.py 逆序撤销用

关键规则：
  - 代码块（``` 围栏）与行内代码（`）内的 [[...]] 视为示例文本，一律不动
  - 附件与 note 同规则重写；附件定位走 scan 索引，不做全局文件名猜测
  - Canvas .canvas 是 JSON，节点 file 字段专用重写；无法确定的列人工复核，不静默断链
  - 执行顺序：先重写引用者 + canvas（旧路径可读），再物理移动 —— 目录级移动时引用者本身
    也在移动范围内，若先移动则旧路径失效、内部互链无法改写
  - 目标在 .trash/ 下记 action=delete（去重/清理语义），restore 从 .trash 还原
"""
import argparse
import csv
import json
import os
import shutil
import sys
import zipfile

# 保证脚本目录在 sys.path（应对 -I/-P/非标准调用，分发后必需）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import MDLINK_RE, WIKILINK_RE, resolve_link_target
from scan import build_fullname_index, build_name_index


def build_path_map(index, operations):
    """operations: [{src, dst}] → {old_rel_path: new_rel_path}，目录移动展开为逐文件映射。"""
    existing = {f["path"] for f in index["files"]}
    path_map = {}
    for op in operations:
        src = op["src"].strip().rstrip("/")
        dst = op["dst"].strip().rstrip("/")
        if not src or not dst:
            continue
        if src in existing:
            path_map[src] = dst
        else:
            # 目录：展开所有以 src/ 开头的文件
            prefix = src + "/"
            for p in existing:
                if p.startswith(prefix):
                    path_map[p] = dst + "/" + p[len(prefix):]
    return path_map


def link_target_for(new_path):
    """新路径 → 链接 target：note 去 .md，附件保留扩展名。"""
    if new_path.endswith(".md"):
        return new_path[:-3]
    return new_path


def rewrite_segment(seg, path_map, name_index, fullname_index, note_map, all_paths):
    """重写一段纯文本（已排除代码块/行内代码）里指向被移文件的链接。"""

    def repl_wiki(m):
        embed = m.group(1)
        target = m.group(2)
        alias = m.group(3)
        base, anchor, resolved, _amb = resolve_link_target(
            target, name_index, fullname_index, note_map, all_paths)
        if resolved in path_map:
            nt = link_target_for(path_map[resolved])
            anchor_part = ("#" + anchor) if anchor else ""
            alias_part = ("|" + alias) if alias else ""
            return f"{embed}[[{nt}{anchor_part}{alias_part}]]"
        return m.group(0)

    def repl_md(m):
        embed = m.group(1)
        text_part = m.group(2)
        target = m.group(3)
        base, anchor, resolved, _amb = resolve_link_target(
            target, name_index, fullname_index, note_map, all_paths)
        if resolved in path_map:
            nt = link_target_for(path_map[resolved])
            anchor_part = ("#" + anchor) if anchor else ""
            return f"{embed}[{text_part}]({nt}{anchor_part})"
        return m.group(0)

    seg = WIKILINK_RE.sub(repl_wiki, seg)
    seg = MDLINK_RE.sub(repl_md, seg)
    return seg


def rewrite_links_in_text(text, path_map, name_index, fullname_index, note_map, all_paths):
    """重写代码块外（fenced + 行内代码）的链接，返回新文本。"""
    fenced = text.split("```")
    for fi in range(0, len(fenced), 2):
        inline = fenced[fi].split("`")
        for ii in range(0, len(inline), 2):
            inline[ii] = rewrite_segment(inline[ii], path_map, name_index, fullname_index, note_map, all_paths)
        fenced[fi] = "`".join(inline)
    return "```".join(fenced)


def rewrite_canvas(vault, canvas_path, path_map):
    """Canvas .canvas 是 JSON，节点 file 字段存相对路径，专用重写。
    返回 (changed, unresolved)。无法确定的列 unresolved 人工复核，不静默断链。"""
    full = os.path.join(vault, canvas_path)
    with open(full, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    changed = False
    unresolved = []
    moved_basenames = {os.path.splitext(os.path.basename(k))[0] for k in path_map}
    for node in data.get("nodes", []):
        f = node.get("file")
        if not f:
            continue
        if f in path_map:
            node["file"] = path_map[f]
            changed = True
        elif os.path.splitext(os.path.basename(f))[0] in moved_basenames:
            unresolved.append(f)
    if changed:
        with open(full, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return changed, unresolved


def backup_to_zip(vault, paths, zip_path):
    """把受影响文件的改前内容备份到 zip（保留相对路径）。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(paths):
            full = os.path.join(vault, p)
            if os.path.isfile(full):
                z.write(full, p)


def main():
    ap = argparse.ArgumentParser(description="移动文件并重写全库链接")
    ap.add_argument("index", help="index.json 路径（先 scan.py 生成）")
    ap.add_argument("--src", help="源相对路径（单次移动）")
    ap.add_argument("--dst", help="目标相对路径（单次移动）")
    ap.add_argument("--plan", help="批量移动 plan.json")
    ap.add_argument("--backup", default="affected.zip", help="备份 zip 输出路径")
    ap.add_argument("--log", default="change-log.csv", help="变更日志输出路径")
    args = ap.parse_args()

    with open(args.index, "r", encoding="utf-8") as f:
        index = json.load(f)

    vault = index["vault_root"]
    files = index["files"]

    if args.plan:
        with open(args.plan, "r", encoding="utf-8") as f:
            plan = json.load(f)
        operations = plan.get("operations", [])
    elif args.src and args.dst:
        operations = [{"src": args.src, "dst": args.dst}]
    else:
        print("❌ 需提供 --plan 或 --src/--dst")
        sys.exit(1)

    path_map = build_path_map(index, operations)
    if not path_map:
        print("❌ 未解析出任何移动映射（src 路径不存在？）")
        sys.exit(1)

    name_index = build_name_index(files)
    fullname_index = build_fullname_index(files)
    note_map = {f["path"]: f for f in files if f["type"] == "note"}
    all_paths = {f["path"] for f in files}

    # 1. 引用者 = links 里 resolved 指向被移文件
    referencers = {l["from"] for l in index["links"] if l["resolved"] in path_map}
    # 2. 目标位置已存在的文件（将被覆盖）
    existing = {f["path"] for f in files}
    targets_existing = [t for t in path_map.values() if t in existing]
    # 3. 受影响文件 = 源 + 引用者 + 目标原文件 + canvas（rewrite_canvas 会改其 file 字段，须备份）
    canvas_files = {f["path"] for f in files if f["type"] == "canvas"}
    affected = set(path_map.keys()) | referencers | set(targets_existing) | canvas_files

    print(f"移动 {len(path_map)} 个文件，重写 {len(referencers)} 个引用者，备份 {len(affected)} 个文件。")

    # 备份改前内容
    backup_to_zip(vault, affected, args.backup)

    # 先重写引用者（物理移动前，旧路径仍可读；目录级移动时引用者本身也被移动）
    for rel in sorted(referencers):
        full = os.path.join(vault, rel)
        if not os.path.isfile(full):
            continue
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        new_text = rewrite_links_in_text(text, path_map, name_index, fullname_index, note_map, all_paths)
        if new_text != text:
            with open(full, "w", encoding="utf-8") as f:
                f.write(new_text)

    # 再重写 canvas（物理移动前）
    unresolved = []
    for f in files:
        if f["type"] == "canvas":
            changed, un = rewrite_canvas(vault, f["path"], path_map)
            if un:
                unresolved.extend((f["path"], u) for u in un)

    # 最后物理移动
    for src, dst in path_map.items():
        src_full = os.path.join(vault, src)
        dst_full = os.path.join(vault, dst)
        os.makedirs(os.path.dirname(dst_full), exist_ok=True)
        if not os.path.exists(src_full):
            print(f"⚠️ 跳过（源不存在）：{src}")
            continue
        shutil.move(src_full, dst_full)

    # 写 change-log.csv（目标在 .trash/ 下记 delete）
    with open(args.log, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["action", "src", "dst"])
        for src, dst in path_map.items():
            action = "delete" if dst.startswith(".trash/") else "move"
            w.writerow([action, src, dst])

    print(f"✅ 完成：备份 {args.backup}，日志 {args.log}")
    if unresolved:
        print("⚠️ 以下 canvas 引用无法确定，需人工复核：")
        for c, u in unresolved:
            print(f"   - {c} → {u}")


if __name__ == "__main__":
    main()
