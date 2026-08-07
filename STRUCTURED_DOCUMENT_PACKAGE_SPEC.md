# Paper2HTML Structured Document Package 1.0

简称：**P2H Package 1.0**

## 1. 规范范围

P2H Package 是一种可移植的结构化文档目录。

它适用于：

- 扫描 PDF；
- 可直接提取文本和结构的原生 PDF；
- 同时包含原生内容和扫描内容的混合 PDF；
- 书籍；
- 期刊论文；
- 学位论文；
- 报告、讲义及其他技术文档。

它必须包含：

1. 一份规范化的 JATS/BITS XML；
2. 文档中使用的图片、媒体和补充资源；
3. 每个源文件的逐页截图；
4. 每个可寻址内容元素在逐页截图中的位置；
5. 页面、元素和源文档之间的映射；
6. OCR、原生 PDF 提取及人工修订的来源记录；
7. 完整性校验信息。

## 2. 规范用语

本规范中的：

- “必须”表示强制要求；
- “不得”表示禁止；
- “应当”表示除非有明确理由，否则必须遵循；
- “可以”表示可选能力；
- “源文档”表示用户提交的 PDF 或其他原始文件；
- “原生提取”表示直接读取 PDF 中的文字、字体、路径、图片和结构；
- “OCR 提取”表示将页面渲染为图像后进行文字及版面识别；
- “内容元素”表示标题、段落、公式、图片、图注、表格、脚注、参考文献等语义单元；
- “结果包根目录”表示包含 `manifest.json` 的目录，在本规范中记为 `OUTPUT_ROOT/`。

## 3. 标准目录结构

每个合规结果包必须采用以下结构：

```text
OUTPUT_ROOT/
├── manifest.json
├── content/
│   └── document.xml
├── provenance/
│   ├── pages.jsonl
│   ├── elements.jsonl
│   └── omissions.jsonl
├── assets/
│   ├── content/
│   │   ├── figures/
│   │   ├── media/
│   │   └── supplements/
│   ├── evidence/
│   │   └── pages/
│   │   │   └── <source-id>/
│   └── sources/
├── annotations/
│   ├── index.json
│   └── <layer-id>.jsonl
├── validation/
│   └── report.json
└── checksums.sha256
```

其中：

- `manifest.json`：结果包入口和文档清单；
- `content/document.xml`：唯一的规范化正文；
- `provenance/pages.jsonl`：页面映射；
- `provenance/elements.jsonl`：内容元素与原稿区域的映射；
- `provenance/omissions.jsonl`：被有意排除的源页面区域；
- `assets/content/`：正文实际使用的资源；
- `assets/evidence/pages/`：源文档的逐页截图；
- `assets/sources/`：可选的原始源文件副本；
- `annotations/`：可选的译文、术语解释等附加层；
- `validation/report.json`：验证结果；
- `checksums.sha256`：文件完整性校验。

以下可选目录在没有内容时可以省略：

```text
assets/content/media/
assets/content/supplements/
assets/sources/
annotations/
```

其他强制文件不得省略。即使对应 JSONL 文件为空，也必须创建空文件。

## 4. 文件和路径规则

结果包内所有路径必须：

- 使用相对于 `OUTPUT_ROOT/` 的相对路径；
- 使用 `/` 作为路径分隔符；
- 不得以 `/` 开头；
- 不得包含 `..`；
- 不得包含符号链接；
- 大小写敏感；
- 使用 UTF-8 编码；
- 在同一结果包内大小写折叠后仍不得重名。

机器生成文件名必须只使用：

```text
a-z
0-9
-
_
.
```

用户提供的原始文件名只保存在 manifest 元数据中，不直接用作内部文件名。

JSON、JSONL 和 XML 文本必须：

- 使用 UTF-8；
- 不带 BOM；
- 使用 LF 换行；
- 将 Unicode 文本规范化为 NFC。

## 5. 结果包标识

每个结果包必须有永久的 `package_id`，格式为 UUID URN：

```text
urn:uuid:550e8400-e29b-41d4-a716-446655440000
```

重新执行但内容没有变化时，应保留同一 `package_id`。从头创建一个逻辑上不同的转换结果时，必须生成新的 `package_id`。

