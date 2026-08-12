# Paper2HTML

The independent P2H Package 0.1 command-line validator is documented in
[`VALIDATOR.md`](VALIDATOR.md).

The first executable PDF-to-package baseline is documented in
[`CONVERTER.md`](CONVERTER.md). It performs real native extraction, 300 DPI
page rendering, independent OCR, provenance generation, and validation while
reporting its current semantic and coverage limits explicitly.

Paper2HTML 用于将书籍、期刊论文等出版物——主要是 PDF 格式的内容——转换为标准、结构化且可供程序处理的文档及配套资源，再通过 HTML 与 JavaScript 将其呈现为适合现代设备阅读的内容。

项目希望摆脱 PDF 固定页面布局的限制，让同一份文献能够在电脑、手机及其他不同尺寸的屏幕上获得清晰、舒适且一致的阅读体验。

## 核心目标

- 支持书籍、期刊论文等文献类型，优先处理 PDF 输入。
- 识别并保留标题、章节、段落、列表、脚注等文档结构和正确的阅读顺序。
- 提取并关联图片、公式、表格及其他文档资源。
- 生成标准化、可供程序处理且便于长期扩展的结构化文档。
- 使用 HTML 与 JavaScript 构建响应式阅读前端。
- 忠实保留原始内容、语义结构及资源之间的关系。
- 支持英文原文、译文辅助等现代化互动阅读功能。
- 为未来增加文档类型、解析能力和阅读交互保留扩展空间。

## 总体处理流程

```text
书籍、论文等输入（主要为 PDF）
                │
                ▼
        内容解析与结构识别
                │
                ├── 文本与语义结构
                ├── 图片、公式与表格
                └── 其他关联资源
                │
                ▼
       标准化结构文档及资源
                │
                ▼
       HTML + JavaScript 阅读前端
                │
                ▼
    桌面端、移动端及其他尺寸的屏幕
```

转换的重点不是逐页复制或展示原始 PDF，而是恢复适合重新排版和交互阅读的文档语义，同时保持内容保真和资源可追踪。

## 项目状态

项目目前处于早期规划和基础设施建设阶段。文档格式、解析流程、前端技术方案及对外接口仍将在后续开发中逐步确定。

## 设计原则

- **结构优先：** 输出应表达文档语义，而不仅是页面坐标和视觉布局。
- **内容保真：** 转换结果应忠实于原文，并能够追踪到对应输入和资源。
- **响应式阅读：** 内容应能根据不同设备和屏幕尺寸自然重排。
- **资源可管理：** 图片、公式、表格和附件应具有稳定、明确的关联方式。
- **渐进增强：** 基础内容在简单环境中也应可读，交互功能在此基础上增强体验。
- **长期可扩展：** 中间格式、数据模型和前端组件应便于增加新的文献类型与功能。

## 本地测试数据

大型测试文件不属于项目源代码，不会提交到 Git 仓库。它们统一存放在项目根目录的 `testdata/` 中；该目录已被 `.gitignore` 完整排除。

目录结构如下：

```text
testdata/
├── cases/                         # 长期保留的测试案例
│   ├── papers/                    # 期刊论文
│   │   └── <case-id>/
│   │       ├── input/             # PDF 等原始输入
│   │       ├── expected/          # 人工确认的标准结果
│   │       └── resources/         # 补充材料和附件
│   ├── books/                     # 书籍
│   └── others/                    # 报告、讲义等其他文献
├── runs/                          # 程序实际生成的结果，可随时删除
├── cache/                         # 下载缓存和处理中间文件，可随时删除
└── incoming/                      # 尚未分类整理的新文件
```

每个正式测试案例使用稳定的英文 ID，例如 `paper-two-column-001` 或 `book-multichapter-001`。案例的原始输入、标准结果和补充资源应放在同一个案例目录中。不要使用标题、作者姓名、空格或中文作为案例 ID。

程序生成的实际结果应写入 `testdata/runs/`，不要写入案例的 `expected/`。只有经过人工检查并确认正确的结果才能成为标准结果。

尚未整理的文件先放入 `testdata/incoming/`；下载文件、OCR 缓存、页面渲染结果等可再生成内容放入 `testdata/cache/`。

少量、授权明确且适合在 CI 中运行的微型测试样例不属于大型测试数据。未来应将它们放在可提交的 `tests/fixtures/` 中。
