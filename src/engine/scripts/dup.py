#!/usr/bin/env python3
"""dup.py — 重复候选打分（只读，只出候选不删）。

用法：python3 dup.py <index.json> [-o dup-report.md] [--max-notes 5000]
逻辑：
  1. content_hash 完全相同 → score=1.00（scan 已算进 duplicates[]，直接纳入）
  2. 正文 shingle Jaccard（char 3-gram，中文友好）→ 0~1 相似度
  3. 只比较 role=content 的笔记（模板/归档/日记之间形状相似，不构成重复）
  4. 比较范围是**全库两两**（长度预筛），不再按标题分组
  5. 按阈值分档输出（见 index-schema 契约 §四）

v1.2 修正（压测发现，非调参）：
  · 旧实现只在「title_normalized 相同」的组内比较。而 title 默认取**文件名**，
    「选题原则.md」与「选题原则-转发版.md」因此永不在同一组 → 近似重复检测
    在实际使用中几乎从不触发（等于功能没在工作）。
  · 旧实现拿**整篇原文**（含 frontmatter）算相似度，同模板建的两篇不同笔记
    会因为 frontmatter 一模一样而被抬高分数。已改为只比正文。
  · 增加「包含关系」指标：A 是 B 的删节版（Jaccard 只有 0.37）时会漏掉，
    用 |A∩B| / min(|A|,|B|) 兜住；仅在篇幅比 ≥0.5 时启用，防止摘要片段误报。
"""
import argparse
import json
import os
import re
from collections import defaultdict


def shingle(text, k=3):
    """char 级 shingle，中文友好（不依赖分词）。"""
    text = re.sub(r"\s+", "", text)
    if len(text) <= k:
        return {text} if text else set()
    return {text[i:i + k] for i in range(len(text) - k + 1)}


def drop_boilerplate(sets, df_threshold):
    """去掉「在库里反复出现」的 shingle（模板骨架、固定小标题、常用套话）。
    为什么需要：20 个项目卡都用同一个模板建，「# 背景 / 相关笔记 / 本次结论」
    这些骨架占了正文一半，3-gram 相似度会把它们全判成 0.90+ 的合并候选。
    实测 20 个项目卡产出 190 对假候选——用户看到的是满屏噪音。
    这类骨架 shingle 在库里出现次数远高于真重复（真重复只有 2 篇），用文档频率剪掉。"""
    df = {}
    for s in sets:
        for g in s:
            df[g] = df.get(g, 0) + 1
    boiler = {g for g, c in df.items() if c >= df_threshold}
    if not boiler:
        return sets
    return [s - boiler for s in sets]


def jaccard(a, b):
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def tier(score, basis="shingle"):
    """相似度 → 档位。

    AUTO（可进 .trash）**只允许 content_hash 判定**：字节完全一致才谈得上「删一份」。
    这条限制是压测补出来的真 bug：一篇短笔记被长笔记完全包含时，
    containment 指标会算出精确的 1.0，旧实现据此判成 AUTO → 用户可能删掉的是一篇
    独立笔记（它只是内容被更大的那篇覆盖了）。相似度再高也只是「像」，不是「是」。
    """
    if basis == "content_hash":
        return "AUTO"
    if score >= 0.95:
        return "PROPOSE"   # 极高相似：合提案，但必须人看
    if score >= 0.85:
        return "DRAFT"     # 合并草案
    if score >= 0.70:
        return "LIST"      # 仅列出
    return None            # 忽略


def read_text(vault, rel):
    with open(os.path.join(vault, rel), "r", encoding="utf-8", errors="replace") as f:
        return f.read()


FM_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)


def body_of(text):
    """只取正文：去掉 frontmatter。
    为什么必须去：同一模板建出来的笔记 frontmatter 完全一致（title 之外的
    tags/status 等），会把两篇内容无关的笔记抬到中高相似度。"""
    return FM_RE.sub("", text, count=1)


# 篇幅比低于此值时不启用「包含关系」指标：一页摘要被打散进长文里，
# 包含度会接近 1.0，但它们是两篇不同的东西，不该报成重复。
MIN_LEN_RATIO = 0.5
# 交集 shingle 数下限：低于它说明两篇只有零星字眼重合，分数不可信（短文本抖动大）。
# 20 个 shingle ≈ 22 个汉字的实质重合，低于此值的 0.78 分没有参考价值。
MIN_SHARED_SHINGLES = 20


def score_pair(set_a, set_b):
    """返回 (score, basis)。score = max(Jaccard, 包含度)，包含度受篇幅比约束。
    另加两条「可信度下限」：交集太小的时候，分数本身不可信（短文本 3-gram 抖动大），
    宁可不报，也不要给用户一堆 0.78 的「疑似合并」。"""
    if not set_a or not set_b:
        return 0.0, "empty"
    inter = len(set_a & set_b)
    if inter < MIN_SHARED_SHINGLES:
        return 0.0, "too-little-overlap"
    la, lb = len(set_a), len(set_b)
    ratio = min(la, lb) / max(la, lb)
    if ratio < MIN_LEN_RATIO:
        return inter / len(set_a | set_b), "shingle"
    jac = inter / len(set_a | set_b)
    cont = inter / min(la, lb)
    if cont > jac + 1e-9:
        return cont, "containment"
    return jac, "shingle"


