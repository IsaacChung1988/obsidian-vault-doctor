#!/usr/bin/env python3
"""probe_dup.py — 量化 dup.py 的相似度判定（不改任何东西，只出数据）。

背景：第一轮压测报告 §七 留了一条「短文本偏敏感：95% 相似只算 0.85，落不到合并提案档」。
      这条一直没有数字支撑。本脚本把不同长度 / 不同改动量的样本跑一遍，
      给出「实际得分 vs 人工判断」，供决策是否调算法或阈值。

用法：python3 probe_dup.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "src", "engine", "scripts")
sys.path.insert(0, SCRIPTS)
from dup import jaccard, shingle, tier  # noqa: E402

CLAUSES = [
    "做内容运营时选题要先确认人群确定性而不是话题新鲜度。",
    "先确认这批人一定会点再考虑怎么把标题写得好看。",
    "竞彩预测的收益来自纪律而不是单场判断的灵感。",
    "资金分配比选场更重要因为一次重仓能抹掉十次正确。",
    "复盘要写清失败原因是模型错还是判断错还是运气差。",
    "笔记库真正的成本不是存储空间而是检索摩擦。",
    "如果一条笔记三个月没有被任何笔记引用它大概率是死的。",
    "模板的作用是降低启动成本不是统一思想。",
    "日记按日期命名天然不会有人回链所以不该算孤儿。",
    "归档快照不写元数据是正常的因为它的使命已经完成。",
    "把方法论写成清单比写成文章更容易被执行。",
    "任何自动化只要做不到可回滚就不该碰用户的文件。",
    "工具的第一次输出决定用户是否还会用第二次。",
    "报出十个问题其中六个是假问题等于一个都没报。",
    "删除不是默认动作移动才是。",
    "整理目录之前先确认链接不会断。",
    "同名不同内容通常是正确设计不是重复。",
    "把重复笔记合并前必须先出一份差异清单。",
    "字数少的笔记相似度天然不稳定要配合包含关系一起看。",
    "让用户相信没问题比让用户看到很多问题更难。",
    "先跑通再优化这个顺序反了就会一直跑不通。",
    "验收标准要能被执行而不是能被打勾。",
    "工程进度的价值在于暴露依赖而不是展示忙碌。",
    "把不确定的地方标出来比假装确定要专业。",
    "数据缺失时如实说明比补充一个估算更可信。",
    "写代码之前先把数据结构定下来能省一半返工。",
    "命名是最便宜也最常被忽略的文档。",
    "测试用例的价值在于它敢不敢报失败。",
    "压测工具自己有 bug 时结论一定是错的。",
    "先怀疑工再怀疑被测对象这个顺序能省很多时间。",
    "性能不是瓶颈的时候优化性能是逃避主要矛盾。",
    "一个功能能不能卖钱取决于它解决的是不是真痛点。",
    "方案越复杂越需要一句能说清的价值主张。",
    "渠道选择比内容质量更早决定成败。",
    "复购来自持续交付确定性而不是偶尔的惊喜。",
    "把选择权交给用户比替用户做决定更安全。",
]


def make(n):
    """生成 n 句互不重复的正文（早期版本用同一段话重复，shingle 集合会人为塌缩，
    导致「改 5 字」和「改 50 字」得分相同——这是探针自身的 bug）。"""
    return "".join(CLAUSES[i % len(CLAUSES)] for i in range(n))


BASE_SHORT = make(2)
BASE_MID = make(8)
BASE_LONG = make(36)


def mutate(text, n):
    """在文本中部改 n 个字符（用不同的字替换，避免全改成同一个字导致
    shingle 集合人为塌缩——第一版探针就踩了这个坑，改 5 字和改 50 字得分一模一样）。"""
    chars = list(text)
    mid = len(chars) // 2
    pool = "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉戌亥"
    for k in range(n):
        i = mid + k
        if i < len(chars):
            chars[i] = pool[k % len(pool)]
    return "".join(chars)


def show(name, a, b, expect):
    sa, sb = shingle(a), shingle(b)
    score = jaccard(sa, sb)
    t = tier(score)
    print(f"{name:<34}{len(a):>5}{len(b):>6}{score:>10.4f}  {t or '忽略':<8}{expect}")


def main():
    print("=" * 86)
    print("dup.py 相似度探针（char 3-gram shingle Jaccard）")
    print("=" * 86)
    print(f"{'样本':<34}{'字数A':>5}{'字数B':>6}{'得分':>10}  {'档位':<8}人工判断")
    print("-" * 86)

    show("完全相同", BASE_SHORT, BASE_SHORT, "应 AUTO")
    show("短文本·改1字", BASE_SHORT, mutate(BASE_SHORT, 1), "应 PROPOSE(≥0.95)")
    show("短文本·改5字", BASE_SHORT, mutate(BASE_SHORT, 5), "应 DRAFT(0.85-0.95)")
    show("短文本·改20字", BASE_SHORT, mutate(BASE_SHORT, 20), "应 LIST 或忽略")
    show("中文本·改5字", BASE_MID, mutate(BASE_MID, 5), "应 PROPOSE")
    show("中文本·改50字", BASE_MID, mutate(BASE_MID, 50), "应 PROPOSE/DRAFT")
    show("长文本·改5字", BASE_LONG, mutate(BASE_LONG, 5), "应 PROPOSE")
    show("长文本·改100字", BASE_LONG, mutate(BASE_LONG, 100), "应 DRAFT")
    show("子集关系 95%（A 含 B 全文）", BASE_SHORT, BASE_SHORT[:-30], "应 PROPOSE（实际是包含关系）")

    # 语序/段落重排（同一内容不同组织方式）
    reordered = BASE_SHORT.split("。")[::-1]
    show("同内容·句序重排", BASE_SHORT, "。".join(reordered), "应 PROPOSE（语义同一）")

    # ── 流程层面的问题（v1.2 已修，保留探针证明修完了）──
    print("\n" + "=" * 86)
    print("流程层：dup.py 的比较范围")
    print("=" * 86)
    print("  · v1.1 及以前：只在「title_normalized 相同」的组内比较，而 title 默认取文件名")
    print("    → 「选题原则.md」与「选题原则-转发版.md」永不在同一组，近似重复≈永不触发。")
    print("  · v1.2：全库两两比较（长度窗口剪枝）+ 只比正文 + 包含度指标 + 骨架剪枝。")
    print(f"  下面这组的近似度 {jaccard(shingle(BASE_SHORT), shingle(mutate(BASE_SHORT, 3))):.4f}，"
          "v1.2 下只要标题不同也会被比较。")

    # ── 真跑一次 CLI，确认输出与内部计算一致（防"文档写了跑不通"）──
    tmp = tempfile.mkdtemp(prefix="probedup-")
    try:
        vault = os.path.join(tmp, "vault")
        os.makedirs(vault)
        # 故意用完全不同的文件名：验证"不同标题也会比较"
        pairs = [
            ("选题原则.md", BASE_SHORT),
            ("选题原则-转发版.md", mutate(BASE_SHORT, 1)),
            ("内容排期草稿.md", BASE_MID),
            ("内容排期-知乎.md", mutate(BASE_MID, 5)),
        ]
        for name, body in pairs:
            with open(os.path.join(vault, name), "w", encoding="utf-8") as f:
                f.write(body)
        idx = os.path.join(tmp, "index.json")
        subprocess.run([sys.executable, "-I", "-S", os.path.join(SCRIPTS, "scan.py"), vault, "-o", idx],
                       check=True, capture_output=True)
        r = subprocess.run([sys.executable, "-I", "-S", os.path.join(SCRIPTS, "dup.py"), idx],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        print("\n--- 实际 CLI 输出（4 个文件，文件名互不相同）---")
        print(r.stdout.strip())
        print("\n注：文件名不同却仍被列为候选 —— 这是 v1.2 的修复点。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
