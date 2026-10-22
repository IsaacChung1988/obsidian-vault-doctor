#!/usr/bin/env python3
"""common.py — 引擎共享工具函数（纯 Python 标准库，零第三方依赖）

供 scan.py / health.py / dup.py / move.py / verify.py / restore.py 复用。
"""
import hashlib
import os
import re

# 附件/非笔记扩展名（用于文件分类与附件定位）
ATTACHMENT_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".pdf", ".mp3", ".mp4", ".wav", ".canvas",
}

# 遍历时排除的目录
EXCLUDE_DIRS = {".obsidian", ".trash", ".git", "node_modules", "__MACOSX"}

# ── 系统/同步垃圾文件（不计入笔记统计、不参与链接解析）──────────────
# macOS 的 .DS_Store / 资源分叉 ._xxx、Windows 的 Thumbs.db、Office 临时文件、
# 编辑器 swap、下载中的分片文件。这些在真实用户库里几乎必然存在，
# 若计入统计会让「文件数」「孤儿」「缺元数据」全部虚高。
JUNK_BASENAMES = {".DS_Store", "Thumbs.db", "thumbs.db", "desktop.ini",
                  ".localized", ".Spotlight-V100", "ehthumbs.db"}
JUNK_PREFIXES = ("._", "~$", ".~")
JUNK_SUFFIXES = (".tmp", ".swp", ".swo", ".crdownload", ".part", ".download")


def is_junk_file(name: str) -> bool:
    """是否为系统/同步垃圾文件（按 basename 判定）。"""
    if name in JUNK_BASENAMES:
        return True
    if name.startswith(JUNK_PREFIXES):
        return True
    return any(name.lower().endswith(s) for s in JUNK_SUFFIXES)


# ── 笔记角色判定：content（正文）/ template（模板）/ archive（归档快照）──
# 判定的意义：模板里的 [[占位符]] 必然断链、模板本身必然无入链，
# 这些不是「库的病」，报出来只会淹没真问题。
TEMPLATE_DIR_HINTS = {"template", "templates", "_templates", "模板", "模板库", "模版"}
TEMPLATE_NAME_HINTS = ("template", "_tpl", "模板", "模版")

ARCHIVE_DIR_HINTS = {"archive", "archives", "_archive", "归档", "归档区", "历史", "backup", "backups"}
ARCHIVE_NAME_HINTS = ("快照", "snapshot", "归档", "archive", "backup", "备份",
                      "副本", "copy", "旧版", "deprecated", "-bak", "_bak")

# 索引/入口页：MOC、库规、Home、README 这类页面天然是「链接的起点」，自己很少被链，
# 报成孤儿是错的——它是库的目录页，不是内容孤儿。
INDEX_NAME_HINTS = ("库规", "总览", "导航", "索引", "目录页", "工作台", "首页",
                    "index", "home", "readme", "moc", "map of content", "dashboard")

# 时间流笔记（日记/周报）：按日期命名，天然无入链。用户的库越大这类越多，
# 若计入孤儿会让孤儿数虚高数倍——这是真实规模下最大的噪音来源。
#
# ⚠️ 第一轮压测后修正（v1.2）：
#   早期实现只要「文件名以日期开头」就判日记，结果 Zettelkasten 用户用
#   「2026-09-01 闪念1.md」这种日期前缀命名原子卡片时，全部被误判成日记，
#   孤儿被静默隐藏（ZK 压测召回率只有 73.3%）。
#   现在只有三种情况才算日记：
#     ① 名字**就是**日期（可带星期）—— 2026-01-01 / 2026-01-01 周一
#     ② 名字里明确写了 日记/日志/daily —— 2026-01-01 日记
#     ③ 所在目录整体是时间流（≥5 篇且 ≥50% 是日期名）—— 目录证据，不是单文件猜测
DAILY_DATE_PREFIX_RE = re.compile(r"^\d{4}[-_.年]\d{1,2}[-_.月]\d{1,2}")
DAILY_DATE_ONLY_RE = re.compile(
    r"^\d{4}[-_.年]\d{1,2}[-_.月]\d{1,2}日?"
    r"(\s*(周[一二三四五六日天]|星期[一二三四五六日天]))?$")
DAILY_NAME_HINTS = ("日记", "日志", "周报", "月报", "日报", "daily", "journal", "diary")



def looks_like_index(relpath: str, fm=None) -> bool:
    """是否索引/入口页（MOC、库规、Home、README）。"""
    parts = _path_parts(relpath)
    if not parts:
        return False
    stem = os.path.splitext(parts[-1])[0].lower()
    if any(h in stem for h in INDEX_NAME_HINTS):
        return True
    if isinstance(fm, dict):
        t = fm.get("type")
        if isinstance(t, str) and t.strip().lower() in ("moc", "index", "rule", "库规"):
            return True
    return False


