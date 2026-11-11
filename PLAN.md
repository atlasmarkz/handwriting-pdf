# PLAN — 少样本手写风格迁移（few-shot handwriting style transfer）

> 当前状态：v0.1 已完成 —— 逐字矢量化（用户写过的字 → TTF → PDF），可用但需要写全目标字符。
> 本文件规划下一步：**只给少量字迹，推断其余全部字形**。
> 立项时间：2026-11-11

## 0. 结论先行：要不要"后训练/微调"

**要模型，但不一定是"微调大模型"。** 分两条路线：

| 路线 | 做法 | 需要用户训练？ | 成本 | 质量 |
|---|---|---|---|---|
| **A. 推理型（推荐先做）** | 用已在字体数据集上训练好的**少样本字形生成模型**（FontDiffuser / Diff-Font / HWT），把用户写的 10–50 个字当 style reference，推理生成其余字形 | 否（模型本身是训过的） | 中（推理 GPU 6–8GB） | 结构较稳，但偏"字体感"，手写细节少 |
| **B. 个人适配型** | 在 A 基础上对个人风格做 per-user 微调（LoRA / 风格编码器） | 是（10–30 字 + 10–30 分钟 GPU） | 高（训练 GPU ≥ 12GB） | 更贴个人习惯（倾斜、重心、连笔倾向） |

**纯规则/prompt 做不到**：现有 hwpdf 只能把"用户写过的字"矢量化，没写过的字无从来处；字符是离散集合，不存在可插值的中间态。

**推荐路径：A 出图 → 复用现有矢量化管线转 TTF → 现有 PDF 引擎排版。** 这样生成的字形同样能进 profile，支持抖动/纸张/墨色，与 v0.1 兼容。

## 1. 现有基线（v0.1，已完成）

- 三条入口：练习纸拍照 / 自由照片 / PDF（嵌入字体、文字层收割、扫描页）；
- 逐字取形 → 轮廓矢量化 → 组装 TTF（真实 outline，逐字保真）；
- profile 固化（字体 + 版式默认值 + `PROMPT.md`）；
- 局限：中文正文需逐字写（30 格/页，300 字 ≈ 10 页）。

## 2. 候选模型对比（2026-11 实查 GitHub API）