TIER_LABEL = {
    "AUTO": "🔴 字节完全一致（可进 .trash，仍需确认）",
    "PROPOSE": "🟠 高相似 ≥0.95（合并提案，需确认）",
    "DRAFT": "🟡 中相似 0.85–0.95（合并草案，需确认）",
    "LIST": "🔵 低相似 0.70–0.85（仅列出）",
}


def main():
    ap = argparse.ArgumentParser(description="重复候选打分（只读）")
    ap.add_argument("index", help="index.json 路径")
    ap.add_argument("-o", "--output", default=None, help="可选：输出报告文件")
    ap.add_argument("--max-notes", type=int, default=5000,
                    help="最多比较多少篇（默认 5000，防止超大库卡住）")
    ap.add_argument("--json", default=None,
                    help="可选：把候选以 JSON 写出（供 plan.py 消费）。不影响终端输出")
    args = ap.parse_args()

    with open(args.index, "r", encoding="utf-8") as f:
        index = json.load(f)

    vault = index["vault_root"]
    files = index["files"]
    notes = [f for f in files if f["type"] == "note"]

    candidates = []
    seen_pairs = set()

    # 1) scan 已算的 content_hash 完全一致组 → score=1.0
    for d in index.get("duplicates", []):
        for i in range(len(d["paths"])):
            for j in range(i + 1, len(d["paths"])):
                pair = tuple(sorted([d["paths"][i], d["paths"][j]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                candidates.append({
                    "paths": list(pair), "score": 1.0, "tier": "AUTO",
                    "basis": "content_hash",
                })

    # 2) 全库两两比较（长度预筛 + 只比正文）
    #    旧实现按标题分组，而标题默认是文件名 → 「A.md」与「A-转发版.md」永不比较，
    #    近似重复检测实际上等于没实现。这里改为全量，用长度窗口剪枝控制成本。
    pool = []
    for f in notes:
        if f.get("role", "content") != "content":
            continue  # 模板/归档/日记形状相似，不构成"重复笔记"
        try:
            txt = body_of(read_text(vault, f["path"]))
        except OSError:
            continue
        s = shingle(txt)
        if not s:
            continue
        pool.append((len(txt), f["path"], s))

    if len(pool) > args.max_notes:
        print(f"⚠️ 待比较笔记 {len(pool)} 篇，超过上限 {args.max_notes}。"
              f"为避免长时间占用，仅比较前 {args.max_notes} 篇（按篇幅升序）。"
              f"如需全量，用 --max-notes 调高。\n")
        pool = sorted(pool)[:args.max_notes]
    else:
        pool.sort()

    # 去掉模板骨架/套话 shingle：库内出现次数 ≥ max(3, 5% 篇数) 的 shingle 视为骨架。
    # 真重复只有 2 篇，永远不会被当成骨架剪掉。
    # 只在 ≥50 篇的库启用：小库里骨架噪音撑不起规模，剪枝反而可能误伤。
    if len(pool) >= 50:
        df_threshold = max(3, int(len(pool) * 0.05))
        cleaned = drop_boilerplate([p[2] for p in pool], df_threshold)
        pool = [(ln, path, s) for (ln, path, _), s in zip(pool, cleaned) if s]

    # 长度窗口：篇幅比 <0.5 的两篇不可能是同一篇的两个版本，直接跳过
    for i in range(len(pool)):
        li, pa, sa = pool[i]
        for j in range(i + 1, len(pool)):
            lj, pb, sb = pool[j]
            if li > 0 and lj / li > 2.0:
                break  # 已按长度排序，后面只会更长
            pair = tuple(sorted([pa, pb]))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            score, basis = score_pair(sa, sb)
            t = tier(score, basis)
            if t:
                candidates.append({
                    "paths": list(pair), "score": round(score, 4),
                    "tier": t, "basis": basis,
                })

    order = {"AUTO": 0, "PROPOSE": 1, "DRAFT": 2, "LIST": 3}
    candidates.sort(key=lambda c: (order[c["tier"]], -c["score"]))

    lines = []
    lines.append("=" * 44)
    lines.append("重复候选报告（只读，不删除任何文件）")
    lines.append("=" * 44)
    if not candidates:
        lines.append("\n未发现重复候选。")
    else:
        cur = None
        for c in candidates:
            if c["tier"] != cur:
                cur = c["tier"]
                lines.append(f"\n{TIER_LABEL[cur]}：")
            # 包含度算出 1.00 时，语义是「短的那篇整个被长的包住」，不是「两篇一样」。
            # 不写这一行，用户会困惑：分数 1.00 为什么没进「字节完全一致」档？
            mark = ""
            if c["basis"] == "containment" and c["score"] >= 0.999:
                mark = "   ← 一篇被另一篇完整包含，不是一模一样"
            lines.append(f"   - {c['score']:.4f}  {c['paths'][0]}  ⇄  {c['paths'][1]}{mark}")
    report = "\n".join(lines)
    print(report)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report + "\n")
        print(f"\n✅ 报告已写入 {args.output}")

    # --json：给 plan.py 之类的下游消费者用。纯加法，不影响上面任何既有行为。
    if args.json:
        counts = {k: 0 for k in ("AUTO", "PROPOSE", "DRAFT", "LIST")}
        for c in candidates:
            counts[c["tier"]] = counts.get(c["tier"], 0) + 1
        payload = {
            "compared_notes": len(pool),
            "max_notes_capped": len(pool) >= args.max_notes,
            "counts": counts,
            "candidates": candidates,
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"✅ 结构化结果已写入 {args.json}")


if __name__ == "__main__":
    main()
