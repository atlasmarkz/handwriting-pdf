# 接入你自己的模型（synth provider 协议）

`hwpdf synth` 自带两条路：`stylefit`（解析式基线，纯 CPU）和 `diff-font`（模型位，等权重与许可）。
如果你手上有一个任意的手写生成模型——Diff-Font、FontDiffuser、自己训的 LoRA、甚至一次 API 调用——
不用改 hwpdf 的代码，写一个 20 行的适配器文件即可复用整条管线（质量门、缓存、TTF 组装、排版 PDF）。

## 协议

适配器就是一个普通的 `.py` 文件，暴露一个函数：

```python
def generate(style, chars, size=320, seed=0):
    """返回 {字符: 掩码}。

    style  你交给 hwpdf 的样例路径（字体或图片），可能为 None
    chars  要生成的字（无重复，含标点）
    size   掩码边长，输出必须是 size×size
    seed   随机种子，同参数应当可复现
    """
```

掩码是二维数组，**非零即墨**（`numpy`、PIL 图都可以；彩色会被转灰度）。
坐标系：左上角原点，整块画布都会被当成字格，hwpdf 负责质量门（太淡/太满/贴边会被拒并重试）
和字体组装，你只管画。

## 例子：把任意模型包成 provider

```python
# my_model_provider.py
import numpy as np
from PIL import Image, ImageDraw

_model = None

def _load():
    global _model
    if _model is None:
        import torch
        _model = torch.load("weights/my-handwriting.pt", map_location="cpu")
        _model.eval()
    return _model

def generate(style, chars, size=320, seed=0):
    m = _load()
    out = {}
    for ch in chars:
        img = m.render(ch, style=style, seed=seed)     # 你自己的调用方式
        mask = np.array(img.resize((size, size)))
        out[ch] = mask > 127
    return out
```

然后：

```bash
hwpdf synth build --engine external --provider my_model_provider.py \
  --style samples/my-handwriting.ttf --charset "春天来了" -o build/my.ttf
```

`--style` 会原样传给 `generate(style, ...)`；模型不需要样例时可以省略。
provider 文件的内容参与缓存 key——改了适配器，缓存自动失效。

## 现成的例子

- `examples/provider_writer.py`：一个确定性的合成写手（倾斜 + 加粗），用来做可复现的对照实验。
- `examples/provider_learned.py`：读取 `scripts/train_learned.py` 训出来的每用户小模型（纯 CPU、无第三方权重）。
  见 `docs/poc/learned.md` 的实验与结论。

## 边界

- hwpdf 不负责模型权重、依赖安装、许可证与显存；这些留在你的环境里（见 `PLAN.md` §8 S4）。
- 生成的掩码会经过同一套质量门；被拒的字会重试，重试仍失败会写进侧车 JSON 的 `rejected`。
- 想直接对比效果：`hwpdf synth specimen --font build/my.ttf --real samples/my-handwriting.ttf`。
