#!/usr/bin/env python3
"""vault.py — 自动定位本机 Obsidian 库（纯标准库，零依赖）。

用法：
  python3 vault.py                # 列出所有库（路径 / open 状态 / md 篇数）
  python3 vault.py --json         # 输出 JSON（供上层解析）
  python3 vault.py --auto         # 自动选库：仅 1 个库或唯 1 个 open 库时直接输出路径
                                  # 多库且无法判定 → 退出码 2（由 Agent 询问用户）

原理：读 Obsidian 官方配置 obsidian.json（Obsidian 启动时会写 open 标记），
跨平台路径：
  macOS   ~/Library/Application Support/obsidian/obsidian.json
  Windows %APPDATA%/obsidian/obsidian.json
  Linux   ~/.config/obsidian/obsidian.json

只读操作：仅读取配置与目录计数，不读笔记内容、不改任何文件。
"""
import argparse
import json
import os
import sys

# 保证脚本目录在 sys.path（应对 -I/-P/非标准调用，分发后必需）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import EXCLUDE_DIRS


def config_candidates():
    """返回各平台 obsidian.json 候选路径（按优先级）。"""
    home = os.path.expanduser("~")
    cands = []
    if sys.platform == "darwin":
        cands.append(os.path.join(home, "Library", "Application Support", "obsidian", "obsidian.json"))
    elif os.name == "nt":
        appdata = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        cands.append(os.path.join(appdata, "obsidian", "obsidian.json"))
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        cands.append(os.path.join(xdg, "obsidian", "obsidian.json"))
    # 兜底：其他平台也试一遍（用户可能用非标准安装）
    cands.append(os.path.join(home, "Library", "Application Support", "obsidian", "obsidian.json"))
    cands.append(os.path.join(home, ".config", "obsidian", "obsidian.json"))
    return cands


def find_config():
    for p in config_candidates():
        if os.path.isfile(p):
            return p
    return None


def count_notes(root):
    """轻量统计 md 篇数（不读内容，仅遍历计数）。"""
    n = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            n += sum(1 for fn in filenames if fn.lower().endswith(".md"))
    except OSError:
        return -1
    return n


def list_vaults(with_counts=True):
    cfg = find_config()
    if not cfg:
        return [], None
    try:
        with open(cfg, "r", encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], cfg

    vaults = []
    for vid, meta in (data.get("vaults") or {}).items():
        path = meta.get("path")
        if not path:
            continue
        exists = os.path.isdir(path)
        vaults.append({
            "id": vid,
            "path": path,
            "open": bool(meta.get("open")),
            "exists": exists,
            "notes": count_notes(path) if (with_counts and exists) else -1,
        })
    vaults.sort(key=lambda v: (not v["open"], -v["notes"]))
    return vaults, cfg


def main():
    ap = argparse.ArgumentParser(description="定位本机 Obsidian 库")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--auto", action="store_true", help="自动选库（多库无法判定则退出码 2）")
    ap.add_argument("--no-count", action="store_true", help="跳过 md 篇数统计（更快）")
    args = ap.parse_args()

    vaults, cfg = list_vaults(with_counts=not args.no_count)

    if not cfg:
        print("❌ 未找到 Obsidian 配置（obsidian.json）。可能未安装 Obsidian，或从未打开过库。")
        print("   请让用户直接提供 vault 路径。")
        sys.exit(1)

    if not vaults:
        print(f"❌ 配置存在但未登记任何库：{cfg}")
        sys.exit(1)

    alive = [v for v in vaults if v["exists"]]

    if args.auto:
        if len(alive) == 1:
            print(alive[0]["path"])
            return
        opens = [v for v in alive if v["open"]]
        if len(opens) == 1:
            print(opens[0]["path"])
            return
        print(f"❌ 有 {len(alive)} 个库、{len(opens)} 个 open 标记，无法自动判定。请让用户选择：")
        for v in alive:
            print(f"   - {v['path']}（{v['notes']} 篇）")
        sys.exit(2)

    if args.json:
        print(json.dumps({"config": cfg, "vaults": vaults}, ensure_ascii=False, indent=2))
        return

    print(f"配置文件：{cfg}")
    print(f"共 {len(vaults)} 个库（存在 {len(alive)} 个）：")
    for i, v in enumerate(vaults, 1):
        flag = "• open" if v["open"] else "      "
        miss = "" if v["exists"] else "  ⚠️ 路径不存在"
        count = f"{v['notes']} 篇" if v["notes"] >= 0 else "—"
        print(f"  {i}. [{flag}] {v['path']}  ({count}){miss}")
    print(f"\n库根路径可直接传给 scan.py：python3 scan.py \"<路径>\" -o index.json")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
