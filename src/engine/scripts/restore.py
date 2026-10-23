#!/usr/bin/env python3
"""restore.py — 回滚 move 操作（纯标准库，零依赖）。

用法：python3 restore.py <vault> <change-log.csv> <affected.zip> [--index-before index.json]

流程：
  1. 逆序撤销 change-log（move 移回 / create 删除新建 / delete 从 .trash 还原）
  2. 解压 affected.zip 覆盖还原引用者与目标原文件
  3. 若有 --index-before，重扫 scan + verify 对比，差异归零才算还原成功
"""
import argparse
import csv
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

# 保证脚本目录在 sys.path（应对 -I/-P/非标准调用，分发后必需）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify import diff, load, render


def main():
    ap = argparse.ArgumentParser(description="回滚 move 操作")
    ap.add_argument("vault", help="vault 绝对路径")
    ap.add_argument("log", help="change-log.csv 路径")
    ap.add_argument("backup", help="affected.zip 路径")
    ap.add_argument("--index-before", default=None, help="治理前 index.json（用于 verify 对比）")
    args = ap.parse_args()

    vault = os.path.abspath(args.vault)

    # 1. 读 change-log，逆序撤销
    with open(args.log, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.reverse()

    for r in rows:
        action = r["action"]
        src = r.get("src", "")
        dst = r.get("dst", "")
        if action == "move":
            dst_full = os.path.join(vault, dst)
            src_full = os.path.join(vault, src)
            if os.path.exists(dst_full):
                os.makedirs(os.path.dirname(src_full), exist_ok=True)
                shutil.move(dst_full, src_full)
        elif action == "create":
            dst_full = os.path.join(vault, dst)
            if os.path.exists(dst_full):
                os.remove(dst_full)
        elif action == "delete":
            dst_full = os.path.join(vault, r.get("dst", ""))
            src_full = os.path.join(vault, src)
            if os.path.exists(dst_full):
                os.makedirs(os.path.dirname(src_full), exist_ok=True)
                shutil.move(dst_full, src_full)

    # 2. 解压 affected.zip 覆盖（还原引用者 + 目标原文件）
    if os.path.exists(args.backup):
        with zipfile.ZipFile(args.backup, "r") as z:
            z.extractall(vault)

    print("✅ 回滚执行完成。")

    # 3. verify 对比（可选）
    if args.index_before:
        # 不能写死 /tmp：Windows 没有该目录（跨平台 CI 实测暴露的 bug）
        after_path = os.path.join(tempfile.gettempdir(), "index-after-restore.json")
        subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "scan.py"),
             vault, "-o", after_path],
            check=True,
        )
        r = diff(load(args.index_before), load(after_path))
        print(render(r))
        clean = (r["broken_delta"] == 0 and not r["added"]
                 and not r["removed"] and not r["moves"])
        if clean:
            print("✅ 差异归零，还原成功。")
        else:
            print("⚠️ 仍有差异，需人工检查。")
    else:
        print("提示：提供 --index-before 可自动 verify 对比还原是否干净。")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