## 6. `manifest.json`

### 6.1 基本结构

`manifest.json` 必须是一个 JSON 对象，至少包含：

```json
{
  "$schema": "https://hwaipy.github.io/Paper2HTML/schema/1.0/manifest.schema.json",
  "format": "paper2html-package",
  "format_version": "1.0",
  "package_id": "urn:uuid:550e8400-e29b-41d4-a716-446655440000",
  "created_at": "2026-08-07T12:00:00Z",
  "generator": {
    "name": "paper2html",
    "version": "0.1.0"
  },
  "document": {
    "id": "doc-000001",
    "type": "book",
    "language": "en",
    "content": "content/document.xml"
  },
  "sources": [],
  "provenance": {
    "pages": "provenance/pages.jsonl",
    "elements": "provenance/elements.jsonl",
    "omissions": "provenance/omissions.jsonl"
  },
  "validation": "validation/report.json",
  "checksums": "checksums.sha256"
}
```

### 6.2 `document` 字段

`document.type` 必须是：

```text
book
article
thesis
report
other
```

`document.language` 必须使用 BCP 47，例如：

```text
en
zh-CN
de
fr
```

语言无法确定时使用 `und`。

`document.id` 必须符合：

```regex
^doc-[0-9]{6}$
```

### 6.3 `sources` 字段

每个输入版本必须有独立的 source 记录：

```json
{
  "id": "src-001",
  "role": "primary",
  "original_name": "book.pdf",
  "media_type": "application/pdf",
  "sha256": "64位小写十六进制SHA-256",
  "size": 123456789,
  "page_count": 848,
  "source_class": "born-digital",
  "extraction_modes": [
    "native-pdf",
    "ocr"
  ],
  "embedded_path": null
}
```

`role` 必须是：

```text
primary
alternate
supplementary
```

`source_class` 必须是：

```text
born-digital
scanned
hybrid
```

`extraction_modes` 必须从以下值中选择：

```text
native-pdf
ocr
manual
```

对于 PDF，`ocr` 必须存在，不论 PDF 能否直接提取文本。

如果用户要求将原 PDF 一起打包：

```json
"embedded_path": "assets/sources/src-001.pdf"
```

否则：

```json
"embedded_path": null
```

即使不嵌入原 PDF，也必须记录原文件名、大小和 SHA-256。

## 7. 规范化正文 XML

### 7.1 唯一正文

每个 P2H Package 1.0 必须且只能有一个规范化正文：

```text
content/document.xml
```

V1.0 不允许将章节拆成多个互相独立的规范正文文件。通用阅读器可以按章节建立缓存或索引，但不能要求转换结果提前拆分。

### 7.2 XML 基础规则

XML 必须：

- 使用 XML 1.0；
- 使用 UTF-8；
- well-formed；
- 图书类文档符合 BITS 2.1；
- 单篇论文符合 JATS 1.3；
- 同时符合 P2H Profile 1.0 的额外约束。

图书、学位论文和多章节报告使用：

```xml
<book xmlns:xlink="http://www.w3.org/1999/xlink"
      xml:lang="en">
```

单篇期刊论文使用：

```xml
<article xmlns:xlink="http://www.w3.org/1999/xlink"
         xml:lang="en"
         article-type="research-article">
```

### 7.3 文档顺序

XML 中元素的文档顺序是唯一的规范阅读顺序。任何阅读器、转换器或导出器均不得根据页面坐标重新改变已确定的 XML 阅读顺序。

### 7.4 图书结构

图书必须使用：

```xml
<book>
  <book-meta>...</book-meta>
  <book-body>
    <book-part book-part-type="chapter" id="part-0001">
      <book-part-meta>...</book-part-meta>
      <body>...</body>
      <back>...</back>
    </book-part>
  </book-body>
  <book-back>...</book-back>
</book>
```

`book-part-type` 可以是：

```text
front-matter
part
chapter
appendix
index
glossary
other
```

章节、附录和索引均必须进入 XML，不得只存在于 manifest。

### 7.5 论文结构

论文必须使用：

