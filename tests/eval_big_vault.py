#!/usr/bin/env python3
"""eval_big_vault.py — 用 ground truth 量化引擎在真实规模库上的表现。

指标：
  召回率（Recall）  = 抓到的问题 / 注入的问题     → 衡量「该报的有没有漏」
  精确率（Precision） = 报对的问题 / 报出总数      → 衡量「有没有瞎报」

用法：
  python3 eval_big_vault.py <gt.json> <index.json>
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src", "engine", "scripts"))
import health as health_mod  # noqa: E402


def pct(a, b):
    return f"{a / b * 100:.1f}%" if b else "—"


def main():
    gt_path, idx_path = sys.argv[1], sys.argv[2]
    with open(gt_path, encoding="utf-8") as f:
        gt = json.load(f)
    with open(idx_path, encoding="utf-8") as f:
        index = json.load(f)

    t0 = time.time()
    res = health_mod.compute(index)
    elapsed = time.time() - t0

    print("=" * 68)
    print("引擎压测评估（ground truth 对比）")
    print("=" * 68)
    inj = gt["injected"]
    s = index["stats"]
    print(f"库规模：笔记 {s['total_notes']} · 附件 {s['total_attachments']} · "
          f"Canvas {s['total_canvas']} · 目录 {s['total_dirs']}")
    print(f"说明：模板 {s.get('total_templates', 0)} · 归档 {s.get('total_archives', 0)} · "
          f"排除垃圾 {s.get('excluded_junk', 0)}")
    print(f"计算耗时：{elapsed * 1000:.0f} ms")
    print()

    rows = []

    # ── 1. 真断链 ──
    gt_broken = {(b["from"], b["target"]) for b in gt["expected_broken"]}
    got_broken = {(l["from"], l["to"]) for l in res["broken_links"]}
    hit = len(gt_broken & got_broken)
    rows.append(("真断链", len(gt_broken), len(got_broken), hit,
                 len(got_broken - gt_broken)))

    # ── 2. 模板占位（应归豁免，不进断链）──
    # 误报列 = 模板占位被误算成「真断链」的数量，正确实现应为 0。
    gt_tpl = len(gt["expected_template_placeholders"])
    got_tpl = len(res["broken_placeholder"])
    tpl_leaked = sum(1 for l in res["broken_links"] if l.get("from_role") == "template")
    rows.append(("模板占位（豁免档）", gt_tpl, got_tpl, min(gt_tpl, got_tpl), tpl_leaked))

    # ── 3. 孤儿 ──
    gt_orph = set(gt["expected_orphans"])
    got_orph = set(res["orphan_notes"])
    hit_o = len(gt_orph & got_orph)
    rows.append(("真孤儿", len(gt_orph), len(got_orph), hit_o,
                 len(got_orph - gt_orph)))

    # ── 4. 重复（同内容不同名）──
    gt_dup = {tuple(sorted(g)) for g in gt["expected_duplicates"]}
    got_dup = {tuple(sorted(g)) for g in res["content_duplicates"]}
    rows.append(("内容完全重复组", len(gt_dup), len(got_dup),
                 len(gt_dup & got_dup), len(got_dup - gt_dup)))

    # ── 5. 同名不同内容（应进分类档，不应进重复档）──
    rows.append(("同名不同内容（分类档）",
                 inj["same_name_diff_groups"], len(res["same_name_diff"]),
                 min(inj["same_name_diff_groups"], len(res["same_name_diff"])),
                 len(res["duplicate_names"])))

    # ── 6. 缺 frontmatter（正文）──
    gt_fm = set(gt["expected_missing_fm"])
    got_fm = set(res["missing_frontmatter"])
    rows.append(("正文缺 frontmatter", len(gt_fm), len(got_fm),
                 len(gt_fm & got_fm), len(got_fm - gt_fm)))

    # ── 7. 归档缺 fm（应豁免）──
    rows.append(("归档缺元数据（豁免档）", inj["archive_no_fm"],
                 len(res["missing_fm_archive"]),
                 min(inj["archive_no_fm"], len(res["missing_fm_archive"])), 0))

    # ── 8. 垃圾文件（应排除）──
    rows.append(("垃圾文件（应排除）", inj["junk_files"],
                 s.get("excluded_junk", 0),
                 min(inj["junk_files"], s.get("excluded_junk", 0)), 0))

    print(f"{'检测项':<24}{'注入':>6}{'报出':>6}{'命中':>6}{'误报':>6}{'召回':>9}{'精确':>9}")
    print("-" * 68)
    for name, n_inj, n_got, n_hit, n_fp in rows:
        prec = pct(n_hit, n_got)
        print(f"{name:<24}{n_inj:>6}{n_got:>6}{n_hit:>6}{n_fp:>6}"
              f"{pct(n_hit, n_inj):>9}{prec:>9}")

    print("-" * 68)

    # ── 误报明细：孤儿里不属于 ground truth 的是谁 ──
    fp_orph = sorted(got_orph - gt_orph)
    if fp_orph:
        print(f"\n⚠️  孤儿误报 {len(fp_orph)} 篇，样本前 12：")
        for p in fp_orph[:12]:
            print(f"   - {p}")
        kinds = {}
        for p in fp_orph:
            key = p.split("/")[0] if "/" in p else "(根目录)"
            kinds[key] = kinds.get(key, 0) + 1
        print(f"   按目录分布：{kinds}")

    fp_broken = sorted(got_broken - gt_broken)
    if fp_broken:
        print(f"\n⚠️  断链误报 {len(fp_broken)} 条：")
        for f_, t_ in fp_broken[:12]:
            print(f"   - {f_} → [[{t_}]]")

    # ── 9. role 判定准确性（仅非 PARA 结构库提供 expected_content / expected_daily）──
    if gt.get("expected_content") or gt.get("expected_daily"):
        role_of = {f["path"]: f.get("role", "content") for f in index["files"]
                   if f["type"] == "note"}
        exp_content = set(gt.get("expected_content") or [])
        exp_daily = set(gt.get("expected_daily") or [])

        # 内容笔记被判成 daily/index/template/archive → 孤儿会被静默豁免（漏报风险）
        hidden = sorted(p for p in exp_content
                        if role_of.get(p) in ("daily", "template", "archive"))
        # 日记被判成 content → 全部报孤儿（噪音风险）
        noisy = sorted(p for p in exp_daily if role_of.get(p) == "content")

        print("\n" + "=" * 68)
        print(f"role 分档准确性（结构：{gt.get('structure', '?')}）")
        print("=" * 68)
        print(f"  内容笔记 {len(exp_content)} 篇 → 被误判为非内容（孤儿将被隐藏）：{len(hidden)}")
        for p in hidden[:8]:
            print(f"     · {p}  → role={role_of.get(p)}")
        if len(hidden) > 8:
            print(f"     … 另 {len(hidden) - 8} 篇")
        print(f"  日记 {len(exp_daily)} 篇 → 被误判为内容（将报孤儿噪音）：{len(noisy)}")
        for p in noisy[:8]:
            print(f"     · {p}  → role={role_of.get(p)}")
        if len(noisy) > 8:
            print(f"     … 另 {len(noisy) - 8} 篇")
        if not hidden and not noisy:
            print("  ✅ role 分档在本结构下无误判")

        # 孤儿漏报 / 噪音拆解
        missed = sorted(gt_orph - got_orph)
        if missed:
            print(f"\n  ❗ 孤儿漏报 {len(missed)} 篇（未被报出）：")
            for p in missed[:10]:
                print(f"     · {p}  → role={role_of.get(p)}")

    print()


if __name__ == "__main__":
    main()