| 模型 | 星 | License | 最后更新 | 语言 | 输入 | 训练需求 |
|---|---|---|---|---|---|---|
| [FontDiffuser](https://github.com/yeungchenwa/FontDiffuser) | 579 | **无** | 2024-03 | 中/英 | 1 张 style 图 + 内容字 | 推理即可（需自取权重） |
| [Diff-Font](https://github.com/Hxyz-123/Font-diff) | 142 | **无** | 2023-09 | 中文 | 参考字图 + 字符表 | 推理即可（需预训练权重） |
| [DG-Font](https://github.com/ecnuycxie/DG-Font) | 251 | **无** | 2024-01 | 中文 | 字体类别风格 | 需训练 |
| [HWT](https://github.com/ankanbhunia/Handwriting-Transformers) | 267 | **无** | 2024-02 | 英文 | 文本 + 若干单词样张（few-shot） | 推理即可（IAM 预训练） |
| [FS-Font](https://github.com/tlc121/FsFont) | 84 | 未核实 | 2023-06 | 中/英 | few-shot 参考字 | 需训练 |
| [zi2zi](https://github.com/kaonashi-tyc/zi2zi) | 2755 | Apache-2.0 | 2019-08 | 中文 | 按字体训练 | 需训练 |

> ⚠️ **许可风险**：上表除 zi2zi 外基本没有 license 文件（`license.spdx_id` 为空）。可以本地做实验，**不能随产品分发权重/代码**。产品化前必须换成有明确许可的模型，或自训（自训还需字体数据集授权，如 Google Fonts / 开源字库的商用条款）。

## 3. 分阶段计划

### P1 选型 PoC（1–2 天）
- [ ] 跑通 FontDiffuser 官方 demo：1 张 style 图（用户 26 字母样张）+ 30 个字符 → 生成图
- [ ] Diff-Font 做对照
- [ ] 目测：结构崩坏率、笔画粘连、缺字表现
- 产出：`docs/poc/` 对比截图 + 继续/放弃结论

### P2 风格适配管线（3–5 天）
- [ ] 采集规范：英文 26 字母 + 10 数字；中文 30 个高频字起步、100 字推荐
- [ ] 生成图 → 矢量化 → TTF（复用 `scripts/vectorize.py` + `fontbuild.py`）
- [ ] 质量阈值：不达标的字形回退系统楷体并告警（沿用现有 fallback 机制）
- 验收：用生成字库排一段 100 字中文/英文，与用户真实字迹页并排对比

### P3 个人微调（1–2 周，需 GPU ≥ 12GB）
- [ ] per-user 微调脚本（LoRA / 风格编码器），输入 10–30 字
- [ ] 记录训练耗时 / 显存 / 效果，与 P2 做 A/B
- [ ] 判定：质量提升是否值得成本（否则砍掉，只做 A）

### P4 集成 hwpdf（3–5 天）
- [ ] 新命令：`hwpdf synth --style hw/style.png --charset common3500 -o hw/font.ttf`
- [ ] profile 增加 `type: synth`，记录 style 图 hash + 生成参数（可复现）
- [ ] 缓存：常用 3500 字预生成；缺字按需生成
- [ ] GPU/CPU 双路推理，CPU 下限制分辨率或排队

### P5 评测体系（与 P2 并行）
- 自动指标：结构相似度（SSIM / LPIPS）、缺字率、渲染后 12pt 可读性
- 人工指标：可读性 1–5 分、盲测"像不像本人"（3 人 2/3 通过）
- 通过线：可读性 ≥ 4/5、缺字率 ≤ 1%
- 参照物：用户提供的真实手写页（如线代笔记那页）

### P6 发布与合规
- [ ] 权重托管（HuggingFace）与许可核查；无许可模型不随仓库分发
- [ ] 隐私：样张本地处理，默认不上传
- [ ] 合规红线：禁止生成他人笔迹/签名，下载即提示

## 4. 待你确认的设计决策

1. **生产路径**：只做"生成图 → 矢量化 → TTF"（推荐，兼容现有 profile/排版），还是直接生成整页 PDF？
2. **语言优先级**：英文先（HWT 成熟、英文 52 字符就够）还是中文先（价值高、难度大）？
3. **交付形态**：并进 `hwpdf`（单 skill，体积变大）还是独立 `hwpdf-synth` 插件（GPU 依赖重、单独安装）？
4. **算力**：你的机器有独立显卡吗？（决定 P3 是否可行）

## 5. 风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 质量 | 复杂汉字（墨/藏/赢）结构崩坏率高 | 抽检 + 阈值回退；常用字优先 |
| 许可 | 候选模型多数无 license | 只做本地实验；产品化换许可明确者或自训 |
| 期望管理 | 少样本是"风格近似"，不是逐笔复刻 | 明确告知；与 v0.1 逐字路线并存 |
| 成本 | GPU 显存、推理时间 | 预生成 + 缓存；CPU 降级 |

## 6. 产品形态：Skill / 插件 / 服务

结论：**能做，而且现有 skill 结构不用推翻**。关键是让"重模型"成为**可选第二引擎**，并且只跑一次。

| 形态 | 能否承载 synth | 用户成本 | 分发 | 判断 |
|---|---|---|---|---|
| 纯 Skill（v0.1 现状） | 能，但依赖重 | 需自备 Python/GPU | GitHub + `npx skills add` | ✅ 保留为主形态 |
| **Skill + 可选 synth 引擎（推荐）** | 能 | 默认轻量；想要补字才下载模型 | 同上 | ✅ 主推 |
| DSH 插件（npm + `dsh.bundle`） | 能，需起 Python sidecar | 市场一键装，首次下载运行时 | 市场评审 | ✅ 有先例：语音输入 bundle 就是"首次使用时下载运行时" |
| 托管服务（云端 API） | 能，效果最稳 | 无本地依赖 | — | ⚠️ 手写属敏感个人信息，只做可选，默认本地 |

### 分层设计（核心：重模型只跑一次）

- **Tier A（默认，无 GPU）**：练习纸/照片/PDF → 矢量化 → TTF → PDF（即 v0.1，已可用）。
- **Tier B（可选）**：`hwpdf synth doctor` 检查 torch/CUDA/权重；`hwpdf synth build --style hw/style.png --charset common3500` 生成字形 → 复用 `vectorize.py` → TTF → 存进 profile。
- **缓存即交付物**：生成结果落成 TTF + profile（记录 style 图 hash、模型版本、参数），第二次使用零成本、不再依赖模型；与 v0.1 的排版/纸张/墨色/抖动完全共用。

### 分发约束

- 权重不进仓库（候选模型多数无 license）：首次下载需用户确认 + 许可提示 + SHA256 校验；
- skill 仓库保持轻量（<1MB）：`requirements-synth.txt` 单独文件，安装脚本按需装；
- DSH 插件负责"设置页（引擎/模型路径/显存上限）+ 调用本地 CLI + 首次下载引导"，市场条目 category 选 `skill`；
- 插件与 skill 可以同仓共存（市场支持 monorepo 子包条目），不冲突。

## 7. 立即可做的下一步

1. 你提供 26 字母 + 10 数字样张（现有练习纸即可，或从线代笔记里挑干净的字格）；
2. 我跑 P1 的 FontDiffuser / Diff-Font demo，出 30 字样张；
3. 效果可接受 → 进 P2 打通"生成 → TTF → PDF"；否则保留 v0.1 逐字路线。

## 8. 工程实施步骤（S0–S6，逐步交付）

原则：**默认路径不被污染**——v0.1（拍照→矢量化→TTF→PDF）保持零重依赖；synth 是可选第二引擎，所有重依赖惰性导入，缺失时给可执行的修复提示而不是崩溃。

| 步骤 | 目标 | 主要交付物 | 验收 |
|---|---|---|---|
| **S0** 基线对齐 | 让"机器能跑"可复现，修掉静默退化 | `scripts/fonts.py`（系统字体探测+缓存）、doctor 报告实际选中字体、模板按字数校验覆盖率 | `doctor` 显示真实 cjk/latin guide 与 `#index`；中文练习纸渲染出描红而非空白格；中英文 selftest 均通过 |
| **S1** synth 骨架 | 子命令、参数、错误路径先立住，不接真模型 | `scripts/synth.py`（engine 协议 + `stub` 引擎 + `doctor`）、`hwpdf synth ...`、`requirements-synth.txt` | `hwpdf synth doctor` 在无 torch 时给出安装指引并返回非零；`synth build --engine stub` 产出可装载的 TTF |
| **S2** 管线打通 | style 图 → 字形图 → 复用矢量化 → TTF → PDF | `synth.build()`，接 `vectorize.py`/`fontbuild.py`，字表解析（ASCII/common3500/自定义） | 用 stub 引擎跑通端到端，PDF 里出现"生成"字形 |
| **S3** 质量门与缓存 | 坏字形不进字体；重复调用零成本 | 每字质量评分（墨量/包围盒/连通域/结构相似）+ 失败即重采样或回退并告警；缓存 key = style hash + 字表 + 模型版本 | 故意喂空白/噪点图片，坏字被拒并回退，日志列出原因；二次运行命中缓存 |
| **S4** 真实模型 PoC | 验证"像不像本人" | `synth` 引擎 `diff-font`（或 FontDiffuser）适配层 + `docs/poc/` 对比图 | 30 字样张人工盲测，记录可读性/结构崩坏率，给继续或放弃的书面结论 |
| **S5** profile/charset 集成 | 与现有工作流合体 | profile `type: synth`（记录 style hash、引擎、参数）、`--charset` 常用字预生成、缺字按需生成 | `hwpdf render` 直接吃 synth profile；同一 profile 二次渲染不触发推理 |
| **S6** 评测与文档 | 可发布、可复现 | 自动指标脚本（SSIM/缺字率/12pt 可读性）、SKILL.md 增补、许可与隐私说明 | 指标脚本一条命令出报告；文档明确"默认本地、权重不入库" |

当前状态：**S0–S3、S5、S6 完成**，S4 等两件用户决策（用哪个模型/权重许可 + 有无独显），期间用 `stylefit` 解析式基线顶上。S6 交付：`hwpdf synth eval`（一条命令出报告：覆盖率 / 质量门 / 原样复用 IoU / 倾角 / 笔宽）、`tests/test_synth.py`（11 例）、`.github/workflows/ci.yml`、`docs/poc/stylefit.md`、`SKILL.md` 的 synth 章节；12pt 可读性仍是人工目检（`synth specimen` / `preview`），不做自动判分。

**S3 落地细节**：质量门（`GATE`：墨量 1.2%–62%、包围盒 ≥28% 边长、贴边即裁切）+ 失败自动重采样（`--retries`，默认 1，换 seed 重画）；`meta.gate` 记录 passed/rejected/retried/attempts 与阈值；缓存 key = `engine.name.version|size|seed|SCHEMA_VERSION`，引擎 `version` 一改就自动失效；`hwpdf synth verify --font` 报覆盖率；`--no-cache` 用于基准测试。

**S5 落地细节**：`hwpdf profile save --synth-font <ttf>` 把 synth 字体复制进 profile 并记录 engine/style_sha256/gate；之后 `hwpdf render --profile <名字>` 对"没写过的字"自动用 synth 字体（显式 `--fallback` 优先）；profile 目录可用 `HWPDF_PROFILE_DIR` / `HWPDF_HOME` 改位置。

**排版层（2026-11-11，与模型无关）**：`render` 现在给每个字独立的小倾斜（±0.45°）、字号微差（−1.2%…+1.8%）
和一条沿行缓慢起伏的基线（而不是每个字各自乱跳）；换行加了中文禁则——行首不出现 `，。、；：！？…` 等、
行尾不留 `（《「『` 等。这两项无论最后用 stylefit 还是真模型都用得上，所以先做。默认值在 `--jitter 1.0`，
设 0 得到完全平直的排版。

**S4 的接入面（已完成）**：模型不再需要改 hwpdf 的代码——写一个 `generate(style, chars, size=, seed=) -> {字: 掩码}`
的适配器，用 `hwpdf synth build --engine external --provider my_model.py` 就能复用质量门、缓存与排版，见
`docs/synth-provider.md`。S4 剩下的只是选模型、拿权重、验许可、跑对照，这三件事都等用户决策。

**S4 之前的 `stylefit` 基线（已可用）**：不训练、不下载、纯 CPU——从你写过的字里量出倾角（列投影方差扫描）、笔宽、x 高/字宽比例，再用仿射+形态学把这些参数印到系统骨架字（中文宋体 / 拉丁 Bradley Hand）上；你写过的字直接原样复用。中文无样本时借用拉丁的倾角与笔宽。它是 S4 的对照基线（`--engine stylefit`，`doctor` 里显示 ready），不是风格迁移：笔画粗细变化、连笔、结构变形都不会被学会。


**骨架修正（2026-11-11）**：CJK 骨架默认从宋体改为无衬线（黑体）——统一笔宽更接近钢笔，宋体的三角顿笔斜切后仍是印刷体观感；拉丁仍优先真手写体。起因是发现 `glyphs_of()` 读 `.ttc` 时没传 `fontNumber`，fontTools 返回空 cmap，导致**任何 TTC 骨架都被判定为缺字并静默回退到宋体**，骨架选择一直没真正生效。现在 `synth doctor` 会打印 cjk/latin 实际选用的骨架，`HWPDF_SKELETON`/`HWPDF_SKELETON_STYLE` 可覆盖；`stylefit` 版本号升到 4（缓存自动失效），并补了 4 个 cmap/骨架相关单测（共 15 个）。