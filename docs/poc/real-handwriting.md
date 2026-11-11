# 真实手写基准：合成代理骗了我们，真实字体不会

前面的对照实验（`learned.md`）用"系统字体 + 确定性变换"当写手，结论是几个候选都分不出高下。
后来发现原因：**合成写手的"风格"就是一个我们能精确拟合的变换**，而且 stylefit 报出的 0.95
其实是"照抄你写过的字"（verbatim copy），不是泛化。要判"能不能推断没写过的字"，只能拿
**真有全部字形的真实手写**来测。

## 数据：三套 OFL 手写字体

| 字体 | 风格 | 汉字数 | 许可 |
|---|---|---|---|
| [Zhi Mang Xing](https://github.com/google/fonts/tree/main/ofl/zhimangxing) | 行书，中等笔压 | 6763 | OFL-1.1 |
| [Ma Shan Zheng](https://github.com/google/fonts/tree/main/ofl/mashanzheng) | 行楷，较重 | 6763 | OFL-1.1 |
| [Long Cang](https://github.com/google/fonts/tree/main/ofl/longcang) | 细瘦行草 | 6763 | OFL-1.1 |

OFL 允许下载、修改、嵌入与再分发（保留许可声明即可），所以它们可以进测试集，但不能当"我们发布的模型权重"。

```bash
mkdir -p build/hwfonts
for f in zhimangxing/ZhiMangXing-Regular mashanzheng/MaShanZheng-Regular longcang/LongCang-Regular; do
  curl -fsSL "https://raw.githubusercontent.com/google/fonts/main/ofl/$f.ttf" -o "build/hwfonts/$(basename $f).ttf"
done
```

## 协议

1. 取字体里前 **360** 个常用字当"你写过的字"，裁成一个只有这些字形的字体（`fontTools.subset`）；
2. 后 **40** 个字留作测试——真值就来自原字体，任何方法都没见过；
3. 各方法用那 360 个字造出 40 个测试字，与真值比 **aligned IoU**（先把墨迹底边中点对齐，
   否则字体度量差异会主导分数）；
4. **骨架基线**：直接拿系统黑体渲染同样的字。一个方法如果落在基线以下，说明它连"字是手写的"
   都没学到。

```bash
python scripts/bench_handwriting.py build/hwfonts/ZhiMangXing-Regular.ttf --size 64
```

## 结果（40 个留出字，aligned IoU）

| 字体 | 骨架基线 | stylefit | 小模型 64px | 小模型 128px |
|---|---|---|---|---|
| Zhi Mang Xing | 0.213 | 0.218 | **0.271** | 0.244 |
| Ma Shan Zheng | 0.276 | 0.242 | **0.325** | — |
| Long Cang | 0.222 | 0.187 | 0.151（仅 10/40 过质量门） | — |

## 三个结论

1. **stylefit 在真实手写面前等于没做事**：0.218 / 0.242 / 0.187，其中两个**低于**骨架基线。
   它是"复制骨架 + 全局倾斜加粗"，而真实手写的差异在笔画内部（粗细变化、连笔、结构变形），
   不在一个仿射矩阵能表达的空间里。
2. **小模型第一次赢过两个基线**（+0.05 ~ +0.06），但也只是"比没学强一点"。0.27–0.33 的 IoU
   意味着形状只能算"有点像"，离可用（0.8+）还很远。这至少证明学习方向是对的：同样的 360 个字，
   它能挤出骨架里没有的信息。
3. **提高分辨率仍然没用**（128px 反而 0.244 < 0.271），和合成代理上的结论一致。瓶颈是**表示**：
   64×64 的像素图把笔画宽度、起收笔这些信息压成了灰度，越快上采样越糊。下一步该换成预测
   **骨架上的位移/宽度**（矢量），而不是猜像素。

## 一个失败模式值得记下来

Long Cang 是细瘦行草，64px 下笔画不到 2 px，小模型学到的输出太淡，**30/40 个字被质量门判为空白**
（`n=10`）。质量门在这里是对的（它拦住了不可用的字），但它也说明：轻笔迹需要更高分辨率的输入
或者笔画级表示，靠放大输出是救不回来的。

## 复现

```bash
# 三套字体
for f in ZhiMangXing MaShanZheng LongCang; do
  python scripts/bench_handwriting.py build/hwfonts/$f-Regular.ttf --size 64
done

# 128px 对照
python scripts/bench_handwriting.py build/hwfonts/ZhiMangXing-Regular.ttf --size 128
```