```xml
<article>
  <front>
    <journal-meta>...</journal-meta>
    <article-meta>...</article-meta>
  </front>
  <body>...</body>
  <back>...</back>
</article>
```

### 7.6 必须支持的语义内容

P2H Profile 必须支持：

- 文档标题、副标题；
- 作者、编辑、译者；
- 作者单位和通信信息；
- 出版时间；
- ISBN、ISSN、DOI、arXiv 等标识；
- 摘要和关键词；
- 版权、许可和资助信息；
- 前言；
- 部、章、附录和索引；
- 多级小节；
- 段落；
- 有序列表和无序列表；
- 引文块；
- 代码或预格式文本；
- 行内公式；
- 独立公式；
- 图片和子图；
- 图号和图注；
- 结构化表格；
- 表题、表注和来源；
- 脚注和尾注；
- 参考文献；
- 补充材料；
- 图、表、公式、章节、脚注和文献交叉引用。

## 8. 内容元素 ID

### 8.1 全局唯一性

所有可寻址内容元素必须具有 `id`。`id` 在整个 `document.xml` 中必须全局唯一，不得仅在章节内唯一。

ID 不使用印刷编号作为唯一依据。印刷编号保存在 `<label>` 中。

### 8.2 ID 格式

ID 必须符合：

```regex
^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$
```

规定前缀如下：

| 元素 | ID 格式 |
|---|---|
| 文档 | `doc-000001` |
| 部、章、附录 | `part-0001` |
| 小节 | `sec-000001` |
| 标题 | `title-000001` |
| 段落 | `p-000001` |
| 列表 | `list-000001` |
| 列表项 | `li-000001` |
| 独立公式 | `eq-000001` |
| 行内公式 | `ineq-000001` |
| 图片 | `fig-000001` |
| 图注 | `caption-000001` |
| 表格 | `tbl-000001` |
| 表格单元格 | `cell-000001` |
| 脚注 | `fn-000001` |
| 参考文献 | `ref-000001` |
| 补充材料 | `supp-000001` |
| 引文块 | `quote-000001` |
| 代码块 | `code-000001` |

数字按对应类型在整个文档中的阅读顺序递增。

例如，印刷编号为“Figure 3.2”的图片可以表示为：

```xml
<fig id="fig-000017">
  <label>Figure 3.2</label>
</fig>
```

### 8.3 必须具有 provenance 的元素

以下元素必须有 ID，并且在 `elements.jsonl` 中恰好对应一条记录：

- 所有可见的元数据字段；
- 所有标题；
- 所有段落；
- 所有列表和列表项；
- 所有独立公式；
- 所有行内公式；
- 所有图片；
- 所有图注；
- 所有表格；
- 所有表格单元格；
- 所有脚注；
- 所有参考文献条目；
- 所有引文块；
- 所有代码块；
- 所有补充材料说明。

纯容器元素，例如 `<body>`、`<back>`、`<fn-group>`，不要求单独生成 provenance。

`<italic>`、`<bold>`、`<sup>` 和 `<sub>` 默认继承最近父级内容元素的 provenance，不单独建立来源记录。

## 9. 数学公式

行内公式必须使用：

```xml
<inline-formula id="ineq-000001">
  <tex-math><![CDATA[E = mc^2]]></tex-math>
</inline-formula>
```

独立公式必须使用：

```xml
<disp-formula id="eq-000001">
  <label>(2.5)</label>
  <tex-math><![CDATA[
    \nabla \times \mathbf{E}
    = -\frac{1}{c}\frac{\partial \mathbf{B}}{\partial t}
  ]]></tex-math>
</disp-formula>
```

要求：

- LaTeX 必须位于 CDATA；
- LaTeX 不得包含外层 `$...$`、`$$...$$` 或 `\(...\)`；
- 独立公式的印刷编号保存在 `<label>`；
- 无编号公式省略 `<label>`；
- LaTeX 必须能够被本规范指定的 KaTeX 兼容语法解析；
- 每个公式必须关联至少一个原稿区域；
- 每个公式必须通过 provenance 关联逐页截图及其区域坐标；
- 公式 OCR 候选必须保存在 provenance 中。

## 10. 图片

每张正文图片必须使用：