def looks_like_daily(relpath: str, fm=None, daily_dirs=(), date_stream_dirs=()) -> bool:
    """是否时间流笔记。
    判定依据从强到弱：配置目录 → frontmatter → 名字含日记词 → 名字就是日期 →
    目录名含日记词 → 目录整体是时间流（日期前缀名才算）。
    注意：单纯「日期前缀」不足以判日记（见上方 DAILY_DATE_PREFIX_RE 注释）。
    """
    parts = _path_parts(relpath)
    if not parts:
        return False
    dir_rel = "/".join(parts[:-1])
    stem = parts[-1].rsplit(".", 1)[0] if "." in parts[-1] else parts[-1]

    # ① .obsidian/daily-notes.json 声明的目录（最权威）
    for dd in daily_dirs:
        dd = dd.strip("/").replace("\\", "/")
        if dd and (dir_rel == dd or dir_rel.startswith(dd + "/")):
            return True
    # ② frontmatter 明确声明
    if isinstance(fm, dict):
        t = fm.get("type")
        if isinstance(t, str) and t.strip().lower() in ("daily", "journal", "diary", "日记"):
            return True
    low = stem.lower()
    # ③ 名字里明确写了「日记/日志/周报/日报」
    if any(h in low for h in DAILY_NAME_HINTS):
        return True
    # ④ 名字就是日期（可带星期）
    if DAILY_DATE_ONLY_RE.match(stem):
        return True
    # ⑤ 目录名本身就是日记目录
    for d in parts[:-1]:
        if any(h in d.lower() for h in ("日记", "日志", "daily", "journal")):
            return True
    # ⑥ 目录整体是时间流：此时日期前缀名也算日记（如「2026-01-01 项目启动」）
    if dir_rel in set(date_stream_dirs) and DAILY_DATE_PREFIX_RE.match(stem):
        return True
    return False


def detect_date_stream_dirs(note_relpaths, min_count=5, min_ratio=0.5):
    """找出「整体是时间流」的目录：该目录下 ≥min_count 篇且 ≥min_ratio 是日期命名。
    用途：只有目录证据足够时，才把「日期前缀 + 任意标题」认作日记；
    单篇日期前缀文件（ZK 的原子卡片）不因此被判成日记。"""
    per = {}
    for rel in note_relpaths:
        parts = _path_parts(rel)
        if not parts:
            continue
        d = "/".join(parts[:-1])
        stem = os.path.splitext(parts[-1])[0]
        c, t = per.get(d, (0, 0))
        per[d] = (c + (1 if DAILY_DATE_PREFIX_RE.match(stem) else 0), t + 1)
    return {d for d, (c, t) in per.items() if t >= min_count and c / t >= min_ratio}


def _path_parts(relpath: str):
    return [p for p in relpath.replace("\\", "/").split("/") if p]


def _strip_dir_prefix(name: str) -> str:
    """去掉目录名的排序前缀：`99-模板` → `模板`，`_素材` → `素材`，`03 模板库` → `模板库`。
    真实用户几乎都用数字前缀做排序，精确匹配目录名会大面积失效。"""
    s = name.strip().lower()
    s = re.sub(r"^[\d\s\-_.、]+", "", s)   # 前导数字/分隔符
    s = re.sub(r"^[_\-]+", "", s)          # 前导下划线（`_templates`）
    return s


# 模板正文里的模板语法标记（Templater / 核心模板插件）：出现即基本可断定是模板
TEMPLATE_SYNTAX_RE = re.compile(
    r"\{\{\s*(date|time|title|yesterday|tomorrow|tp\.|date:|文件名|日期)\b", re.IGNORECASE)


def has_template_syntax(text: str) -> bool:
    """正文含 {{date}} / {{title}} / {{tp.xxx}} 这类模板变量。"""
    if not text:
        return False
    head = text[:3000]  # 只看开头，避免大文件全扫
    return bool(TEMPLATE_SYNTAX_RE.search(head))


