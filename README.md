<h1 align="center"><img src="docs/logo-mark.png" width="32" align="bottom" hspace="10"> handwriting-pdf</h1>

<p align="center">
  <b>把照片里的字迹，变成一份能打印的手写稿</b><br>
  <sub>照片 / 扫描 / PDF → 你自己的字体（.ttf）→ 排好版的 PDF｜全程本地，不联网｜Codex · DeepSeek Harness skill</sub>
</p>

<p align="center">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-3b82f6?style=flat-square">
  <img alt="python" src="https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square">
  <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-6b7280?style=flat-square">
  <img alt="offline" src="https://img.shields.io/badge/100%25%20local-no%20network-16a34a?style=flat-square">
</p>

<p align="center">
  <img src="docs/hero.png" width="720" alt="用自己写出来的字体排出的一封短信">
</p>
<p align="center"><sub>示例：把 26 个字母做成字体后，用它排一封短信——横线纸、逐行抖动、墨色可调</sub></p>

## 它解决什么

大多数"手写生成器"只是换一套内置手写字体——你上传的字迹并不参与生成。这里反过来：**把你的手写描摹成矢量轮廓，做成一套真正的 `.ttf`**，再用它排版。每个字都来自你的笔尖。

- **真笔迹** —— 逐字取形、矢量化，不是字体换皮；缺字会明确告警，不会偷偷塞别的字体。
- **可复用** —— 字体可固化成 profile（字体 + 版式默认值 + 提示词），下次一句话复现同一套字。
- **全本地** —— 不联网、不上传；样张、字体、生成的 PDF 只留在你机器上。

## 三步出稿

```powershell
hwpdf template --text-file letter.txt -d hw            # 1) 按正文"去重后的字"生成练习纸
hwpdf scan hw/page1.jpg -d hw                          # 2) 拍照 → 四角定位 → 透视矫正 → 按格取字
hwpdf font -d hw --family "My Hand" -o hw/font.ttf     # 3) 矢量化成 TrueType 字体
hwpdf render --font hw/font.ttf --text-file letter.txt -o hw/letter.pdf
```

<p align="center">
  <img src="docs/sheet-card.png" width="520" alt="自动生成的练习纸：浅灰描红字 + 四角定位块">
</p>
<p align="center"><sub>练习纸由工具生成：浅灰描红字 + 整页四角的黑色定位块（拍照歪了自动矫正）；格子与字符一一对应，所以不会认错字</sub></p>

打印要点：**A4 / 100%（实际大小）**，深色笔（0.5 mm+），每格一个字，照片里四角黑块要完整。

## 三种输入，按可靠性排序

| 输入 | 命令 | 说明 |
|---|---|---|
| **练习纸照片**（推荐） | `scan` | 格子坐标 ↔ 字符一一对应，没有识别环节，不会错字 |
| 已有字迹的 PDF | `extract-font` / `pdf-specimen` | 先尝试抠出嵌入字体；没有就按"文字层给标签 + 页面渲染给字形"自动收割 |
| 随手写的照片 | `segment` + `label` | 不用打印；但连笔/粘连的字切分困难，需要人工核对 |

## 安装

```bash
npx skills add atlasmarkz/handwriting-pdf@handwriting-pdf -g -y
```

或克隆后本地安装（建私有 venv、生成启动器、复制进两个宿主目录）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1            # DSH + Codex
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Target dsh # 只装 DSH
```

```bash
bash install.sh            # macOS / Linux，默认两个目录都装
```

## 常用命令

| 命令 | 作用 |
|---|---|
| `hwpdf doctor` | 检查依赖与系统字体 |
| `hwpdf template --text-file doc.txt -d hw` | 生成练习纸（PDF + 元数据） |
| `hwpdf scan IMG... -d hw [--append]` | 拍照 → 字形；`--append` 可事后补字 |
| `hwpdf font -d hw [--weight -2..2]` | 组装字体，可调笔画粗细 |
| `hwpdf coverage --font f --text-file doc.txt` | 列出缺字 |
| `hwpdf render --font f --text-file doc.txt -o out.pdf` | 出 PDF（`--paper`、`--jitter`、`--ink`、`--size`） |
| `hwpdf profile save --name me --font f` | 固化字迹 profile，附 `PROMPT.md` |
| `hwpdf preview --pdf out.pdf -d dir` | PDF 转 PNG，便于目检 |

## 限制

- **一字一形**：没有连笔衔接、同字异形、笔压与墨色浓淡；行距/基线抖动是模拟的。
- **字库只覆盖你写过的字**：中文正文按去重字数逐字写，每页 30 格；缺字用系统楷体兜底并告警。
- 生成的 PDF 文字层可搜索/复制（嵌入的是真字体）。
- 仅供本人字迹的个人文稿使用；不用于签名、合同、票据、证件。

## License

MIT