```xml
<fig id="fig-000001">
  <label>Figure 1.1</label>
  <caption id="caption-000001">
    <p id="p-000010">Caption text.</p>
  </caption>
  <graphic xlink:href="../assets/content/figures/fig-000001.png"/>
</fig>
```

规范化展示图片必须保存为 PNG：

```text
assets/content/figures/fig-000001.png
```

要求：

- PNG 必须为有效、可解码图像；
- 不得将图注烘焙进规范化图片；
- 图注必须同时以结构化文本存在；
- 子图关系必须在 XML 中表达；
- 图片必须通过 provenance 关联逐页截图及其区域坐标；
- 原 PDF 中的矢量或原始二进制资源可以额外保存，但不能代替规范化 PNG。

原始资源可以保存为：

```text
assets/content/figures/original/fig-000001.svg
assets/content/figures/original/fig-000001.pdf
```

并在 provenance 中记录。

## 11. 表格

表格应当优先表示为结构化 XML：

```xml
<table-wrap id="tbl-000001">
  <label>Table 2.1</label>
  <caption id="caption-000002">
    <title id="title-000020">Table title</title>
  </caption>
  <table>
    <thead>
      <tr>
        <th id="cell-000001">Header</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td id="cell-000002">Value</td>
      </tr>
    </tbody>
  </table>
</table-wrap>
```

要求：

- 每个单元格必须有 ID；
- 合并单元格必须使用标准 `rowspan`、`colspan`；
- 表题、表注和来源必须保留；
- 单元格阅读顺序为从上至下、从左至右；
- 每个表格必须通过 provenance 关联逐页截图及其整体区域坐标；
- 每个单元格必须通过 provenance 关联逐页截图及其区域坐标；
- 表格中的公式必须使用行内公式元素。

如果无法可靠恢复表格结构，可以使用图片回退：

```xml
<table-wrap id="tbl-000001" specific-use="image-only">
  <graphic xlink:href="../assets/content/figures/tbl-000001.png"/>
</table-wrap>
```

此情况必须在验证报告中产生 warning，不能静默视为完整结构化表格。

## 12. 交叉引用

所有交叉引用必须使用 `<xref>` 和 `rid`。

例如：

```xml
<xref ref-type="fig" rid="fig-000001">Figure 1.1</xref>
<xref ref-type="table" rid="tbl-000001">Table 2.1</xref>
<xref ref-type="disp-formula" rid="eq-000001">Eq. (2.5)</xref>
<xref ref-type="fn" rid="fn-000001">1</xref>
<xref ref-type="bibr" rid="ref-000001">[1]</xref>
<xref ref-type="sec" rid="sec-000001">Section 1</xref>
```

要求：

- `rid` 必须指向当前 XML 中存在的 ID；
- `ref-type` 必须与目标元素类型一致；
- 不得使用标题文本、印刷编号或文件名代替稳定 ID。

## 13. 页面截图

### 13.1 强制 OCR 和页面渲染

每个 PDF 都必须进行页面渲染和 OCR，包括能够直接读取结构化内容的原生 PDF。

对于原生 PDF：

- 原生文本和结构提取是一个候选来源；
- OCR 是另一个独立候选来源；
- 两者必须保留来源记录；
- 最终文本可以以原生内容为主，但不得省略 OCR 步骤和 OCR 证据。

### 13.2 页面图片格式

每个物理页面必须渲染为：

- PNG；
- sRGB；
- 300 DPI；
- 按 PDF 页面旋转信息校正后的正向页面；
- 不进行裁边；
- 不进行有损压缩。

命名方式：

```text
assets/evidence/pages/src-001/page-000001.png
assets/evidence/pages/src-001/page-000002.png
```

页码是源文件中从 1 开始的物理页序号，使用六位补零。即使页面为空白，也必须有页面截图和页面记录。

## 14. 页面映射 `pages.jsonl`

`pages.jsonl` 每行一个 JSON 对象，每个源文件物理页一行。

示例：

