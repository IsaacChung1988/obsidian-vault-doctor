#!/usr/bin/env python3
"""gen_demo_vault.py — 生成演示库（商品化材料）

用法：python3 tests/gen_demo_vault.py [输出目录，默认 <repo>/demo-vault]

造一个 14 篇的小库，包含全部四类典型问题 + 三类"看着像问题其实正常"，
让新用户 30 秒内看到体检报告的效果。ground truth：
  - A 级：1 个垃圾文件、1 组完全重复、1 条真断链
  - B 级：1 篇孤儿、2 篇缺 frontmatter、1 组相似(删节版)
  - C 级：1 个模板占位、2 篇日记无回链、1 篇归档
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build(root):
    def w(rel, body):
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)

    def fm(title, tags, status="active"):
        return (f"---\ntitle: {title}\ntags: [{tags}]\nstatus: {status}\n---\n\n")

    # ── 正文：互相链接的网络 ──
    w("10-项目/网站改版.md", fm("网站改版", "项目") +
      "# 网站改版\n\n目标、范围见 [[设计规范]]，排期依赖 [[素材整理清单]]。\n\n"
      "上线前过一遍 [[发布检查清单]]。\n")
    w("10-项目/素材整理清单.md", fm("素材整理清单", "项目") +
      "# 素材整理清单\n\n图片统一放 [[设计规范]] 里的附件区。\n")
    w("20-领域/设计规范.md", fm("设计规范", "设计") +
      "# 设计规范\n\n色彩与栅格。参考 [[品牌故事]] 的调性。\n")
    w("20-领域/品牌故事.md", fm("品牌故事", "品牌") +
      "# 品牌故事\n\n一句话：把复杂的事说明白。\n")
    w("20-领域/发布检查清单.md", fm("发布检查清单", "运维") +
      "# 发布检查清单\n\n- [ ] 域名解析\n- [ ] 备份\n- [ ] [[设计规范]] 终稿确认\n")

    # ── B 级：孤儿（没人链接它）──
    w("20-领域/读书笔记-增长黑客.md", fm("读书笔记-增长黑客", "读书") +
      "# 读书笔记：增长黑客\n\nAARRR 模型速记。还没决定放哪个主题下。\n")

    # ── B 级：相似（删节版，dup.py 的 containment 会抓到）──
    long_body = (
        "做内容运营时，选题的第一原则是人群确定性，而不是话题新鲜度，"
        "先确认这批人一定会点进去。渠道选择比内容质量更早决定成败，"
        "因为渠道决定了谁看得见你。复购来自持续交付确定性，而不是偶尔的惊喜。")
    w("30-随手/选题方法论.md", fm("选题方法论", "运营") +
      "# 选题方法论\n\n" + long_body + "\n")
    w("30-随手/选题方法论-摘录.md", fm("选题方法论-摘录", "运营") +
      "# 选题方法论（摘录）\n\n" + long_body + "\n")

    # ── B 级：缺 frontmatter ──
    w("30-随手/会议记录-1012.md", "# 会议记录 10.12\n\n讨论了 [[网站改版]] 的排期。\n")
    w("30-随手/临时想法.md", "# 临时想法\n\n要不要给库加个 MOC？\n")

    # ── C 级：模板（占位链接 + 无入链，都该被豁免）──
    w("99-模板/读书笔记模板.md",
      "---\ntype: template\n---\n\n# [[书名]] 读书笔记\n\n核心观点：\n\n延伸：[[相关笔记]]\n")

    # ── C 级：日记（无回链属正常）──
    w("40-日记/2026-10-20.md", fm("2026-10-20", "日记") +
      "# 2026-10-20\n\n改了 [[设计规范]] 的栅格。\n")
    w("40-日记/2026-10-21.md", fm("2026-10-21", "日记") +
      "# 2026-10-21\n\n开了发布检查会。\n")

    # ── C 级：归档 ──
    w("90-归档/网站改版-2025旧版.md",
      fm("网站改版-2025旧版", "归档", status="archived") +
      "# 网站改版（2025 旧版）\n\n已废弃，保留备查。\n")

    # ── A 级：完全重复（必须字节级一致——frontmatter 也一字不差）──
    w("30-随手/截图说明.md", fm("截图说明", "素材") +
      "# 截图说明\n\n所有截图先拖进附件目录再引用。\n")
    w("30-随手/截图说明-副本.md", fm("截图说明", "素材") +
      "# 截图说明\n\n所有截图先拖进附件目录再引用。\n")

    # ── A 级：真断链（指向不存在的笔记）──
    w("10-项目/排期表.md", fm("排期表", "项目") +
      "# 排期表\n\n里程碑对齐 [[版本规划总表]]（还没建）。\n")

    # ── A 级：垃圾文件 ──
    with open(os.path.join(root, ".DS_Store"), "wb") as f:
        f.write(b"\x00\x01junk")


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "demo-vault")
    if os.path.exists(root):
        import shutil
        shutil.rmtree(root)
    os.makedirs(root)
    build(root)
    n = sum(len(fs) for _, _, fs in os.walk(root))
    print(f"✅ 演示库已生成：{root}（{n} 个文件）")
    print("   跑法：scan.py <该目录> -o index.json && health.py index.json && plan.py index.json")


if __name__ == "__main__":
    main()
