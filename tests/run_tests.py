#!/usr/bin/env python3
"""run_tests.py — 引擎边界测试集 v2（21 用例）自动化回归脚本。

用途：把《obsidian-引擎-边界测试集-v1.md》的 21 个用例落成可执行断言，
      每个用例在独立临时沙盒跑（不碰真实库），输出 PASS / FAIL / SKIP 清单。

用法：
  python3 run_tests.py                # 跑全部 21 用例
  python3 run_tests.py --only 01,17   # 只跑指定用例
  python3 run_tests.py --keep         # 保留沙盒（调试用，不清理）

退出码：全过（含 SKIP）= 0；任一 FAIL / ERROR = 1。

设计约定：
  - 每个 case 返回 ("PASS", "") 或 ("SKIP", "原因")，失败抛 AssertionError。
  - 用 subprocess 跑真实 CLI（scan/move/verify/restore），保证测的是可执行路径。
  - 用 verify.diff() 拿结构化差异做断言（不解析文本）。
  - 辅助文件（index.json/affected.zip/change-log.csv）写到沙盒外的工作目录，
    避免被 scan 当成笔记扫进去污染 vault。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "src" / "engine" / "scripts"
PY = sys.executable

# 允许 import verify.py 拿结构化结果
sys.path.insert(0, str(SCRIPTS))
import verify as verify_mod  # noqa: E402
import health as health_mod  # noqa: E402

PASS = ("PASS", "")
SKIP = ("SKIP", "")

# 全局工作目录（每个 case 重置），辅助文件写这里，不进 vault 沙盒
WORK = None


def art(name):
    """辅助文件路径（写到沙盒外 WORK 目录）。"""
    return str(Path(WORK) / name)


def sk(reason):
    return ("SKIP", reason)


# ---------- CLI 封装 ----------

def sh(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def run_scan(sb, out):
    return sh([PY, str(SCRIPTS / "scan.py"), str(sb), "-o", str(out)])


def run_move(sb, index, src, dst, backup, log):
    return sh([PY, str(SCRIPTS / "move.py"), str(index),
               "--src", src, "--dst", dst,
               "--backup", str(backup), "--log", str(log)])


def run_restore(sb, log, backup, index_before):
    return sh([PY, str(SCRIPTS / "restore.py"), str(sb), str(log), str(backup),
               "--index-before", str(index_before)])


# ---------- 沙盒辅助 ----------

def write(sb, rel, content):
    full = Path(sb) / rel
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")


def read(sb, rel):
    return (Path(sb) / rel).read_text(encoding="utf-8")


def exists(sb, rel):
    return (Path(sb) / rel).exists()


def load_index(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def assert_restore_clean(sb, before_index, log, backup):
    """restore 后重扫，与 before 对比，断言差异归零。"""
    run_restore(sb, log, backup, before_index)
    after_path = art("after-restore.json")
    run_scan(sb, after_path)
    before = load_index(before_index)
    after = load_index(after_path)
    r = verify_mod.diff(before, after)
    assert r["broken_delta"] == 0, f"restore 后断链变化 {r['broken_delta']}"
    assert not r["added"] and not r["removed"] and not r["moves"], \
        f"restore 未归零：+{r['added']} -{r['removed']} 移{r['moves']}"


def move_assert_restore(sb, src, dst, check_fn):
    """标准 move 用例骨架：scan→move→check_fn(改写断言)→verify→restore 归零。"""
    before = art("before.json")
    backup = art("affected.zip")
    log = art("change-log.csv")
    run_scan(sb, before)
    r = run_move(sb, before, src, dst, backup, log)
    assert r.returncode == 0, f"move 失败: {r.stderr}"
    check_fn(sb, before, src, dst)
    # verify 断链不增
    after = art("after.json")
    run_scan(sb, after)
    b, a = load_index(before), load_index(after)
    d = verify_mod.diff(b, a)
    assert d["broken_delta"] == 0, f"断链变化 {d['broken_delta']}（{d['broken_new']}）"
    assert_restore_clean(sb, before, log, backup)


# ---------- 21 个用例 ----------

def case_01(sb):
    """最短路径重写：B/Note → C/Note，[[B/Note]] 更新为 [[C/Note]]。"""
    write(sb, "A/Note.md", "见 [[B/Note]]")
    write(sb, "B/Note.md", "B 内容")
    def check(sb, before, src, dst):
        assert read(sb, "A/Note.md") == "见 [[C/Note]]", f"未改写: {read(sb, 'A/Note.md')!r}"
    move_assert_restore(sb, "B/Note.md", "C/Note.md", check)
    return PASS


def case_02(sb):
    """同名文件消歧：[[Readme]] 解析到最短 P/Readme.md，移动 Archive/Readme.md 不误伤。"""
    # 注：文档原用 Projects/Archive 目录名，但 "Projects"(8) > "Archive"(7)，
    # 最短路径会指向 Archive，与文档"解析到 Projects"矛盾。此处改用 P/（最短）保证语义正确。
    write(sb, "P/Readme.md", "最短路径目标")
    write(sb, "Archive/Readme.md", "被移动的")
    write(sb, "Index.md", "[[Readme]]")
    write(sb, "Index2.md", "[[Archive/Readme]]")
    def check(sb, before, src, dst):
        assert read(sb, "Index.md") == "[[Readme]]", f"[[Readme]] 被误改: {read(sb, 'Index.md')!r}"
        assert read(sb, "Index2.md") == "[[Archive/Old/Readme]]", \
            f"显式链接未改: {read(sb, 'Index2.md')!r}"
    move_assert_restore(sb, "Archive/Readme.md", "Archive/Old/Readme.md", check)
    return PASS


def case_03(sb):
    """嵌套目录移动：A/B/C/Deep → X/Y/Deep，跨 3 级。"""
    write(sb, "A/B/C/Deep.md", "深层内容")
    write(sb, "Root.md", "[[A/B/C/Deep]]")
    def check(sb, before, src, dst):
        assert read(sb, "Root.md") == "[[X/Y/Deep]]", f"未改写: {read(sb, 'Root.md')!r}"
        assert exists(sb, "X/Y/Deep.md"), "目标文件未就位"
    move_assert_restore(sb, "A/B/C/Deep.md", "X/Y/Deep.md", check)
    return PASS


def case_04(sb):
    """嵌入引用：![[Image.png]] → ![[Assets/Image.png]]。"""
    write(sb, "Note.md", "![[Image.png]]")
    write(sb, "Image.png", "PNG 占位")
    def check(sb, before, src, dst):
        assert read(sb, "Note.md") == "![[Assets/Image.png]]", \
            f"嵌入未改: {read(sb, 'Note.md')!r}"
    move_assert_restore(sb, "Image.png", "Assets/Image.png", check)
    return PASS


def case_05(sb):
    """block 引用：[[Note#^abc123]] → [[Sub/Note#^abc123]]，^block 保留。"""
    write(sb, "Note.md", "内容\n^abc123")
    write(sb, "Ref.md", "[[Note#^abc123]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[Sub/Note#^abc123]]", \
            f"block 未改: {read(sb, 'Ref.md')!r}"
    move_assert_restore(sb, "Note.md", "Sub/Note.md", check)
    return PASS


def case_06(sb):
    """代码块内伪链接：fenced/行内代码内的 [[...]] 一律不动。"""
    write(sb, "真实链接.md", "真实目标")
    write(sb, "Guide.md",
          "正文 [[真实链接]]\n\n"
          "```md\n这是 [[不应该被改写的链接]]\n```\n\n"
          "行内 `[[另一个伪链接]]` 也不动。\n")
    def check(sb, before, src, dst):
        t = read(sb, "Guide.md")
        assert "正文 [[Moved/真实链接]]" in t, f"正文未改: {t!r}"
        assert "[[不应该被改写的链接]]" in t, f"代码块被误改: {t!r}"
        assert "`[[另一个伪链接]]`" in t, f"行内代码被误改: {t!r}"
    move_assert_restore(sb, "真实链接.md", "Moved/真实链接.md", check)
    return PASS


def case_07(sb):
    """附件双语法：![[doc.pdf]] 和 [下载](doc.pdf) 同步改写。"""
    write(sb, "Note.md", "![[doc.pdf]] 和 [下载](doc.pdf)")
    write(sb, "doc.pdf", "PDF 占位")
    def check(sb, before, src, dst):
        t = read(sb, "Note.md")
        assert "![[Files/doc.pdf]]" in t, f"embed 未改: {t!r}"
        assert "[下载](Files/doc.pdf)" in t, f"mdlink 未改: {t!r}"
    move_assert_restore(sb, "doc.pdf", "Files/doc.pdf", check)
    return PASS


def case_08(sb):
    """.trash 与 .obsidian 排除：不收录、不触碰。"""
    write(sb, ".obsidian/config.json", "{}")
    write(sb, ".trash/Old.md", "旧文件")
    write(sb, "Live.md", "[[Old]]")
    before = art("before.json")
    run_scan(sb, before)
    idx = load_index(before)
    paths = [f["path"] for f in idx["files"]]
    assert not any(p.startswith(".obsidian/") for p in paths), f"收录了 .obsidian: {paths}"
    assert not any(p.startswith(".trash/") for p in paths), f"收录了 .trash: {paths}"
    backup = art("affected.zip")
    log = art("change-log.csv")
    run_move(sb, before, "Live.md", "Archive/Live.md", backup, log)
    assert read(sb, ".trash/Old.md") == "旧文件", ".trash 被改写"
    assert read(sb, ".obsidian/config.json") == "{}", ".obsidian 被改写"
    assert_restore_clean(sb, before, log, backup)
    return PASS


def case_09(sb):
    """中文与空格文件名：[[草稿 文件]] → [[归档/草稿 文件]]。"""
    write(sb, "我的笔记/草稿 文件.md", "草稿内容")
    write(sb, "Ref.md", "[[草稿 文件]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[归档/草稿 文件]]", \
            f"中文/空格未改: {read(sb, 'Ref.md')!r}"
    move_assert_restore(sb, "我的笔记/草稿 文件.md", "归档/草稿 文件.md", check)
    return PASS


def case_10(sb):
    """symlink：不跟随、不死循环、不误报断链。"""
    write(sb, "Real/Note.md", "真实文件")
    os.symlink("Real", os.path.join(sb, "Link"))
    os.symlink("..", os.path.join(sb, "Real", "loop"))  # 环
    before = art("before.json")
    r = run_scan(sb, before)
    assert r.returncode == 0, f"scan 因 symlink 环崩溃: {r.stderr}"
    idx = load_index(before)
    paths = [f["path"] for f in idx["files"]]
    assert not any(p.startswith("Link/") for p in paths), f"跟随了 symlink 目录: {paths}"
    backup = art("affected.zip")
    log = art("change-log.csv")
    run_move(sb, before, "Real/Note.md", "Real/Moved.md", backup, log)
    after = art("after.json")
    run_scan(sb, after)
    d = verify_mod.diff(load_index(before), load_index(after))
    assert d["broken_delta"] == 0, f"symlink 被误报断链: {d['broken_new']}"
    assert_restore_clean(sb, before, log, backup)
    return PASS


def case_11(sb):
    """别名 wikilink：[[草稿|我的草稿]] → [[归档/草稿|我的草稿]]，别名不丢。"""
    write(sb, "草稿.md", "草稿内容")
    write(sb, "Ref.md", "[[草稿|我的草稿]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[归档/草稿|我的草稿]]", \
            f"别名未保留: {read(sb, 'Ref.md')!r}"
    move_assert_restore(sb, "草稿.md", "归档/草稿.md", check)
    return PASS


def case_12(sb):
    """heading 链接：[[Note#一 概述]] → [[Sub/Note#一 概述]]，标题保留。"""
    write(sb, "Note.md", "## 一 概述\n内容")
    write(sb, "Ref.md", "[[Note#一 概述]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[Sub/Note#一 概述]]", \
            f"heading 未改: {read(sb, 'Ref.md')!r}"
    move_assert_restore(sb, "Note.md", "Sub/Note.md", check)
    return PASS


def case_13(sb):
    """Canvas .canvas：JSON file 字段专用重写 + 可回滚。"""
    write(sb, "A/Note.md", "笔记内容")
    canvas = {"nodes": [{"type": "text", "text": "节点", "file": "A/Note.md"}]}
    write(sb, "Board.canvas", json.dumps(canvas, ensure_ascii=False))
    def check(sb, before, src, dst):
        data = json.loads(read(sb, "Board.canvas"))
        assert data["nodes"][0]["file"] == "X/Note.md", \
            f"canvas file 字段未改: {data['nodes'][0]['file']!r}"
    move_assert_restore(sb, "A/Note.md", "X/Note.md", check)
    return PASS


def case_14(sb):
    """目录级移动：Projects/ 整文件夹 → Archive/Projects/，内外部链接全更新。"""
    write(sb, "Projects/A.md", "A 见 [[B]] 和 [[C]]")
    write(sb, "Projects/B.md", "B 见 [[A]]")
    write(sb, "Projects/C.md", "C 内容")
    write(sb, "Index.md", "[[Projects/A]]")
    def check(sb, before, src, dst):
        assert exists(sb, "Archive/Projects/A.md"), "A 未移动"
        assert exists(sb, "Archive/Projects/B.md"), "B 未移动"
        assert exists(sb, "Archive/Projects/C.md"), "C 未移动"
        assert not exists(sb, "Projects/A.md"), "源目录残留"
        assert read(sb, "Index.md") == "[[Archive/Projects/A]]", \
            f"外部链接未改: {read(sb, 'Index.md')!r}"
        assert read(sb, "Archive/Projects/A.md") == "A 见 [[Archive/Projects/B]] 和 [[Archive/Projects/C]]", \
            f"内部互链未改: {read(sb, 'Archive/Projects/A.md')!r}"
    move_assert_restore(sb, "Projects", "Archive/Projects", check)
    return PASS


def case_15(sb):
    """重命名：根 A.md → B.md，同名 Other/A.md 引用不受影响。"""
    write(sb, "A.md", "根目录 A")
    write(sb, "Other/A.md", "另一个 A")
    write(sb, "Ref.md", "[[A]]")
    write(sb, "RefOther.md", "[[Other/A]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[B]]", f"根 A 未改名: {read(sb, 'Ref.md')!r}"
        assert read(sb, "RefOther.md") == "[[Other/A]]", \
            f"同名 Other/A 被误改: {read(sb, 'RefOther.md')!r}"
    move_assert_restore(sb, "A.md", "B.md", check)
    return PASS


def case_16(sb):
    """frontmatter 内链接改写（不破坏 YAML 结构）。"""
    write(sb, "Old.md", "旧文件")
    write(sb, "Note.md", '---\ntitle: 我的笔记\nrelated: "[[Old]]"\n---\n\n正文内容\n')
    def check(sb, before, src, dst):
        t = read(sb, "Note.md")
        assert 'related: "[[Sub/Old]]"' in t, f"frontmatter 未改: {t!r}"
        assert t.startswith("---\n"), f"YAML 头被破坏: {t!r}"
        assert 'title: 我的笔记' in t, f"YAML 键被破坏: {t!r}"
    move_assert_restore(sb, "Old.md", "Sub/Old.md", check)
    return PASS


def case_17(sb):
    """文件名含保留字符 #：[[Note #1]] → [[Sub/Note #1]]，# 不当 heading 分隔符。"""
    write(sb, "Note #1.md", "带 # 的文件")
    write(sb, "Ref.md", "[[Note #1]]")
    def check(sb, before, src, dst):
        assert read(sb, "Ref.md") == "[[Sub/Note #1]]", \
            f"# 文件名未正确处理: {read(sb, 'Ref.md')!r}"
    move_assert_restore(sb, "Note #1.md", "Sub/Note #1.md", check)
    return PASS


def case_18(sb):
    """幂等性：同一 move 执行两次，第二次跳过且不产生半成品。"""
    write(sb, "Note.md", "内容")
    write(sb, "Ref.md", "[[Note]]")
    before = art("before.json")
    backup = art("affected.zip")
    log = art("change-log.csv")
    run_scan(sb, before)
    r1 = run_move(sb, before, "Note.md", "Sub/Note.md", backup, log)
    assert r1.returncode == 0, f"第一次 move 失败: {r1.stderr}"
    # 第二次用同一个 before.json（旧 index，源仍在其中）
    backup2 = art("affected2.zip")
    log2 = art("change-log2.csv")
    r2 = run_move(sb, before, "Note.md", "Sub/Note.md", backup2, log2)
    assert "不存在" in r2.stdout or "跳过" in r2.stdout, \
        f"第二次未提示源不存在: stdout={r2.stdout!r}"
    assert not exists(sb, "Note.md"), "源文件残留"
    assert exists(sb, "Sub/Note.md"), "目标缺失"
    assert read(sb, "Ref.md") == "[[Sub/Note]]", f"Ref 异常: {read(sb, 'Ref.md')!r}"
    # 用第一次的 log 回滚（验证断点续跑能力：第二次不影响第一次的 change-log）
    assert_restore_clean(sb, before, log, backup)
    return PASS


def case_19(sb):
    """大小写不敏感文件系统（macOS APFS）：Note.md 与 note.md 无法并存。"""
    write(sb, "Note.md", "大写版本")
    write(sb, "note.md", "小写版本")
    names = os.listdir(sb)
    has_both = "Note.md" in names and "note.md" in names
    if not has_both:
        return sk("环境不支持：APFS 大小写不敏感，无法构造 Note.md 与 note.md 并存")
    # 大小写敏感文件系统（如 Linux ext4）：验证歧义处理
    before = art("before.json")
    run_scan(sb, before)
    idx = load_index(before)
    assert idx["files"], "scan 无结果"
    return PASS


def case_20(sb):
    """delete / 移进 .trash 的回滚：完整还原、.trash 不残留。"""
    write(sb, "Note.md", "要删除的内容")
    before = art("before.json")
    backup = art("affected.zip")
    log = art("change-log.csv")
    run_scan(sb, before)
    r = run_move(sb, before, "Note.md", ".trash/Note.md", backup, log)
    assert r.returncode == 0, f"move 到 .trash 失败: {r.stderr}"
    assert not exists(sb, "Note.md"), "原文件仍在"
    assert exists(sb, ".trash/Note.md"), ".trash 里没有文件"
    with open(log, "r", encoding="utf-8") as f:
        lines = f.read().strip().splitlines()
    assert any("delete" in ln for ln in lines[1:]), f"change-log 未记 delete: {lines}"
    assert_restore_clean(sb, before, log, backup)
    assert exists(sb, "Note.md"), "还原后原文件缺失"
    assert not exists(sb, ".trash/Note.md"), ".trash 残留"
    return PASS


def case_21(sb):
    """文件名含点（版本号）：[[手册-v5.0.2-快照]] 正确解析，不误判扩展名。"""
    write(sb, "手册-v5.0.2-快照.md", "快照内容")
    write(sb, "项目卡.md", "[[手册-v5.0.2-快照]]")
    before = art("before.json")
    run_scan(sb, before)
    idx = load_index(before)
    links = [l for l in idx["links"] if l["from"] == "项目卡.md"]
    assert len(links) == 1, f"链接数异常: {links}"
    assert links[0]["resolved"] == "手册-v5.0.2-快照.md", \
        f"点号文件名解析错误: {links[0]['resolved']!r}"
    return PASS


# ---------- 22-24：体检噪音治理（模板占位 / 垃圾文件 / 重名分档）----------

def case_22(sb):
    """[P0] 系统垃圾文件排除：.DS_Store / ._xxx / Thumbs.db 不进索引、不计入统计。
    背景：macOS 用户 100% 会撞上，若收录会让笔记数、孤儿数、缺元数据全部虚高。"""
    write(sb, "Note.md", "---\ntitle: N\n---\n\n正文\n")
    write(sb, ".DS_Store", "junk")
    write(sb, "._Note.md", "appledouble")   # macOS 资源分叉，扩展名仍是 .md
    write(sb, "Thumbs.db", "junk")
    write(sb, "~$草稿.docx", "office-temp")
    write(sb, "Sub/.DS_Store", "junk")
    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)
    paths = {f["path"] for f in d["files"]}
    for bad in (".DS_Store", "._Note.md", "Thumbs.db", "~$草稿.docx", "Sub/.DS_Store"):
        assert bad not in paths, f"垃圾文件被收录进索引：{bad}"
    assert d["stats"]["excluded_junk"] == 5, \
        f"excluded_junk={d['stats']['excluded_junk']}，应为 5"
    assert d["stats"]["total_notes"] == 1, \
        f"笔记数应为 1（只有 Note.md），实际 {d['stats']['total_notes']}"
    return PASS


def case_23(sb):
    """[P0] 模板占位符不冒充断链：模板里的 [[占位符]] 归「已豁免」，正文断链才是真问题。
    背景：模板给用户填，未实例化的链接必然解析失败；报成断链会淹没真毛病。"""
    write(sb, "99-模板/项目模板.md",
          "---\ntype: project\n---\n\n- [[知识卡名]]\n- [[2026-xx-xx 对话纪要]]\n")
    write(sb, "Note.md", "---\ntitle: N\n---\n\n真断链：[[真的不存在的笔记]]\n")
    write(sb, "Home.md", "---\ntitle: H\n---\n\n[[Note]]\n")
    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)

    roles = {f["path"]: f.get("role") for f in d["files"] if f["type"] == "note"}
    assert roles["99-模板/项目模板.md"] == "template", f"模板角色判定失败：{roles}"
    assert roles["Note.md"] == "content", f"正文角色判定失败：{roles}"

    res = health_mod.compute(d)
    assert len(res["broken_links"]) == 1, \
        f"真断链应只有 1 条，实际 {len(res['broken_links'])}：" \
        f"{[l['to'] for l in res['broken_links']]}"
    assert len(res["broken_placeholder"]) == 2, \
        f"模板占位应 2 条，实际 {len(res['broken_placeholder'])}"
    assert "99-模板/项目模板.md" not in res["orphan_notes"], "模板被误报为孤儿"
    assert "99-模板/项目模板.md" in res["orphan_exempt"].get("template", []), \
        "模板未归入「模板无入链」豁免档"
    return PASS


def case_24(sb):
    """[P0] 重名分档：同名不同内容=正常分类（不报重复）；同名同内容 / 不同名同内容=真重复。
    背景：各项目目录下各一份「项目卡」是正确的分类设计，报成重复是误导。"""
    write(sb, "健康/项目卡.md", "---\ntitle: 健康\n---\n\n健康项目的内容\n")
    write(sb, "竞彩/项目卡.md", "---\ntitle: 竞彩\n---\n\n竞彩项目的内容\n")
    write(sb, "Dup1.md", "---\ntitle: 同\n---\n\n完全一样的内容 ABC\n")
    write(sb, "Dup2.md", "---\ntitle: 同\n---\n\n完全一样的内容 ABC\n")
    write(sb, "S1/同名.md", "---\ntitle: x\n---\n\n同样的正文 ZZZ\n")
    write(sb, "S2/同名.md", "---\ntitle: x\n---\n\n同样的正文 ZZZ\n")
    write(sb, "Home.md", "---\ntitle: H\n---\n\n[[Dup1]]\n")
    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)
    res = health_mod.compute(d)

    assert "项目卡" in res["same_name_diff"], \
        f"同名不同内容未归入分类档：{res['same_name_diff']}"
    assert "项目卡" not in res["duplicate_names"], "同名不同内容被误报为重复"
    assert "同名" in res["duplicate_names"], \
        f"同名同内容未判为重复：{res['duplicate_names']}"
    assert any("Dup1.md" in g and "Dup2.md" in g for g in res["content_duplicates"]), \
        f"不同名同内容未判为重复：{res['content_duplicates']}"
    return PASS


def case_25(sb):
    """[P0] 角色豁免：日记 / 索引页 / 归档无入链不算孤儿。
    背景：300+ 篇压测发现，真实库里这三类占「无入链文件」八成以上，
    全报成孤儿会让精确率跌到 10%，用户直接不信这个工具。"""
    # 有入链的正文
    write(sb, "Home.md", "---\ntype: moc\n---\n\n[[正文A]] [[10-项目档案/项目01/项目卡|项目01]]\n")
    write(sb, "正文A.md", "---\ntitle: A\n---\n\n正文\n")
    # 各类天然无入链的文件
    write(sb, "10-项目档案/项目01/项目卡.md", "---\ntitle: P1\n---\n\n项目卡\n")
    write(sb, "30-对话日记/2026-10-22.md", "---\ntype: daily\n---\n\n今日纪要\n")
    write(sb, "30-对话日记/2026-10-21.md", "# 2026-10-21\n\n无 frontmatter 的日记\n")
    write(sb, "90-归档/手册-v1.0-快照.md", "# 旧版快照\n\n归档内容\n")
    write(sb, "99-模板/日记模板.md", "---\ntype: daily\n---\n\n# {{date}}\n\n- [[占位知识卡]]\n")

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)

    roles = {f["path"]: f.get("role") for f in d["files"] if f["type"] == "note"}
    assert roles["Home.md"] == "index", f"库规/MOC 未识别为 index：{roles.get('Home.md')}"
    assert roles["30-对话日记/2026-10-22.md"] == "daily", \
        f"日记未识别：{roles.get('30-对话日记/2026-10-22.md')}"
    assert roles["90-归档/手册-v1.0-快照.md"] == "archive", \
        f"归档未识别：{roles.get('90-归档/手册-v1.0-快照.md')}"

    res = health_mod.compute(d)
    # 孤儿档里只应剩「正文A」以外的空集——即 0 篇
    assert res["orphan_notes"] == [], \
        f"日记/索引/归档被误报为孤儿：{res['orphan_notes']}"
    ex = res["orphan_exempt"]
    assert len(ex.get("daily", [])) == 2, f"日记豁免数不对：{ex.get('daily')}"
    assert len(ex.get("index", [])) == 1, f"索引豁免数不对：{ex.get('index')}"
    assert len(ex.get("archive", [])) == 1, f"归档豁免数不对：{ex.get('archive')}"
    assert len(ex.get("template", [])) == 1, f"模板豁免数不对：{ex.get('template')}"
    return PASS


def case_26(sb):
    """[P0] 日期前缀的原子卡不该被判成日记（Zettelkasten 陷阱）。
    背景：ZK 用户常用「2026-09-01 闪念1.md」命名原子卡。早期只要文件名以日期开头
    就判日记，结果 40 篇内容卡被豁免，孤儿被静默隐藏——ZK 压测孤儿召回率只有 73.3%。
    修法：日期前缀只有在「所在目录整体是时间流」时才当日记。"""
    # 目录级证据：6 篇纯日期名 → 该目录判为时间流
    for d in range(1, 7):
        write(sb, f"20 时间线/2026-08-0{d}.md", f"# 8/{d}\n\n时间流内容 {d}\n")
    # ZK 原子卡：日期前缀但目录不是时间流（根目录混着别的东西）
    write(sb, "2026-09-01 闪念1.md", "---\ntitle: 闪念1\n---\n\n原子卡内容\n\n[[2026-09-02 闪念2]]\n")
    write(sb, "2026-09-02 闪念2.md", "---\ntitle: 闪念2\n---\n\n原子卡内容二\n\n[[2026-09-01 闪念1]]\n")
    write(sb, "孤立卡.md", "---\ntitle: 孤立\n---\n\n没人链我\n")
    # 配置优先：daily-notes.json 声明的目录，文件名随便叫也算日记
    write(sb, ".obsidian/daily-notes.json",
          json.dumps({"folder": "Journal", "format": "YYYY-MM-DD"}, ensure_ascii=False))
    write(sb, "Journal/随便一个名字.md", "# 今天\n\n配置声明这是日记目录\n")

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)
    roles = {f["path"]: f.get("role") for f in d["files"] if f["type"] == "note"}

    assert roles["2026-09-01 闪念1.md"] == "content", \
        f"日期前缀原子卡被误判为日记：role={roles.get('2026-09-01 闪念1.md')}"
    assert roles["20 时间线/2026-08-01.md"] == "daily", \
        f"整目录是时间流却未识别：role={roles.get('20 时间线/2026-08-01.md')}"
    assert roles["Journal/随便一个名字.md"] == "daily", \
        f"daily-notes.json 配置未生效：role={roles.get('Journal/随便一个名字.md')}"

    res = health_mod.compute(d)
    assert "孤立卡.md" in res["orphan_notes"], "真孤儿未被报出"
    assert "2026-09-01 闪念1.md" not in res["orphan_notes"], "互为链接的原子卡被误报孤儿"
    return PASS


def case_27(sb):
    """[P0] 模板识别的两个真实盲区。
    ① 目录名带排序前缀（99-模板）—— 精确匹配目录名会失效；
    ② 模板目录叫非标准名（_素材），文件名也不含「模板」，只能靠正文 {{date}} 认定。"""
    write(sb, "_素材/通用.md", "---\ntitle: 通用\n---\n\n# {{date}}\n\n关联：[[{{related}}]]\n")
    write(sb, "99-模板/随手记.md", "# 随手\n\n- [[待填写]]\n")
    write(sb, "正文.md", "---\ntitle: 正文\n---\n\n[[真的不存在]]\n")

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    d = load_index(idx)
    roles = {f["path"]: f.get("role") for f in d["files"] if f["type"] == "note"}

    assert roles["_素材/通用.md"] == "template", \
        f"靠 {{date}} 语法未识别出模板：role={roles.get('_素材/通用.md')}"
    assert roles["99-模板/随手记.md"] == "template", \
        f"带数字前缀的模板目录未识别：role={roles.get('99-模板/随手记.md')}"

    res = health_mod.compute(d)
    broken_real = {l["from"] for l in res["broken_links"]}
    assert broken_real == {"正文.md"}, f"模板占位混进真断链：{broken_real}"
    assert len(res["broken_placeholder"]) == 2, \
        f"模板占位漏报：{len(res['broken_placeholder'])}"
    return PASS


def case_28(sb):
    """[P0] 近似重复必须真的能查出来，且不被模板骨架污染。
    两个真实缺陷（v1.2 修）：
      ① 旧实现按 title(=文件名) 分组比较 → 文件名不同的近似内容永不比较，功能等于没实现；
      ② 20 篇同模板建的项目卡会产出 190 对 0.90+ 的假候选，把真重复淹没。"""
    # 60 篇同模板笔记（骨架一致、实体不同）—— 不该被判成互相重复
    for i in range(1, 61):
        write(sb, f"项目卡{i:02d}.md",
              "---\ntype: project\n---\n\n# 项目卡\n\n## 背景\n项目背景说明。\n"
              "## 相关笔记\n- [[知识卡01]]\n## 本次结论\n"
              f"第 {i} 个项目的结论。\n")
    write(sb, "知识卡01.md", "---\ntitle: 知识卡01\n---\n\n被项目卡引用\n")

    base1 = ("做内容运营时选题要先确认人群确定性，而不是话题新鲜度。"
             "先确认这批人一定会点，再考虑怎么把标题写得好看。"
             "渠道选择比内容质量更早决定成败。")
    # 文件名完全不同 + 内容近似 → 必须被报出（①）
    write(sb, "选题原则.md", base1)
    write(sb, "选题原则-转发版.md", base1.replace("人群确定性", "受众确定性"))
    # 删节版（包含关系）：另一段独立正文，避免与上面两组交叉（交叉会被骨架剪枝）
    base2 = ("竞彩预测的收益来自纪律而不是单场判断的灵感。资金分配比选场更重要。"
             "复盘要写清失败原因是模型错还是判断错还是运气差。")
    write(sb, "内容排期草稿.md", base2 + "任何自动化只要做不到可回滚就不该碰用户的文件。")
    write(sb, "内容排期-摘录.md", base2)

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr
    out = art("dup.txt")
    p = subprocess.run([PY, "-I", "-S", str(SCRIPTS / "dup.py"), idx, "-o", out],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    text = Path(out).read_text(encoding="utf-8")

    pairs = []
    for line in text.splitlines():
        if "⇄" in line:
            left, right = line.split("⇄")
            pairs.append((left.split()[-1].strip(), right.strip()))
    joined = ["|".join(sorted(x)) for x in pairs]

    assert any("选题原则.md" in x and "选题原则-转发版.md" in x for x in joined), \
        f"文件名不同的近似笔记未被查出（①）：{joined}"
    assert any("内容排期草稿.md" in x and "内容排期-摘录.md" in x for x in joined), \
        f"删节版（包含关系）未被查出：{joined}"

    template_noise = [x for x in joined if x.count("项目卡") == 2]
    assert not template_noise, \
        f"同模板笔记被误报为互相重复 {len(template_noise)} 对（②）：{template_noise[:3]}"
    return PASS


def case_29(sb):
    """[P0] AUTO 档（可进 .trash）只认字节一致，包含度算出 1.0 不得进 AUTO。

    v1.3 修的真 bug：一篇短笔记的内容被长笔记完整包含时，containment 指标
    = |A∩B| / min(|A|,|B|) 会算出精确的 1.0。旧 tier() 只看分数不看依据，
    把它判成 AUTO（"字节完全一致，可进 .trash"）。用户据此删掉的可能是一篇
    独立笔记——它只是内容被更大的那篇盖住了。相似度再高也只是「像」，不是「是」。

    这个用例同时守两侧：包含关系必须报出（不能漏），但绝不能进 AUTO（不能致命）。
    """
    long_text = ("做内容运营时，选题的第一原则是人群确定性，而不是话题新鲜度，先确认这批人一定会点进去。"
                 "渠道选择比内容质量更早决定成败，因为渠道决定了谁看得见你。"
                 "复购来自持续交付确定性，而不是偶尔的惊喜，把选择权交给用户更安全。"
                 "定价是估算用户愿意为这个确定性付多少，不是成本加利润。")
    write(sb, "完整长文.md", long_text)
    # 前 75% 被完整包含；篇幅比 0.75 > MIN_LEN_RATIO(0.5)，会启用包含度指标
    write(sb, "长文摘录片段.md", long_text[:int(len(long_text) * 0.75)])
    # 字节一致的一对：必须进 AUTO（否则修过头，把真重复也降级了）
    same = "这一篇的内容要被完整复制一份用于对照，长度要够以免被 shingle 下限过滤掉。\n"
    write(sb, "字节一致A.md", same)
    write(sb, "字节一致B.md", same)

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr

    jout = art("dup.json")
    p = subprocess.run([PY, "-I", "-S", str(SCRIPTS / "dup.py"), idx, "--json", jout],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    data = json.loads(Path(jout).read_text(encoding="utf-8"))

    by_pair = {tuple(sorted(c["paths"])): c for c in data["candidates"]}

    contained = by_pair.get(tuple(sorted(["完整长文.md", "长文摘录片段.md"])))
    assert contained is not None, \
        f"包含关系的一对完全没被查出（漏报）：{list(by_pair)}"
    assert contained["tier"] != "AUTO", (
        f"包含关系被判成 AUTO（可进 .trash）——会误导用户删掉独立笔记。"
        f"score={contained['score']} basis={contained['basis']}")
    assert contained["basis"] == "containment" and contained["score"] >= 0.99, \
        f"这个样本本应触发 containment≈1.0，实际 basis={contained['basis']} score={contained['score']}"

    identical = by_pair.get(tuple(sorted(["字节一致A.md", "字节一致B.md"])))
    assert identical is not None, "字节一致的一对未被查出"
    assert identical["tier"] == "AUTO", \
        f"字节一致却没进 AUTO（修过头）：{identical}"
    return PASS


def case_30(sb):
    """[P1] plan.py 治理计划：分级正确、编号连续、健康分自洽、缺 dup 时不假装无重复。

    plan.py 是治库专家对用户的第一句话，它错在「分级」上比算错数字严重得多：
    把该动的放进「别动」用户就永远不修；把不该动的放进「立刻修」用户会毁库。
    """
    PLANNER = SCRIPTS.parent.parent / "restructure-planner" / "scripts"
    assert (PLANNER / "plan.py").exists(), f"plan.py 不存在：{PLANNER}"

    # A 级素材：垃圾文件 + 完全重复
    write(sb, ".DS_Store", "junk")
    write(sb, "资料/Thumbs.db", "junk")
    dup_body = ("这段正文用来制造字节级完全一致的两篇笔记，"
                "内容要足够长以免被 shingle 下限过滤掉。\n")
    write(sb, "重复甲.md", dup_body)
    write(sb, "重复乙.md", dup_body)
    # A 级素材：正文断链（真断，不是模板占位）
    write(sb, "有断链的正文.md", "这里引用了 [[根本不存在的笔记]]。\n")
    # B 级素材：孤儿
    write(sb, "孤立正文.md", "没有人链接到这一篇。\n")
    # C 级素材：模板占位（不得算断链）+ 日记（不得算孤儿）
    write(sb, "模板/模板样例.md", "请填写 [[示例笔记名]]。\n")
    write(sb, "日记/2026-01-01.md", "今天写了一篇日记。\n")

    idx = art("i.json")
    r = run_scan(sb, idx)
    assert r.returncode == 0, r.stderr

    jout = art("plan.json")
    p = subprocess.run([PY, "-I", "-S", str(PLANNER / "plan.py"), idx, "--json", jout],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    plan = json.loads(Path(jout).read_text(encoding="utf-8"))

    # ① 分级正确性
    assert plan["detail"]["junk"], "垃圾文件没被 A 级抓到"
    assert len(plan["detail"]["content_dups"]) >= 1, "完全重复没被 A 级抓到"
    assert plan["detail"]["broken"], "正文断链没被 A 级抓到"
    assert "孤立正文.md" in plan["detail"]["orphans"], "孤立正文没被 B 级抓到"
    # ② C 级豁免：模板占位与日记不得混进真问题
    assert "模板/模板样例.md" not in [x["from"] for x in plan["detail"]["broken"]], \
        "模板占位被当成了正文断链（C 级漏判成 A 级）"
    assert "日记/2026-01-01.md" not in plan["detail"]["orphans"], \
        "日记被当成了孤儿（C 级漏判成 B 级）"
    # ③ 健康分自洽：0–100 且修完只会更高
    s0, s1, s2 = plan["health_score"], plan["score_after_A"], plan["score_after_AB"]
    assert 0 <= s0 <= 100, f"健康分越界：{s0}"
    assert s0 <= s1 <= s2 <= 100, f"修复后的分数不是单调升：{s0} → {s1} → {s2}"

    # ④ 终端输出：编号必须连续（不能出现 A1→A3 断号）
    out = subprocess.run([PY, "-I", "-S", str(PLANNER / "plan.py"), idx],
                         capture_output=True, text=True)
    text = out.stdout
    codes = [f"A{i}" for i in range(1, 10) if f"  A{i}  " in text]
    assert codes, f"终端输出里没有 A 级编号：\n{text[:400]}"
    expect = [f"A{i}" for i in range(1, len(codes) + 1)]
    assert codes == expect, f"A 级编号断号：出现 {codes}，应为 {expect}"
    assert "健康分" in text and "建议顺序" in text, f"输出缺少关键段落：\n{text[:400]}"
    return PASS


def case_31(sb):
    """[P1] verify 移动配对：库里有完全重复文件时，「删一份重复」不得误报成「移动」。

    旧实现的 hash→path 字典会把同 hash 多份折叠成一个路径，
    导致 ① 删一份重复被报成「移动到另一份」；② restore 误报「仍有差异」。
    正确语义：移入 .trash（不在扫描范围）= 删除；普通移动 = 移动。
    """
    body = ("这是用来制造两篇字节级完全一致的笔记的正文，"
            "长度需要超过 shingle 下限，内容本身不重要。\n")
    write(sb, "正本.md", body)
    write(sb, "重复副本.md", body)          # 与正本字节一致
    write(sb, "独立笔记.md", "和上面两篇完全无关的内容，随手写的一句话。\n")

    idx0 = art("i31a.json")
    assert run_scan(sb, idx0).returncode == 0

    # ── 场景①：把副本移入 .trash（= 从库视角删除）──
    r = run_move(sb, idx0, "重复副本.md", ".trash/重复副本.md",
                 art("a31.zip"), art("l31.csv"))
    assert r.returncode == 0, r.stderr
    idx1 = art("i31b.json")
    assert run_scan(sb, idx1).returncode == 0
    d = verify_mod.diff(_load_json(idx0), _load_json(idx1))
    assert not d["moves"], f"删一份重复被误报成移动：{d['moves']}"
    assert d["removed"] == ["重复副本.md"], f"应报删除且仅删副本：{d['removed']}"

    # 回滚必须差异归零
    r = run_restore(sb, art("l31.csv"), art("a31.zip"), idx0)
    assert r.returncode == 0, r.stderr
    idx2 = art("i31c.json")
    assert run_scan(sb, idx2).returncode == 0
    d = verify_mod.diff(_load_json(idx0), _load_json(idx2))
    assert not (d["moves"] or d["added"] or d["removed"]), \
        f"回滚后仍有差异：moves={d['moves']} +{d['added']} -{d['removed']}"

    # ── 场景②：普通移动仍应报移动（配对算法没被改坏）──
    r = run_move(sb, idx0, "独立笔记.md", "子目录/独立笔记.md",
                 art("a31b.zip"), art("l31b.csv"))
    assert r.returncode == 0, r.stderr
    idx3 = art("i31d.json")
    assert run_scan(sb, idx3).returncode == 0
    d = verify_mod.diff(_load_json(idx0), _load_json(idx3))
    assert d["moves"] == [("独立笔记.md", "子目录/独立笔记.md")], \
        f"普通移动配对错误：{d['moves']}"
    return PASS


def _load_json(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------- 注册 ----------

CASES = [
    ("01", "最短路径重写", case_01),
    ("02", "同名文件消歧", case_02),
    ("03", "嵌套目录移动", case_03),
    ("04", "嵌入引用 ![[embed]]", case_04),
    ("05", "block 引用 [[note#^block]]", case_05),
    ("06", "代码块内伪链接不改写", case_06),
    ("07", "附件双语法重写", case_07),
    ("08", ".trash 与 .obsidian 排除", case_08),
    ("09", "中文与空格文件名", case_09),
    ("10", "symlink 符号链接", case_10),
    ("11", "别名 [[note|别名]]", case_11),
    ("12", "heading 链接 [[note#标题]]", case_12),
    ("13", "Canvas .canvas 重写", case_13),
    ("14", "目录级移动", case_14),
    ("15", "重命名同名消歧", case_15),
    ("16", "frontmatter 保护", case_16),
    ("17", "文件名含 # 保留字符", case_17),
    ("18", "幂等性 + 中断恢复", case_18),
    ("19", "大小写不敏感", case_19),
    ("20", "delete / .trash 回滚", case_20),
    ("21", "文件名含点（版本号）", case_21),
    ("22", "系统垃圾文件排除", case_22),
    ("23", "模板占位不冒充断链", case_23),
    ("24", "重名分档（分类 vs 重复）", case_24),
    ("25", "角色豁免（日记/索引/归档）", case_25),
    ("26", "日期前缀原子卡不判日记（ZK 陷阱）", case_26),
    ("27", "模板识别盲区（前缀目录 / 非标准名）", case_27),
    ("28", "近似重复可查出且不被模板骨架污染", case_28),
    ("29", "AUTO 档只认字节一致（包含度 1.0 不得进 .trash）", case_29),
    ("30", "plan.py 分级/编号/健康分自洽", case_30),
    ("31", "verify 移动配对与完全重复消歧", case_31),
]


def main():
    global WORK
    ap = argparse.ArgumentParser(description="引擎边界测试集 v2 自动化回归")
    ap.add_argument("--only", help="逗号分隔的用例编号，如 01,17")
    ap.add_argument("--keep", action="store_true", help="保留沙盒不清理")
    args = ap.parse_args()

    only = None
    if args.only:
        only = {x.strip() for x in args.only.split(",") if x.strip()}

    results = []
    for cid, name, fn in CASES:
        if only and cid not in only:
            continue
        sb = tempfile.mkdtemp(prefix="obvault-t")
        work = tempfile.mkdtemp(prefix="obwork-t")
        WORK = work
        try:
            status, detail = fn(sb)
        except AssertionError as e:
            status, detail = "FAIL", str(e)
        except Exception as e:  # noqa: BLE001
            status, detail = "ERROR", repr(e)
        finally:
            if args.keep:
                print(f"[{cid}] 沙盒保留于 {sb} / 工作目录 {work}")
            else:
                shutil.rmtree(sb, ignore_errors=True)
                shutil.rmtree(work, ignore_errors=True)
        results.append((cid, name, status, detail))
        mark = {"PASS": "✅", "FAIL": "❌", "SKIP": "⏭️ ", "ERROR": "💥"}[status]
        print(f"{mark} {cid} {name}")
        if detail:
            print(f"      └─ {detail}")

    n_pass = sum(1 for r in results if r[2] == "PASS")
    n_fail = sum(1 for r in results if r[2] in ("FAIL", "ERROR"))
    n_skip = sum(1 for r in results if r[2] == "SKIP")
    print("\n" + "=" * 50)
    print(f"汇总：{len(results)} 用例 → ✅ {n_pass} / ❌ {n_fail} / ⏭️ {n_skip}")
    if n_fail:
        failed = [f"{r[0]} {r[1]}" for r in results if r[2] in ("FAIL", "ERROR")]
        print("失败用例：" + "、".join(failed))
    print("=" * 50)
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
