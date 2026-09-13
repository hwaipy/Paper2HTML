# Paper2HTML Reader 交付与双入口设计

本文固定 P2H Package 的浏览器阅读入口设计，并描述当前实现的接口和职责。

## 设计结论

每个转换结果包包含两个轻量职责明确的 HTML 入口：

- `index.html`：用于 HTTP/HTTPS 静态发布；
- `index-local.html`：用于用户在桌面文件系统中直接双击打开。

Reader 本身不复制进每个结果包。两个入口都引用网站 B 通过 HTTPS 托管的、按版本固定且不可变的
Reader 发布文件。结果包既可以作为网站 A 上的普通静态目录发布，也可以作为完整目录下载到本地。
本设计不依赖 Sites，Reader 可以发布到任何满足要求的静态文件服务。

## HTTP/HTTPS 模式

用户打开网站 A 上的：

```text
https://site-a.example/documents/example/index.html
```

`index.html` 从网站 B 加载 Reader JavaScript 和 CSS。虽然代码来自网站 B，但它在网站 A 页面中
执行。Reader 以当前页面所在目录为包根目录，通过相对 URL 和 Fetch API 读取网站 A 上的：

```text
./manifest.json
./content/document.xml
./provenance/*.jsonl
./annotations/*
./validation/report.json
```

这些请求与 `index.html` 同源，因此网站 A 不需要为了 Reader 配置跨域访问。Reader 发布服务器只需
允许浏览器加载其 JavaScript、CSS 和这些发布文件自身依赖的静态资源。

## 本地文件模式

用户双击：

```text
file:///.../package/index-local.html
```

普通浏览器通常不允许 `file://` 页面用 Fetch API 自动读取相邻的 JSON、JSONL 或 XML。为实现
零交互打开，Converter 必须把 Reader 所需的规范文本文件作为只读快照嵌入
`index-local.html`。Reader 从内嵌映射实现的 `EmbeddedPackageSource` 读取这些文本，不再对它们
发起本地 Fetch 请求。

内嵌范围是结果包内 Reader 可能通过 JavaScript 读取的全部 UTF-8 文本文件，包括：

- `manifest.json`；
- `content/document.xml`；
- `provenance/pages.jsonl`；
- `provenance/elements.jsonl`；
- `provenance/omissions.jsonl`；
- `validation/report.json`；
- 存在时的 `annotations/index.json` 及其声明的全部 annotation layer 文件；
- 以后由 manifest 声明、Reader 阅读时需要的其他 UTF-8 文本文件；
- Reader 需要读取的文本型补充资源。

以下内容不嵌入：

- `checksums.sha256`；
- `index.html` 和 `index-local.html`；
- 图片、音频、视频、字体、源 PDF 和其他二进制资源。

`checksums.sha256` 不得嵌入，是因为它必须包含 `index-local.html` 的哈希；若
`index-local.html` 又包含 `checksums.sha256`，两者会形成无法稳定求值的自引用循环。

## 二进制资源

图片、音视频和其他大型资源在两种模式下使用完全相同的包内相对 URL。Reader 以入口 HTML 所在
目录为包根目录解析资源路径，例如：

```text
assets/content/figures/fig-000001.png
assets/evidence/pages/src-001/page-000001.png
```

浏览器会分别解析为 `file://...` 或 `https://site-a.example/...`。不需要 Base64、不需要 Blob URL，
也不需要把大型资源登记到本地文件选择器中。

## Reader 数据源边界

Reader 保持一套 manifest 加载、XML 解析和文档渲染代码，只替换文本来源：

```ts
interface PackageSource {
  readText(path: string): Promise<string>;
  resourceUrl(path: string): string;
}
```

- `HttpPackageSource.readText()` 使用 `fetch()`；
- `EmbeddedPackageSource.readText()` 从 `index-local.html` 的内嵌映射读取；
- 两者的 `resourceUrl()` 都以当前入口页面目录解析包内相对 URL。

Reader 根据入口提供的启动配置选择数据源，不根据文件名猜测模式。XML 字符串取得后，两种模式都
使用同一个安全配置的 `DOMParser` 和同一套渲染组件。

## 内嵌格式与安全

文本快照必须使用明确版本的启动协议，并以不会终止 `<script>` 元素的方式编码。推荐将每个 UTF-8
文件独立进行 Base64 编码，再放入一个 JSON 路径映射；Reader 解码时必须严格校验 UTF-8。

Converter 必须：

- 仅接受规范化、安全的包内路径；
- 在所有规范文本及最终验证报告写完后生成 `index-local.html`；
- 最后生成 `checksums.sha256`，使两个 HTML 入口都进入完整性校验；
- 保证固定输入、固定 Reader 版本和固定时间戳产生字节稳定的入口文件。

Reader 发布引用必须使用 HTTPS、固定兼容版本和不可变文件。后续发布加固应使用内容哈希文件名或
Subresource Integrity；若使用 SRI，网站 B 必须返回浏览器所需的 CORS 响应头。
Reader 使用的 KaTeX 字体必须作为发布目录中的独立字体文件提供，不得以内联 Base64 字体扩大
`reader.css`；CSS 中的字体 URL 必须相对于 Reader 发布目录解析。

Reader 发布信息独立记录在机器可读的 [`reader/releases.json`](reader/releases.json) 中。清单包含
默认版本、该版本的固定 HTTPS 目录、入口文件名和 SHA-256。Converter 在生成包时读取仓库内清单，
并把选定版本的具体 URL 固化进两个入口；它不在转换时联网读取可变的 `latest` 信息。发布清单不属于
P2H Package Schema：Reader 可以独立升级，而不会改变结构化文档包格式。已经发布的版本目录不得
覆盖；发布新 Reader 时应新增版本目录和清单项，再显式切换 `default`。

## 文件大小约束

`index.html` 不包含文档正文或 provenance 快照，应保持很小。

`index-local.html` 会重复上述文本内容，大小大致等于所有嵌入文本的总大小再加编码开销；这是本地
双击、零交互读取与浏览器 `file://` 安全边界之间的明确权衡。二进制资源始终不进入该文件。

## 实现与验证范围

当前实现同步包含：

1. Reader 的版本化静态发布构建；
2. `HttpPackageSource` 与 `EmbeddedPackageSource`；
3. Converter 的两个入口模板和安全快照编码；
4. Validator 对入口、Reader 引用、启动协议和 checksum 的检查；
5. 规范、golden package 与回归投影更新；
6. HTTP 双站点和本地 `file://` 的浏览器集成测试接口；真实发布地址上线后还需执行发布验收。