```json
{
  "source_id": "src-001",
  "physical_page": 33,
  "logical_page_id": "lp-000033",
  "printed_label": "1",
  "width_pt": 612.0,
  "height_pt": 792.0,
  "rotation_degrees": 0,
  "image": "assets/evidence/pages/src-001/page-000033.png",
  "image_width_px": 2550,
  "image_height_px": 3300,
  "render_dpi": 300,
  "ocr_status": "completed"
}
```

字段规则：

- `physical_page`：从 1 开始；
- `logical_page_id`：跨不同版本表示同一逻辑页的稳定标识；
- `printed_label`：页面上印刷的页码，可以是 `"xii"`、`"12"` 或 `null`；
- `rotation_degrees`：只能为 `0`、`90`、`180`、`270`；
- `ocr_status`：必须是 `completed`、`no-text` 或 `failed`。

合规结果包不得包含 `ocr_status: "failed"`。

多版本中对应同一逻辑页的记录必须共享同一个 `logical_page_id`。

## 15. 元素来源记录 `elements.jsonl`

### 15.1 一条元素记录

每个需要 provenance 的 XML 元素必须对应一行：

```json
{
  "element_id": "p-000001",
  "xml_path": "/book/book-body/book-part[1]/body/sec[1]/p[1]",
  "reading_order": 17,
  "sources": [
    {
      "source_id": "src-001",
      "physical_page": 33,
      "logical_page_id": "lp-000033",
      "page_image": "assets/evidence/pages/src-001/page-000033.png",
      "regions": [
        {
          "bbox": [0.1023, 0.1831, 0.8874, 0.2612]
        }
      ],
      "candidates": [
        {
          "method": "native-pdf",
          "engine": "pdf-text",
          "engine_version": "1.0",
          "text": "Extracted native text",
          "confidence": 1.0
        },
        {
          "method": "ocr",
          "engine": "example-ocr",
          "engine_version": "2.0",
          "text": "Extracted OCR text",
          "confidence": 0.97
        }
      ]
    }
  ],
  "decision": {
    "method": "reconciled",
    "confidence": 0.99
  },
  "revisions": []
}
```

每个 `sources` 项表示该元素在一个源文档物理页上的位置。`page_image` 必须直接引用该物理页在 `assets/evidence/pages/` 中的逐页截图。元素跨页时，必须为每个涉及的物理页分别建立一个 `sources` 项。

### 15.2 `bbox` 坐标

`bbox` 必须是：

```text
[x0, y0, x1, y1]
```

坐标系规则：

- 左上角为 `(0, 0)`；
- 右下角为 `(1, 1)`；
- 坐标基于旋转校正后的完整页面；
- 四个数均在 `[0, 1]` 范围内；
- 必须满足 `x0 < x1` 和 `y0 < y1`。

一个元素在同一页内由多个不连续区域组成时，必须提供多个 region。元素跨页时，必须使用多个 `sources` 项。

### 15.3 `reading_order`

`reading_order`：

- 从 1 开始；
- 在整个文档范围内连续；
- 不得重复；
- 必须与 XML 中的规范阅读顺序一致。

### 15.4 候选内容

对于文本元素：

- 扫描 PDF 至少有一个 `ocr` 候选；
- 原生 PDF 至少有一个 `native-pdf` 候选和一个 `ocr` 候选；
- 混合 PDF 根据具体区域适用同样规则；
- 不能用最终 XML 文本覆盖候选文本；
- OCR 候选必须保留 OCR 引擎名称和版本；
- `confidence` 必须在 `0` 到 `1` 之间。

`decision.method` 必须是：

```text
native-pdf
ocr
reconciled
manual
```

### 15.5 修订记录

人工或自动语义修订必须记录：

```json
{
  "timestamp": "2026-08-07T12:30:00Z",
  "actor": "human:hwaipy",
  "method": "manual",
  "before": "原内容",
  "after": "修正后内容",
  "reason": "与原始页面核对后修正公式符号",
  "evidence": [{
    "source_id": "src-001",
    "physical_page": 33,
    "page_image": "assets/evidence/pages/src-001/page-000033.png",
    "bbox": [0.1023, 0.1831, 0.8874, 0.2612]
  }]
}
```

不得直接修改最终内容而不增加修订记录。

## 16. 有意省略的内容 `omissions.jsonl`

