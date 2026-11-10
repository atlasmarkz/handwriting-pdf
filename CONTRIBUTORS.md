# Contributors

## atlasmarkz

- 需求与验收标准：明确"按我的字迹生成 PDF"要的是什么（真笔迹、可复用、本地不上传）；
- 提供字迹样本（手写作业 PDF），逐轮给出观感反馈（粗细、纸型、行距、墨色）；
- 仓库所有者、最终决策与发布。

## DeepSeek AI（deepseek-v4.1-flash，运行于 DeepSeek Harness）

以 AI 协作方身份参与（模型本身没有 GitHub 账号，因此不出现在 Collaborators 列表中，以此文件与 commit trailer 记录贡献）：

- **调研**：横向比对 DSH 插件市场（10,119 条）、skills.sh 生态、开源笔迹合成/少样本字体项目与国内成品工具，确认"没有开箱即用方案"，并给出可组合路线；
- **设计**：确定"练习纸四角定位 → 透视矫正 → 按格取字 → 轮廓矢量化 → TTF → ReportLab 排版"的管线，以及"PDF 三条入口（嵌入字体 / 文字层收割 / 扫描图）"；
- **实现**：`scripts/` 下全部代码（CLI、图像处理、矢量化、字体组装、渲染、PDF 解析、profile 系统），共 14 个子命令；
- **验证**：合成样张 selftest（含模拟拍歪的照片）、中文/拉丁/自由照片/PDF 往返（round-trip）四类测试，PDF 字体子集与 em 溢出等边角问题的修复；
- **文档与分发**：`SKILL.md`、`references/`、`install.ps1`/`install.sh`、README、zip 包，以及双宿主（Codex / DeepSeek Harness）安装验证。

协作方式：每个 commit 由 agent 起草并验证，由仓库所有者复核后合并发布。

## 如何记录后续 AI 协作

后续提交请保留 co-author trailer，便于追溯：

```
feat: ...

Co-authored-by: DeepSeek AI <agent@dsh.local>
```

`agent@dsh.local` 是一个明确非官方的占位地址：模型没有邮箱账号，也不应冒用 DeepSeek 官方域名。GitHub 只有在邮箱绑定账号时才会显示头像/链接，所以这里显示为纯文本署名。