def looks_like_template(relpath: str, fm=None, template_dirs=(), text=None) -> bool:
    """是否模板。
    优先级：① .obsidian 配置声明的模板目录（最权威，含 Templater 插件）
            ② 目录名/文件名含 template/模板 字样（先去掉 99- 这类排序前缀）
            ③ frontmatter 标记（type: template / template: true / tags 含 template）
            ④ 正文含 {{date}}/{{tp.xxx}} 模板语法
    """
    rel = relpath.replace("\\", "/")
    for td in template_dirs:
        td = td.strip("/").replace("\\", "/")
        if td and (rel == td or rel.startswith(td + "/")):
            return True
    parts = _path_parts(rel)
    for d in parts[:-1]:
        norm = _strip_dir_prefix(d)
        if norm in TEMPLATE_DIR_HINTS or d.lower() in TEMPLATE_DIR_HINTS:
            return True
    stem = os.path.splitext(parts[-1])[0].lower() if parts else ""
    if any(h in stem for h in TEMPLATE_NAME_HINTS):
        return True
    if isinstance(fm, dict):
        t = fm.get("type")
        if isinstance(t, str) and t.strip().lower() in ("template", "模板"):
            return True
        if fm.get("template") is True or str(fm.get("template", "")).lower() == "true":
            return True
        tags = fm.get("tags")
        tag_list = tags if isinstance(tags, list) else ([tags] if isinstance(tags, str) else [])
        if any(str(x).strip().lower() in ("template", "模板") for x in tag_list):
            return True
    if text and has_template_syntax(text):
        return True
    return False


def read_templater_dirs(vault: str):
    """读 Templater 插件配置里的模板目录（.obsidian/plugins/templater-obsidian/data.json）。
    Templater 是 Obsidian 最常用的模板插件之一，配置位置与核心插件不同。"""
    cfg = os.path.join(vault, ".obsidian", "plugins", "templater-obsidian", "data.json")
    if not os.path.isfile(cfg):
        return []
    try:
        import json
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        folder = data.get("templates_folder")
        return [folder] if isinstance(folder, str) and folder.strip() else []
    except Exception:
        return []


def read_daily_dirs(vault: str):
    """读 .obsidian/daily-notes.json（核心「日记」插件）配置的日记目录。
    有配置就以配置为准，不再靠文件名猜——这是最可靠的时间流证据。"""
    cfg = os.path.join(vault, ".obsidian", "daily-notes.json")
    if not os.path.isfile(cfg):
        # 周期性笔记（Periodic Notes 插件）也常存日记
        cfg2 = os.path.join(vault, ".obsidian", "plugins", "periodic-notes", "data.json")
        if not os.path.isfile(cfg2):
            return []
        cfg = cfg2
    try:
        import json
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = []
        if isinstance(data.get("folder"), str) and data["folder"].strip():
            out.append(data["folder"])
        for key in ("daily", "weekly"):
            sub = data.get(key)
            if isinstance(sub, dict) and isinstance(sub.get("folder"), str) and sub["folder"].strip():
                out.append(sub["folder"])
        return out
    except Exception:
        return []


def looks_like_archive(relpath: str, fm=None) -> bool:
    """是否归档/快照类文件（这类文件缺 frontmatter 属正常，不该报成问题）。"""
    parts = _path_parts(relpath)
    for d in parts[:-1]:
        if d.lower() in ARCHIVE_DIR_HINTS:
            return True
    stem = os.path.splitext(parts[-1])[0].lower() if parts else ""
    if any(h in stem for h in ARCHIVE_NAME_HINTS):
        return True
    if isinstance(fm, dict):
        st = fm.get("status")
        if isinstance(st, str) and st.strip().lower() in ("archived", "归档", "deprecated"):
            return True
    return False


def read_template_dirs(vault: str):
    """读用户配置的模板目录：核心「模板」插件 + Templater 插件，两者都读。
    读不到就返回空列表——不报错，降级到启发式判定。"""
    dirs = []
    cfg = os.path.join(vault, ".obsidian", "templates.json")
    if os.path.isfile(cfg):
        try:
            import json
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f)
            folder = data.get("folder")
            if isinstance(folder, str) and folder.strip():
                dirs.append(folder)
        except Exception:
            pass
    for d in read_templater_dirs(vault):
        if d not in dirs:
            dirs.append(d)
    return dirs


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_title(name: str) -> str:
    """标题归一化：去扩展名、小写、去空格/连字符/下划线。用于 dup 与重名检测。"""
    name = re.sub(r"\.[^.]+$", "", name)
    name = name.lower()
    name = re.sub(r"[\s\-_]+", "", name)
    return name


def split_out_code_blocks(text: str) -> str:
    """返回 fenced code block 外的文本（``` 围栏内视为示例，不参与链接解析）。
    规则：``` 成对切分，偶数段（索引 0,2,4...）为代码块外。"""
    parts = text.split("```")
    return "\n".join(parts[0::2])