页眉、页脚、页码、扫描污点等被排除内容不得静默丢弃。

每个省略区域必须记录：

```json
{
  "id": "omit-000001",
  "source_id": "src-001",
  "physical_page": 33,
  "logical_page_id": "lp-000033",
  "page_image": "assets/evidence/pages/src-001/page-000033.png",
  "bbox": [0.1, 0.02, 0.9, 0.06],
  "type": "page-header",
  "reason": "重复页眉，不属于正文"
}
```

`type` 必须是：

```text
page-header
page-footer
page-number
scanner-artifact
duplicate
decorative
blank
unreadable
other
```

`other` 必须提供非空 `reason`。每条省略记录必须通过 `page_image` 和 `bbox` 指向逐页截图中的具体区域。

## 17. 内容资源

### 17.1 Figures

规范化图片：

```text
assets/content/figures/<element-id>.png
```

可选原始表示：

```text
assets/content/figures/original/<element-id>.<ext>
```

### 17.2 Media

音频、视频和交互媒体：

```text
assets/content/media/<element-id>.<ext>
```

允许格式：

```text
audio/mpeg
audio/ogg
video/mp4
video/webm
```

XML 中必须声明 MIME 类型。

### 17.3 Supplements

补充材料：

```text
assets/content/supplements/<element-id>.<ext>
```

必须保持原文件格式，不得为了统一格式而破坏数据。例如：

```text
supp-000001.pdf
supp-000002.csv
supp-000003.zip
```

### 17.4 原始源文件

原始 PDF 默认不嵌入结果包。

用户显式要求嵌入时保存为：

```text
assets/sources/src-001.pdf
assets/sources/src-002.djvu
```

无论是否嵌入，manifest 都必须保存源文件 SHA-256。

## 18. 注释、译文和术语层

译文、术语解释、阅读注释不得改写规范正文，也不得混入 provenance。它们作为可选 annotation layer 保存。

`annotations/index.json`：

```json
{
  "layers": [
    {
      "id": "translation-zh-cn",
      "kind": "translation",
      "language": "zh-CN",
      "path": "annotations/translation-zh-cn.jsonl"
    }
  ]
}
```

每条记录：

```json
{
  "target_id": "p-000001",
  "kind": "translation",
  "language": "zh-CN",
  "content_text": "这是一段中文译文。"
}
```

`kind` 可以是：

```text
translation
definition
commentary
reading-note
```

每条记录必须指向存在的 XML 元素 ID。

`content_text` 与 `content_xml` 必须且只能出现一个：

- `content_text`：纯文本；
- `content_xml`：使用 P2H 允许的 JATS 行内元素构成的 XML fragment。

注释层是结果包的可选增强内容，不属于原文转录，不得被当作原始内容。

## 19. `checksums.sha256`

`checksums.sha256` 必须包含结果包内除自身外所有普通文件的 SHA-256。

格式为：

```text
<64位小写SHA-256><两个空格><相对路径>
```

例如：

```text
0123456789abcdef...  manifest.json
abcdef0123456789...  content/document.xml
```

规则：

- 路径相对于 `OUTPUT_ROOT/`；
- 按路径 UTF-8 字节序升序排列；
- 不包含目录；
- 不包含 `checksums.sha256` 自身；
- 不得遗漏空 JSONL 文件。

## 20. `validation/report.json`

转换结束后必须生成验证报告：

```json
{
  "format": "paper2html-validation-report",
  "format_version": "1.0",
  "valid": true,
  "validated_at": "2026-08-07T13:00:00Z",
  "checks": {
    "manifest_schema": "passed",
    "xml_well_formed": "passed",
    "jats_bits_schema": "passed",
    "p2h_profile": "passed",
    "id_uniqueness": "passed",
    "cross_references": "passed",
    "page_coverage": "passed",
    "element_provenance": "passed",
    "asset_integrity": "passed",
    "checksum_integrity": "passed"
  },
  "errors": [],
  "warnings": []
}
```

如果 `valid` 不是 `true`，该目录不得被声明为合规的 P2H Package。

## 21. 强制验证规则

合规验证器必须检查以下内容。

### 21.1 文件级

