#!/usr/bin/env python3
"""verify.py — 治理前后对比（只读），输出结构级差异表。

用法：python3 verify.py <index-before.json> <index-after.json> [-o diff.md]
对比维度：
  1. 文件增减（path 集合差）
  2. 移动（在增删之间按 content_hash 多重集配对，同 basename 优先；
     完全重复文件存在时不会把「删一份」误配成「移动」）
  3. 断链增减（links[].resolved is None，用 from+raw 作稳定标识）
  4. 元数据覆盖变化（stats.missing_frontmatter）
"""
import argparse
import json
import os
import sys
from collections import defaultdict


def load(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def diff(before, after):
    bfiles = {f["path"]: f for f in before["files"]}
    afiles = {f["path"]: f for f in after["files"]}
    bpaths = set(bfiles)
    apaths = set(afiles)

    # 增删：path 集合差
    removed = sorted(bpaths - apaths)
    added = sorted(apaths - bpaths)

    # 移动配对：在「删除」与「新增」之间按 content_hash 配对。
    # 必须按多重集处理：库里存在完全重复文件（同 hash 多份）时，
    # 简单的 hash→path 映射会把「删掉一份重复」误配成「移动到另一份」。
    # 规则：同 hash 才配；同 basename 优先；配不上的保持增/删语义。
    rem_by_hash = defaultdict(list)
    for p in removed:
        rem_by_hash[bfiles[p].get("content_hash")].append(p)
    add_by_hash = defaultdict(list)
    for p in added:
        add_by_hash[afiles[p].get("content_hash")].append(p)

    moves = []
    for h, rem_list in rem_by_hash.items():
        add_pool = add_by_hash.get(h)
        if not add_pool:
            continue
        add_pool = sorted(add_pool)
        used = set()
        pairs = []
        # 第一遍：basename 相同的优先配（消同 hash 多份的歧义）
        for r in sorted(rem_list):
            rn = os.path.basename(r)
            match = next((a for a in add_pool
                          if a not in used and os.path.basename(a) == rn), None)
            if match:
                pairs.append((r, match))
                used.add(match)
        # 第二遍：剩下的按排序顺序配
        for r in sorted(rem_list):
            if any(p[0] == r for p in pairs):
                continue
            match = next((a for a in add_pool if a not in used), None)
            if match:
                pairs.append((r, match))
                used.add(match)
        moves.extend(pairs)

    moved_src = {s for s, _ in moves}
    moved_dst = {d for _, d in moves}
    added = sorted(p for p in added if p not in moved_dst)
    removed = sorted(p for p in removed if p not in moved_src)

    # 断链（from+raw 作稳定标识，跨扫描可比）
    def key(l):
        return (l["from"], l["raw"])

    bbroken = {key(l) for l in before["links"] if l["resolved"] is None}
    abroken = {key(l) for l in after["links"] if l["resolved"] is None}
    broken_new = sorted(abroken - bbroken)
    broken_fixed = sorted(bbroken - abroken)

    return {
        "added": added,
        "removed": removed,
        "moves": sorted(moves),
        "broken_before": len(bbroken),
        "broken_after": len(abroken),
        "broken_delta": len(abroken) - len(bbroken),
        "broken_new": broken_new,
        "broken_fixed": broken_fixed,
        "missing_fm_before": before["stats"].get("missing_frontmatter", 0),
        "missing_fm_after": after["stats"].get("missing_frontmatter", 0),
    }


def render(r):
    lines = []
    lines.append("=" * 44)
    lines.append("治理前后差异表（verify）")
    lines.append("=" * 44)
    lines.append(f"文件：+{len(r['added'])} 新增 / -{len(r['removed'])} 删除 / {len(r['moves'])} 移动")
    for a in r["added"]:
        lines.append(f"   ➕ {a}")
    for d in r["removed"]:
        lines.append(f"   ➖ {d}")
    for src, dst in r["moves"]:
        lines.append(f"   🔀 {src} → {dst}")

    lines.append(f"\n断链：{r['broken_before']} → {r['broken_after']}（Δ {r['broken_delta']:+d}）")
    for k in r["broken_new"]:
        lines.append(f"   ➕新断链 {k[0]} → {k[1]}")
    for k in r["broken_fixed"]:
        lines.append(f"   ✅已修复 {k[0]} → {k[1]}")

    lines.append(f"\n缺 frontmatter：{r['missing_fm_before']} → {r['missing_fm_after']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="治理前后结构差异对比")
    ap.add_argument("before", help="治理前 index.json")
    ap.add_argument("after", help="治理后 index.json")
    ap.add_argument("-o", "--output", default=None)
    args = ap.parse_args()

    r = diff(load(args.before), load(args.after))
    report = render(r)
    print(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n✅ 报告已写入 {args.output}")


if __name__ == "__main__":
    try:  # Windows 控制台默认 cp1252/GBK，打印中文/✅❌会崩；强制 UTF-8
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
