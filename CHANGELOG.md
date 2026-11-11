# Changelog

## 0.3.0 — 2026-11-11

从"能把你的字排成整页"到"能选风格、能量风格、能验收"。

### 新增

- **预制字体目录**：`hwpdf fonts` 列出三款 OFL-1.1 手写字体（志莽行书、马善政行楷、龙藏行草），`--fetch` 下载并校验，许可写入 `LICENSES.txt`；`hwpdf render --font-name 志莽行书` 直接用。字体只在本机，下载总是显式触发。
- **书写习惯测量**：`hwpdf habits` 量倾角、笔宽、字宽与抖动，把"排版体 ≈ 0.35、真实行书 1.5–2.0"映射成 `--jitter`；`profile save` 会把测量结果存进 profile。
- **`synth` 第二引擎**（实验）：`stylefit` 解析式基线——从你写过的字量出倾斜/笔宽/比例，印到系统骨架上补没写过的字。3755 字约 10 秒、纯 CPU、零下载；质量门、按字缓存（全量重建 8.9 s → 1.5 s，补字 9 s → 1.8 s）、`synth extend` 与 `render --grow` 按需补字、`synth verify` 报覆盖率、`synth eval` 出评测报告、`synth bench` 量单字预算。
- **真实手写基准**：`scripts/bench_handwriting.py` 用 OFL 手写字体的 360 字训练、40 字留出，比较骨架基线 / stylefit / 像素小模型 / 矢量小模型（`train_learned.py`、`train_vecwarp.py`，`--engine external --provider` 接入）。
- **排版层**：逐字倾斜与字号微差、行基线慢起伏；中文禁则换行（不让 `，。）》` 出现在行首，`（《"` 不留在行尾）。
- **双语 README**：`README.md` 中文、`README.en.md` 英文，各自配自己语言的演示图。

### 修复

- 系统字体探测不再硬编码路径（macOS 27 上 PingFang 已不存在）；TTC 的 cmap 按 `fontNumber` 读取，骨架选择不再静默回退。
- 合成字的标点与拉丁降部不再被钉到同一条基线上。
- 中文 family 名（如"我的笔迹"）不再写出非 ASCII 的 PostScript 名导致 reportlab 无法加载。
- CJK 走回退字体时行宽不再溢出右边界；显式 `--fallback` 缺字会明说"将渲染为空白"而不是静默跳过。

### 已知限制

- **没写过的字不像你写的**：`synth` 只带上倾角、笔宽与比例，结构来自系统骨架；macOS 无中文手写骨架，中文只能是"黑体+倾斜"。真实手写留出基准上对齐 IoU 约 0.25–0.37（小模型 0.28–0.37），离可用还有距离。
- 一字一形：无连笔衔接、同字异形、笔压与墨色浓淡。
- 少样本扩散模型的权重许可多为空白（见 `PLAN.md` §8 S4），因此不随包分发。

## 0.2.0 及更早

见 `git log` 与 `PLAN.md` §8。
