#!/usr/bin/env python3
"""pack.py — Obsidian 专家产品打包脚本（纯 Python 标准库，零第三方依赖）

职责（自包含分发铁律）：
1. 发现 src/ 下所有专家包（以 .codebuddy-plugin/plugin.json 为标志）
2. 把两个 skill（engine + planner）作为内嵌副本复制进每个专家包的 skills/
3. sha256 逐文件校验源与内嵌副本一致（阻断版本漂移）
4. 写 *-version.txt 锁版本（校验之后写，避免污染校验集合）
5. 产出正式版本化包名到 dist/：
   - dist/<专家名>-v<版本>.zip        （专家包，内含两个 skill）
   - dist/obsidian-vault-engine-v<版本>.zip        （独立 skill 包）
   - dist/obsidian-restructure-planner-v<版本>.zip （独立 skill 包）
6. 洁净检查：包内不允许 __pycache__ / *.pyc / .DS_Store
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(BASE, "src")
DIST_DIR = os.path.join(BASE, "dist")

# 内嵌进专家包的 skill 清单：目录名 -> src 下的路径
SKILLS = {
    "obsidian-vault-engine": os.path.join(SRC_DIR, "engine"),
    "obsidian-restructure-planner": os.path.join(SRC_DIR, "restructure-planner"),
}

FORBIDDEN_IN_ZIP = ("__pycache__", ".DS_Store", ".pyc")


def sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dir_hashes(root):
    result = {}
    for dirpath, _, filenames in os.walk(root):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            result[rel] = sha256_of_file(full)
    return result


def read_version(skill_md_path):
    """从 SKILL.md frontmatter 解析 version 字段，缺省 0.0.0。"""
    with open(skill_md_path, "r", encoding="utf-8") as f:
        text = f.read()
    if text.startswith("---"):
        block = text.split("---", 2)[1]
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip("\"'")
    return "0.0.0"


def copy_dir(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def verify_copy(src, dst):
    src_h, dst_h = dir_hashes(src), dir_hashes(dst)
    if src_h == dst_h:
        return True, None
    return False, set(src_h.items()) ^ set(dst_h.items())


def find_experts():
    """src/ 下含 .codebuddy-plugin/plugin.json 的目录视为专家包。"""
    out = []
    if not os.path.isdir(SRC_DIR):
        return out
    for d in sorted(os.listdir(SRC_DIR)):
        pj = os.path.join(SRC_DIR, d, ".codebuddy-plugin", "plugin.json")
        if os.path.isfile(pj):
            out.append(d)
    return out


def clean_check_dir(root):
    """返回包内不应存在的文件相对路径列表。"""
    bad = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in list(dirnames):
            if d in FORBIDDEN_IN_ZIP:
                bad.append(os.path.relpath(os.path.join(dirpath, d), root))
        for fn in filenames:
            if fn == ".DS_Store" or fn.endswith(".pyc"):
                bad.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return bad


def zip_dir(root, zip_path, top_dir=None):
    """打包。top_dir 指定时，包内第一层是 top_dir/（平台上架要求：
    解压后第一层必须是 {skill-name}/ 或 {expert-name}/，不能是散文件）。"""
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _, filenames in os.walk(root):
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root)
                if top_dir:
                    rel = os.path.join(top_dir, rel)
                z.write(full, rel)
    return zip_path


def main():
    ap = argparse.ArgumentParser(description="Obsidian 专家产品打包")
    ap.add_argument("--skip-verify", action="store_true", help="跳过 hash 校验（仅调试用）")
    args = ap.parse_args()

    # 前置校验：两个 skill 必须就绪
    skill_versions = {}
    for name, path in SKILLS.items():
        sm = os.path.join(path, "SKILL.md")
        if not os.path.isfile(sm):
            print(f"❌ skill 未构建：{name} 缺 SKILL.md，中止。")
            sys.exit(1)
        skill_versions[name] = read_version(sm)
    print("✓ skill 就绪：" + "、".join(
        f"{n} v{v}" for n, v in skill_versions.items()))

    experts = find_experts()
    os.makedirs(DIST_DIR, exist_ok=True)
    outputs = []

    # ── 独立 skill 包 ──
    for name, path in SKILLS.items():
        bad = clean_check_dir(path)
        if bad:
            print(f"❌ {name} 内含不洁净文件，先清理：{bad[:5]}")
            sys.exit(1)
        v = skill_versions[name]
        zp = os.path.join(DIST_DIR, f"{name}-v{v}.zip")
        zip_dir(path, zp, top_dir=name)
        outputs.append(zp)
        print(f"  ✓ 独立 skill 包 {os.path.relpath(zp, BASE)}")

    # ── 专家包（内嵌双 skill 副本，在临时构建目录组装，不污染 src）──
    build_root = os.path.join(DIST_DIR, ".build")
    if os.path.exists(build_root):
        shutil.rmtree(build_root)
    os.makedirs(build_root)

    for name in experts:
        expert_dir = os.path.join(SRC_DIR, name)
        print(f"→ 打包专家 [{name}]")

        # 专家版本号与市场名从 plugin.json 读
        with open(os.path.join(expert_dir, ".codebuddy-plugin", "plugin.json"),
                  "r", encoding="utf-8") as f:
            pj = json.load(f)
        expert_version = pj.get("version", "0.0.0")
        market_name = pj.get("name") or name

        # 组装到 staging：src 源目录保持干净
        staging = os.path.join(build_root, name)
        shutil.copytree(expert_dir, staging)

        for skill_name, skill_src in SKILLS.items():
            dst = os.path.join(staging, "skills", skill_name)
            copy_dir(skill_src, dst)
            if not args.skip_verify:
                ok, diff = verify_copy(skill_src, dst)
                if not ok:
                    print(f"❌ hash 校验失败（{skill_name}），阻断打包。不一致文件：")
                    for rel, h in sorted(diff):
                        print(f"    {rel}  {h}")
                    sys.exit(1)
                print(f"    ✓ {skill_name} hash 校验一致（v{skill_versions[skill_name]}）")
            else:
                print(f"    ⚠️ {skill_name} 已跳过 hash 校验")
            # 版本锁在 verify 之后写
            with open(os.path.join(dst, "engine-version.txt"), "w",
                      encoding="utf-8") as f:
                f.write(skill_versions[skill_name] + "\n")

        bad = clean_check_dir(staging)
        if bad:
            print(f"❌ 专家包内含不洁净文件，先清理：{bad[:5]}")
            sys.exit(1)

        zp = os.path.join(DIST_DIR, f"{market_name}-v{expert_version}.zip")
        zip_dir(staging, zp, top_dir=market_name)
        outputs.append(zp)
        print(f"    ✓ 产出 {os.path.relpath(zp, BASE)}")

    shutil.rmtree(build_root, ignore_errors=True)

    print(f"\n✅ 全部完成：{len(outputs)} 个包已产出到 dist/")


if __name__ == "__main__":
    main()