- 所有强制文件存在；
- 没有非法路径或符号链接；
- 所有文件通过 SHA-256；
- manifest 符合 JSON Schema；
- JSONL 每一非空行均是独立有效 JSON。

### 21.2 XML

- XML well-formed；
- 通过相应 JATS/BITS Schema；
- 通过 P2H Profile；
- 所有 ID 全局唯一；
- 所有 `rid` 目标存在；
- 所有 `xlink:href` 目标存在；
- XML 根类型与 manifest 的 `document.type` 一致；
- XML 元数据与 manifest 不矛盾。

### 21.3 页面

- 每个源文件的物理页从 1 连续到 `page_count`；
- 每页恰好有一张页面截图；
- 页面 PNG 可解码；
- 页面尺寸与 300 DPI 渲染结果一致；
- 没有 OCR 失败页；
- 多来源逻辑页面映射没有冲突。

### 21.4 元素

- 每个强制 provenance 元素恰好有一条记录；
- 每条 provenance 都指向存在的 XML ID；
- 每个 `page_image` 都指向 `pages.jsonl` 中对应物理页的页面截图；
- 每个 region 都位于页面范围内；
- `reading_order` 唯一且连续；
- `reading_order` 与 XML 顺序一致；
- 原生 PDF 的文本元素同时具有原生提取和 OCR 候选。

### 21.5 内容覆盖

每个非空源页面区域必须满足以下之一：

- 映射到一个 XML 内容元素；
- 记录为有意省略区域。

验证器不得允许来源不明的大面积未覆盖区域。

### 21.6 公式、图片和表格

- 所有 LaTeX 可解析；
- 所有图片可解码；
- 所有图、表、公式标签与对应元素关联；
- 每个表格行列关系合法；
- 表格合并单元格不越界；
- 图片、表格和公式均通过页面截图引用及区域坐标关联原稿证据。

## 22. 原生 PDF 与扫描 PDF 的统一处理规则

两类 PDF 最终产生完全相同的包结构。差别只体现在 provenance：

| 来源类型 | 原生提取 | OCR | 页面截图 | 元素坐标定位 |
|---|---:|---:|---:|---:|
| 扫描 PDF | 可选 | 必须 | 必须 | 必须 |
| 原生 PDF | 必须 | 必须 | 必须 | 必须 |
| 混合 PDF | 适用区域必须 | 必须 | 必须 | 必须 |

原生 PDF 也必须 OCR，原因是：

- 验证原生文本是否缺字或乱码；
- 处理公式、特殊字体和嵌入字形；
- 建立页面坐标和视觉证据；
- 保证所有内容元素可以回到原始页面；
- 使不同类型 PDF 具有一致的数据结构和审核方式。

最终 XML 不需要区分元素最初来自 OCR 还是原生提取；该信息统一保存在 `elements.jsonl`。

## 23. 最小合规结果包

最小合规包至少为：

```text
OUTPUT_ROOT/
├── manifest.json
├── content/
│   └── document.xml
├── provenance/
│   ├── pages.jsonl
│   ├── elements.jsonl
│   └── omissions.jsonl
├── assets/
│   └── evidence/
│       └── pages/
│           └── src-001/
│               └── page-000001.png
├── validation/
│   └── report.json
└── checksums.sha256
```

如果正文包含图片、媒体或补充材料，则必须增加相应的 `assets/content/` 文件。

## 24. 合规性声明

一个结果目录只有同时满足以下条件，才能称为：

```text
Paper2HTML Structured Document Package 1.0
```

条件是：

1. 目录结构符合本规范；
2. manifest 符合 P2H Manifest Schema 1.0；
3. 正文通过固定版本的 JATS/BITS Schema；
4. 正文通过 P2H Profile 1.0；
5. provenance 完整；
6. 所有页面都有逐页截图，所有内容元素都通过页面引用和区域坐标关联视觉证据；
7. 所有内部引用和资源路径有效；
8. 所有文件通过 SHA-256；
9. `validation/report.json` 中 `"valid": true`。

原生 PDF、扫描 PDF 和混合 PDF 使用同一套输出格式，并全部经过 OCR、页面截图和元素级坐标定位。
