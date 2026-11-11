# PoC：`stylefit` 解析式基线（S4 之前的对照点）

> 结论先说：`stylefit` 不是风格迁移。它只学四样能测的东西——倾角、笔宽、x 高、字宽比例——把它们印到系统骨架字上。你写过的字原样复用；没写过的字保持骨架字的结构，带上你的倾斜和粗细。

## 它做什么

1. 用你写的字（`--style <你的字体.ttf>`）渲染取样：拉丁 `xnoaehgpds`、中文 `永和九年岁在癸丑上下中今`（取实际存在的）。
2. 列投影方差扫描 −0.30..+0.30 估倾角；墨水比 → 笔宽（腐蚀/膨胀 −1..+2 级）；高宽比 → y/x 缩放（夹在 0.85–1.15）。
3. 对每个没写过的字：系统骨架字（中文宋体 / 拉丁 Bradley Hand）→ 仿射（倾角+缩放+±0.6° 抖动）→ 形态学调粗细 → 出界就整体平移回格内。
4. 你写过的字直接画原字形，不做近似。
5. 中文没有样本时借用拉丁测出的倾角与笔宽（同一个人的手是同一个倾斜），骨架仍是宋体。

## 复现

```bash
# 1) 用你写的字当风格源（这里用 selftest 生成的练习字体当例子）
hwpdf synth build --style build/selftest/font.ttf \
  --charset @build/stylefit/charset-all.txt -o build/stylefit/font-all.ttf --engine stylefit

# 2) 数值评测：覆盖率 / 质量门 / 原样复用 IoU / 倾角 / 笔宽
python scripts/eval_synth.py --font build/stylefit/font-all.ttf \
  --real build/selftest/font.ttf --charset alpha -o build/eval/report-alpha.md

# 3) 目检：左"你写的"、右"推断的"
hwpdf synth specimen --font build/stylefit/font-all.ttf --real build/selftest/font.ttf \
  --chars "Helo Wrd 永和九年" -o build/stylefit/spec.png

# 4) 真正排一页
hwpdf render --font build/selftest/font.ttf --fallback build/stylefit/font-all.ttf \
  --text "The quick brown fox jumps over the lazy dog 0123. 永和九年，岁在癸丑，暮春之初，会于会稽山阴之兰亭。" \
  -o build/stylefit/page-all.pdf --size 24
```

## 2026-11-11 的实测（macOS 27 / M 系，纯 CPU）

| 指标 | 结果 |
|---|---|
| 字库规模 | 3877 字（ASCII + GB2312 一级 3755 + 中文标点），**8.3 s** |
| 质量门 | passed 3877 / rejected 0 / retried 0 |
| 原样复用（你写过的字） | 归一化 IoU **0.94–0.95**（1.0 = 像素级一致；差异来自 TTF 量化与包围盒归一） |
| 倾角 | 真实 **+0.20** → 生成 **+0.16**（拟合把倾斜印上去了） |
| 平均墨水填充 | 真实 **0.463** → 生成 **0.475**（略粗一点） |
| 覆盖率 | ASCII 40/40、GB2312 3755+ 全部 100% |

## 观感（图在 `docs/poc/`）

![你写的 vs 推断的](stylefit-pairs.png)

左格 = 你写的，右格 = 推断的。你写过的字（H/e/l/o/W/r/d）两格一致；中文格是宋体骨架 + 你的倾斜。

![排出来的页面](stylefit-page.png)

拉丁部分是可信的手写观感；中文部分**仍然像宋体**，只是变斜、变粗了一点。

![中文放大](stylefit-cjk-probe.png)

放大看，中文确实带上了倾角和粗细，但笔画形状、连笔、结构重心全是宋体的。

## 为什么它不是 S4

- 学不到**笔画粗细变化**（横细竖粗）、**连笔/牵丝**、**结构变形**（左低右高、部件比例）。
- 中文在没有中文样本时只是"斜的宋体"，离"像我写的"很远。
- 它的价值：① 零下载、零 GPU、确定性，今天就能用；② 给 S4 的扩散模型一个可量化的对照（同样输入，比 IoU/倾角/笔宽之外还要比"像不像"）。

## 已知限制

- 骨架字决定结构：中文换不成楷体/行书就是印刷体观感；`fonts.py` 在 macOS 上能找到的最高分中文字体通常是宋体。
- 单一全局倾角：不区分"竖画倾斜"和"整体斜体"，对楷书这类竖画正直的字体会低估。
- 出界靠平移修正，极端字形（超长竖钩）仍可能被裁。
- 无连笔，字距是独立的（排印时靠 `--tracking` 与抖动补一点自然感）。