def classify(path: str) -> str:
    """按扩展名分类文件：note / canvas / attachment / other。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".md":
        return "note"
    if ext == ".canvas":
        return "canvas"
    if ext in ATTACHMENT_EXTS:
        return "attachment"
    return "other"


def parse_frontmatter(text: str):
    """解析 YAML frontmatter（--- 包裹）。返回 dict；无则返回 None。
    零依赖：仅支持简单 key: value、单行列表 [a, b]、布尔值。"""
    if not text.startswith("---"):
        return None
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.DOTALL)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [x.strip().strip("\"'") for x in v[1:-1].split(",") if x.strip()]
        elif v in ("true", "false"):
            v = (v == "true")
        else:
            v = v.strip("\"'")
        fm[k] = v
    return fm


# wikilink：![[target#anchor|alias]]
WIKILINK_RE = re.compile(r"(!?)\[\[([^\[\]\n]+?)(?:\|([^\[\]\n]+))?\]\]")
# markdown 链接：[text](path) 或 ![alt](path)
MDLINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)")


def extract_links(text: str):
    """提取代码块外的所有链接。返回 list[dict]。
    每条含 kind / raw / target / anchor / alias / is_embed。"""
    outside = split_out_code_blocks(text)
    links = []

    for m in WIKILINK_RE.finditer(outside):
        is_embed = m.group(1) == "!"
        target = m.group(2)  # 保留完整（含 #）：文件名的 # 与 heading/block 的 # 在 resolve 阶段区分
        alias = m.group(3)
        anchor = target.split("#", 1)[1] if "#" in target else None
        # kind 分类优先级：block > heading > embed > alias > wikilink
        if anchor and anchor.startswith("^"):
            kind = "block"
        elif anchor:
            kind = "heading"
        elif is_embed:
            kind = "embed"
        elif alias:
            kind = "alias"
        else:
            kind = "wikilink"
        links.append({
            "kind": kind, "raw": m.group(0), "target": target,
            "anchor": anchor, "alias": alias, "is_embed": is_embed,
        })

    for m in MDLINK_RE.finditer(outside):
        is_embed = m.group(1) == "!"
        target = m.group(3)  # 保留完整（含 #）
        anchor = target.split("#", 1)[1] if "#" in target else None
        links.append({
            "kind": "embed" if is_embed else "mdlink",
            "raw": m.group(0), "target": target,
            "anchor": anchor, "alias": None, "is_embed": is_embed,
        })

    return links


def has_known_ext(name: str) -> bool:
    """是否以已知扩展名结尾（.md 或附件扩展名）。区分"真扩展名"与文件名里的点（如 v5.0.2）。"""
    low = name.lower()
    return low.endswith(".md") or any(low.endswith(e) for e in ATTACHMENT_EXTS)


def resolve_target(target, name_index, fullname_index, note_map, all_paths=None):
    """把链接 target 解析为实际文件 path（Obsidian 最短路径规则）。
    返回 (resolved_path, ambiguous)；resolved 为 None 表示断链。
    - 显式路径（含 /）→ 精确匹配（带扩展名精确匹配文件；无扩展名加 .md）
    - 带已知扩展名（.md / 附件扩展名）→ 按完整 basename 最短路径
    - 纯 stem（含"文件名里的点"如 v5.0.2）→ 按 name_index 最短路径；多个同名则 ambiguous=True
    """
    if all_paths is None:
        all_paths = set(note_map.keys())
    target = target.replace("\\", "/")
    if "/" in target:
        if has_known_ext(target):
            # 附件/显式带扩展名路径（如 Assets/Image.png）→ 精确匹配，不加 .md
            if target in all_paths:
                return target, False
            return None, False  # 断链
        # 无扩展名显式路径（如 B/Note）→ 加 .md 精确匹配
        cand = target + ".md"
        if cand in all_paths:
            return cand, False
        return None, False  # 断链
    if has_known_ext(target):
        # 带已知扩展名 → 按完整文件名匹配（附件 img.png / 显式 Note.md）
        paths = fullname_index.get(target, [])
        if paths:
            return min(paths, key=len), (len(paths) > 1)
        return None, False  # 断链
    # 纯 stem
    paths = name_index.get(target, [])
    if not paths:
        return None, False  # 断链
    return min(paths, key=len), (len(paths) > 1)


def resolve_link_target(target, name_index, fullname_index, note_map, all_paths=None):
    """解析含可能 #anchor 的链接 target。
    先按完整 target 当文件名解析（文件名可能含 #，如 "Note #1"）；
    失败再拆 # 解析 base 部分。
    返回 (base, anchor, resolved_path, ambiguous)。
    """
    target = target.replace("\\", "/")
    r = resolve_target(target, name_index, fullname_index, note_map, all_paths)
    if r[0] is not None:
        return target, None, r[0], r[1]
    if "#" in target:
        base, anchor = target.split("#", 1)
        r2 = resolve_target(base, name_index, fullname_index, note_map, all_paths)
        return base, anchor, r2[0], r2[1]
    return target, None, None, False
