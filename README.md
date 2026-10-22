# Obsidian 专家产品 · 工程基线

> 目标：产出可上架市场盈利的 Obsidian 产品三件套 —— **Skill（引擎底座）→ 专家（单 agent）→ 专家团（team）**。
> 铁律：从下往上做，上层只引用已锁定的下层。引擎没做完，专家就是空壳。

---

## 一、目录规范（四类包统一骨架）

```
obsidian-expert-product/
├── README.md              ← 本文件（目录规范 + 版本号方案）
├── pack.py                ← 打包脚本（copy + hash 校验 + zip）
├── src/                   ← 单源开发（唯一编辑处）
│   ├── engine/            ← obsidian-vault-engine 引擎 skill（M1–M3 产出）
│   │   ├── SKILL.md
│   │   └── scripts/
│   │       ├── scan.py      （M1）
│   │       ├── health.py    （M1）
│   │       ├── dup.py       （M2）
│   │       ├── move.py      （M2）
│   │       ├── verify.py    （M2）
│   │       └── restore.py   （M2）
│   ├── onboarding-kit/    ← A 专用 kit skill（M4，模板 + 3 看板）
│   └── restructure-planner/ ← B 专用 kit skill（M5，诊断 + ABC 分级 + 迁移协议）
├── experts/               ← 专家包源码（M4–M5 产出）
│   ├── obsidian-starter/  ← 专家 A（建库启动，agent 型）
│   └── obsidian-rescuer/  ← 专家 B（库治理重建，agent 型）
└── dist/                  ← 打包产物（自包含副本，只读，勿手改）
```

### 引擎 skill 结构（engine）
```
engine/
├── SKILL.md              # 必需：name/description/frontmatter + 操作流程
└── scripts/*.py          # 纯 Python 标准库，零第三方依赖
```

### 专家包结构（agent 型，参考本机 company-gm）
```
专家名/
├── .codebuddy-plugin/plugin.json   # 元数据（name/version/agents/expertType/displayName/tags/quickPrompts）
├── README.md
├── agents/{name}.md                # 专家 prompt 本体
├── avatars/expert.png              # 256px 头像
└── skills/engine/                  # ← 内嵌引擎副本（自包含分发铁律）
    ├── SKILL.md
    └── scripts/*.py
```

### 专家团结构（team 型，参考 obsidian-knowledge-governance-team）
```
专家团名/
├── .codebuddy-plugin/plugin.json   # expertType: team，members 含 lead
├── agents/{lead}.md + agents/members/*.md
├── avatars/*.png
└── skills/engine/                  # ← 内嵌引擎副本
```

> ⚠️【待核实】"专家包内嵌 skills/ 子目录、agent md 引用内嵌 skill"的机制，WorkBuddy 平台是否支持，须在 M4 落地前用官方 expert-manager 规范核实。pack.py 的 copy 逻辑不受此影响，先立起来。

---

## 二、版本号方案

- 规则：`MAJOR.MINOR.PATCH`（语义化版本）。
- **引擎与上层包版本解耦**：引擎 `obsidian-vault-engine` 独立发版（0.1.0 起）；专家 A/B、kit 各自独立发版。
- **打包时锁版本**：pack.py 把引擎当前版本写入专家包的 `engine-version.txt`，并做 hash 校验，两份不一致即阻断打包。
- 目录里的版本来源：引擎版本读 `src/engine/SKILL.md` 的 frontmatter `version`；专家包版本读各自 `plugin.json` 的 `version`。

---

## 三、打包流程（pack.py 职责）

```
src/engine（单源）
   │  copy（自包含分发）
   ▼
experts/{专家}/skills/engine（内嵌副本）
   │  hash 校验（src vs 副本一致）
   ▼
dist/{专家}.zip（解压即用、零外部依赖）
```

1. `copy_engine()`：把 `src/engine` 递归复制到每个专家包的 `skills/engine`。
2. `verify_hash()`：逐文件 sha256 对比 src 与副本，任一不一致 → 报错阻断。
3. `package()`：把专家包打成 zip 到 `dist/`。

---

## 四、M0 验收标准

- [ ] pack.py 空包跑通：无引擎时提示"引擎未构建"并安全退出。
- [ ] 放入一个占位 `SKILL.md` 后，copy + hash 校验 + zip 全流程跑通。
- [ ] 产物 zip 解压后目录结构与规范一致，零外部依赖。
