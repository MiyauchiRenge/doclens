# 验证记录（VERIFY）

本文件如实记录：哪些内容**已在真实环境验证通过**，哪些**未验证**、原因是什么。
请勿把"未验证"当成"已验证"使用。

验证日期：2026-10-22
验证环境 A：Windows 11 + Docker Desktop 29.8.0（linux/x86_64 引擎）、
Python 3.11（干净 venv）、WSL bash
验证环境 B：**openEuler 22.03 LTS-SP3**（x86_64，3.4 GB 内存），
Docker 26.1.3 + Compose v2.27.0 —— 实机部署并远程验收

---

## 一、已验证通过 ✅

### 1. portal 服务逻辑（真实 HTTP 调用，非模拟）

`tools/smoke_test.py`，共 40 项断言全部通过：

| 范围 | 验证内容 |
|---|---|
| 健康检查 | `/api/health` 返回 200，状态字段正确 |
| 上传 | 中文文件名保留、大小统计、空文件拒绝 |
| 预览路由 | PDF/图片走 native，`.doc` 走 kkFileView |
| kkFileView 地址构造 | URL 为 base64 且**内层地址带扩展名**（其类型识别依赖后缀） |
| 原始字节 | 下载内容与上传内容逐字节一致，`Content-Type` 正确，`no-store` 生效 |
| 路径穿越 | `../../etc/passwd` 被净化为安全的纯文件名 |
| 一次性下载 | 首次 200 且内容正确，二次访问 410 |
| 会话清理 | 关闭后会话从索引移除，再取文件返回 404 |
| 转换状态机 | 任务能从 queued/running 落到 done/failed，不永久卡死；失败带可读原因 |
| 入参校验 | 非 PDF 拒绝转换（400） |

### 2. PDF → Word 真实转换

`tools/convert_test.py`，使用生产同一个 `convert_worker.py`：

- 夹具：嵌入真实中文字体的 PDF（含标题、正文、3×3 表格）
- 结果：退出码 0，耗时 **3.2 秒**，产出 37 KB `.docx`
- 校验：中文关键词完整、标题顺序正确、**无缺字占位符**、表格被识别
- 结论：达到"内部可读"标准（文字完整、顺序正确、可编辑、表格结构保留）

**全栈复验**（nginx → portal → 容器内 pdf2docx → 一次性下载）：

- 上传 → `convertible=true`，转换 `done`，下载 200 / 37,673 字节
- 同一链接二次下载返回 **410**（一次性令牌生效）
- 产物可被 python-docx 打开：4 段正文 + 1 个表格，内容与源 PDF 一致

> 附注：首轮测试用的是未嵌入中文字体的 PDF，导出全是 `IIIII` 占位符。
> 这是**夹具缺陷**（PDF 本身没有 ToUnicode 映射），不是转换器缺陷。
> 换成嵌入字体后完全正常。真实业务 PDF（Word/Office 导出）通常带有正确映射。

### 3. 依赖可解析

在全新 venv 中仅凭 `services/portal/requirements.txt` 安装成功：
`fastapi 0.115.6 / uvicorn 0.34.0 / python-multipart 0.0.20 / pdf2docx 0.5.8`，
无版本冲突，`import pdf2docx` 正常。

### 4. 编排与脚本静态校验

- `docker-compose.yml` YAML 可解析，三个服务定义完整
- 四个 shell 脚本 `bash -n` 全部通过
- 全部 Python 文件 `py_compile` 通过
- 前端 `app.js` `node --check` 通过

### 5. OFD 前端渲染（真实样本，无头 Edge 实测）

用 `@file-viewer/renderer-ofd`（flyfish file-viewer，Apache-2.0）单独打了一个
OFD-only 产物（1.16 MB / gzip 230 KB），在无头 Edge 中加载 3 个真实 OFD 样本：

| 样本 | 大小 | 事件链 | SVG 节点 | 文字层字符 | JS 报错 |
|---|---|---|---|---|---|
| h.ofd | 73 KB | load-start → load-complete | 79 | 16,581 | 无 |
| n.ofd | 40 KB | load-start → load-complete | 23 | 16,901 | 无 |
| 999.ofd | 30 KB | load-start → load-complete | 779 | 19,825 | 无 |

**结论：OFD 可以在浏览器内纯前端渲染，无需后端转码、无需额外容器。**
渲染产物是 SVG + 图片 + 可提取文字层，不是位图。

#### 全栈复验（nginx → portal → 浏览器）

在 `docker compose` 起到三容器后，经真实 `/api/upload` → `/api/preview` 流程，
用无头 Edge 截图（`docs/ofd-render-proof.png`）确认：

- 页码显示 **1 / 5**，工具栏（搜索、缩放、下载、打印、HTML）正常
- 页面内容与**红色签章图层**均正确绘制（该样本自带 OFDRW 测试章）
- 事件链 `load-start → load-complete`，无 JS 报错

#### 踩到并修掉的一个真实缺陷

首轮全栈测试时页面显示"不支持当前文件格式的在线预览"。
原因：预览 URL 用了无后缀的 `/api/file/{sid}/{key}`，
而 flyfish 渲染器**靠 URL 后缀判定文件类型**——和 kkFileView 是同一个坑。

修复：OFD 走 `/api/file/{sid}/{key}/{文件名}`。
只靠 DOM 计数（SVG=1）会被误导，是**截图**才暴露的问题；
因此本项结论以截图为证据，不以代码计数为准。

> 附带修正了一个此前的错误假设：**kkFileView 4.1.0 完全没有 OFD 能力**。
> 拉取镜像后 `find / -iname '*ofd*'` 无结果、jar 内无 `ofd` 字符串，
> 且 Docker Hub 上不存在 `4.4.0` 这个 tag（最高为 `4.1.0`）。
> 原方案中"OFD 交给 kkFileView"的设计已废弃。

---

## 二、未验证 ❌（务必在目标环境确认）

### 1. 镜像构建与多容器编排：**已验证 ✅**（VPN 恢复后补测）

- `docker build` 成功：portal 镜像 **925 MB**（含 pdf2docx / OpenCV / PyMuPDF）
- 三个镜像均拉到本地：nginx 74.5 MB、kkFileView **2.5 GB**、portal 925 MB
- `docker compose up -d` 三容器全部启动，portal 健康检查通过
- 修正了两处只有真跑才会暴露的问题：
  - 原 `Dockerfile` 多装了 libGL 等图形库，Debian 源上 `libgbm1` 易 502 失败；
    pdf2docx 用的是 `opencv-python-headless`，只需 `libglib2.0-0` + `libgomp1`
  - compose 里 `./web` 应为 `../web`（compose 文件在子目录），否则 nginx 起不来

仍未验证：`docker save` 产物在**离线机器**上 `load` 的完整性（需目标机）；
以及镜像在 openEuler/麒麟 的 Docker 版本下是否同样正常。

### 2. 带国密签章的 OFD 渲染效果：**部分验证**

已确认：样本中的**图像型签章图层能正确绘制**（见截图中的红色测试章）。

未确认：带 **SM2 签名与验签交互**的 OFD——没有这类真实样本可测。
渲染器对签名面板、验签结果展示的支持程度未知。

风险：签章可能只显示图形、不显示验签状态。这通常不影响"看"，但影响"信"。
退路是引入 `ofdrw`（Java，Apache-2.0）在服务端把 OFD 转成 PDF 或做验签。

### 3. kkFileView 在子路径下能否正常工作：**已验证 ✅**

实测 `/preview/onlinePreview` 返回 200 的预览页，且其资源引用是**相对路径**
（`js/jquery-3.6.1.min.js`），经 `/preview/js/...` 代理后返回 200（89 KB）。
ngx 的 `@kk_fallback` 兜底规则保留，用于覆盖潜在的绝对路径引用。

#### 踩到并修掉的第二个真实缺陷：DOCX 卡在 loading

现象：`.docx` 预览页能打开（标题"PDF图片预览"），但一直显示 loading 图标，
正文图片不出现。用无头浏览器截图才看清。

根因：kkFileView 会把转码后的页面**拼成绝对地址**塞进 `<img data-src="...">`，
地址基于请求的 `Host` 头。nginx 原先用 `proxy_set_header Host $host`，
`$host` **只有主机名没有端口**，于是生成 `http://127.0.0.1:80/测试文档/0.jpg`，
浏览器去连 80 端口自然失败。

修复：改为 `$http_host`（带端口），并补 `X-Forwarded-Port`。
修复后地址变为 `http://127.0.0.1:8080/测试文档/0.jpg`，返回 200（52 KB JPEG），
截图确认标题与两段中文正文正常渲染。

#### 踩到并修掉的第三个真实缺陷：Office 预览"格式不对"

排查：检查容器内字体替换链（`fc-match`），发现两处硬缺口：

| 文档字体 | 修复前被替换成 | 后果 |
|---|---|---|
| **Calibri**（Word 默认西文） | 文泉驿正黑 | 英文/数字宽度全变 → 换行、对齐、分页错位 |
| **DengXian / 等线**（Office 2016+ 默认中文） | 文泉驿正黑 | 中文字形与字面不一致 |

宋体/仿宋/楷体/黑体/微软雅黑镜像自带，不缺。

修复：加入 **Carlito**（Calibri 的度量兼容开源字体，SIL OFL）与
`99-font-alias.conf` 映射（Calibri→Carlito、等线→微软雅黑、Arial→Liberation）。
挂载单个 conf 文件，不覆盖镜像自带 fontconfig 配置。

验证：`fc-match Calibri` → `Carlito-Regular.ttf`；`fc-match 等线` → `msyh.ttf`。

> 仍需诚实说明：LibreOffice 与 Word 是不同排版引擎，
> 文本框、图文环绕、表格边框、分页位置等差异**无法靠补字体消除**。
> 对版式要求极高的文档，应在源端另存 PDF 后再预览。

#### 踩到并修掉的第四个缺陷：字体替换导致"字变大、行距变松"

现象：用户反馈"感觉有些字体大"。

排查：先量了容器内可用字体（宋体/黑体/楷体/仿宋/雅黑/华文全套都在），
再用 `font.getmetrics()` 量各字体行高，发现关键差异：

| 字体 | 行高/字号 |
|---|---:|
| 宋体 / 黑体 / 楷体 / 仿宋 | 1.01 |
| 等线 DengXian | 1.05 |
| **微软雅黑** | **1.33** |

而此前把「等线」映射到了「微软雅黑」——等线本身行高 1.05，换成雅黑后行高被抬到 1.33。

**修复后用真实文档量化验证**（同一段 12 行中文，10.5pt，导出 PDF 量行距）：

| 渲染 | 平均行距 | 相对基准 |
|---|---:|---:|
| 宋体（基准） | 23.8 pt | — |
| 等线 → 黑体（修复后） | **23.8 pt** | **0%** |
| 等线 → 微软雅黑（修复前） | 25.9 pt | **+8.8%** |

改法：`99-font-alias.conf` 里 `DengXian/等线/等线 Light` 改指向 `SimHei`（黑体），
并补了 `Cambria → Caladea`（度量兼容）。

> 说明：按字体自带 metrics 算是 1.33/1.05 ≈ +27%，但实际排版里段前段后间距、
> 行距倍数等会稀释这个差异，实测落在 **+8.8%**。以实测为准。

#### 试过但未采用的方案：PDF 矢量预览

`KK_OFFICE_PREVIEW_TYPE=pdf` 可切换为矢量 PDF 预览（清晰、文字可选中）。
实测：PDF 能取到（200 / application/pdf），但 kkFileView 的 pdf.js 页面
始终显示 `0/0` 页、文档不渲染。**结论：未通过验证，默认仍用 image 模式**，
开关保留但默认值不动。

### 4. openEuler / 麒麟实机安装：**已在 openEuler 验证 ✅**

目标机：openEuler 22.03 LTS-SP3 / kernel 5.10 / x86_64 / 3.4 GB 内存 / 40 GB 可用盘 /
Docker 26.1.3 / Compose v2.27.0 / SELinux Permissive / 无 KYSEC。

部署结果：三容器起来，`http://<IP>:9080` 可访问，三项功能全部实测通过（见下）。

实机暴露并修复的**三个**问题（本地 Docker Desktop 上都不会出现）：

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | kkFileView 反复重启，JVM fatal error | 宿主 `nr_open=1073741816`，Docker 继承超大 nofile；Java 8 按此预分配 fd 表 → `unable to allocate file descriptor table - out of memory` | compose 显式 `ulimits.nofile: 65535` |
| 2 | 起了也连不上 LibreOffice：`started but its pid could not be found`，残留 `<defunct>` soffice | 容器内 JVM 是 PID 1，不回收子进程 | `init: true`（tini 当 PID 1） |
| 3 | 小内存机上 JVM 与 LibreOffice 抢内存 | JVM 按物理内存推算堆 | `JAVA_TOOL_OPTIONS=-Xmx768m`，并加大 `shm_size: 256m` |

实机验收（远程调用，非本地）：

- DOCX：预览页 200，图片地址 `http://<内网IP>:9080/远程测试/0.jpg` → 200（52 KB）
- OFD：`mode=ofd`，截图与本地结果**字节级一致**（5 页、含签章图层）
- PDF→Word：`done`，下载 200 / 37,673 字节，二次下载 410，产物 4 段 + 1 表格且中文正确
- 资源占用：内存 1.0 GB / 3.4 GB，镜像合计 2.59 GB

### 4.1 最终全量验收（端口 7783，含新增导出能力）

| 项 | 结果 |
|---|---|
| DOCX 预览 | ✅ 页面 200，图片 200 |
| XLSX 预览 | ✅ HTML 表格渲染，公式算出 3 |
| OFD 预览 | ✅ `mode=ofd`，5 页含签章 |
| PDF → Word | ✅ `done`，37,673 字节，二次下载 410 |
| **Word → PDF** | ✅ 200 / 11,804 字节，magic `%PDF-`，耗时约 1 秒 |
| **Excel → PDF** | ✅ 按设计拒绝：415 + 明确提示（见下） |
| 转码产物清理 | ✅ 重启前 13 个文件 → 重启后 **0 个**（tmpfs 生效） |
| 端口可配置 | ✅ 8080 → 7783 切换后健康 200 |
| 重复下载 | ✅ 同一链接连续下载 3 次均 200（见下方修复说明） |
| 扫描件 PDF | ✅ 明确拒绝并说明原因，不产出空文档 |

**Excel → PDF 的取舍**：kkFileView 的 Excel 预览走 HTML 表格渲染，不会产出 PDF。
强行导出只会 404，所以 portal 在**上传时就把 `pdf_export` 判为 false**，
前端不显示按钮，绕过前端直接调接口也会得到 415 和可读的原因。
要 Excel 的 PDF 版，正确做法是源端另存为 PDF。

#### 修复：下载链接"已失效或已被使用"

早期把下载令牌做成**一次性**（GET 一次就从表里摘除）。实测中浏览器会重复请求
（下载管理器、用户点两次、重试），第二次必然 410，体验很差。

改为：令牌在会话有效期内**可重复下载**，文件仍随会话关闭 / TTL 到期删除——
"用完即清"的语义不变，只是不再"用一次就废"。
实测同一链接连续下载 3 次：200 / 200 / 200。

#### 新增：扫描件 OCR 转 Word（RapidOCR）

扫描件没有文字层，pdf2docx 只会产出空壳。现在提供两条路：

1. **普通转换**：转换前用 PyMuPDF 统计可提取字符，低于 20 个判定为扫描件，
   直接拒绝并提示改用 OCR（不产出无意义的空文档）。
2. **OCR 转换**：`POST /api/ocr/{sid}/{key}`，走 RapidOCR 流水线。

OCR 实现：PyMuPDF 按 200 DPI 渲染每页 → RapidOCR 识别（PP-OCRv4 模型，ONNX 推理）
→ 按行聚类 + 行距/字号判断合并段落 → python-docx 生成。

**开发机实测**（中文扫描样本，1 页）：

```
'设备安装说明（扫描件测试）'
'一、开箱检查'
'请在签收后24小时内核对包装清单，确认主机、电源线、'
'导轨及随机文档齐全。如有缺失请及时联系售后。'
'二、上架安装'
'将服务器沿导轨推入机柜，确保卡扣到位。上架前请断开'
'所有电源，并做好防静电措施。'
```

标题与正文正确分段，中文零错字，首轮耗时 13.2 秒（含模型加载）。

**关键工程决策**：`rapidocr-onnxruntime` 声明依赖 `opencv-python`（GUI 版），
与 pdf2docx 的 `opencv-python-headless` 会争抢 `cv2`。Dockerfile 用
`pip install --no-deps` 只装本体，复用 headless 版，并在构建期加了一行
`import cv2; from rapidocr_onnxruntime import RapidOCR` 冒烟，缺库会当场失败。

**未验证**：目标机（3.4 GB 内存）上的实际耗时与内存峰值——见下方待办。

#### 新增：可搜索 PDF（保留原貌 + 文字层）

用户反馈 OCR 产物“底色、图片都没保留”——这是对的，纯文字 Word 就是会丢版式。
因此增加了第二种产物：**原图不动，只叠不可见文字层**。

实现：PyMuPDF 按 200 DPI 渲染 → RapidOCR 识别拿坐标 → 坐标从像素换算回 PDF 点
→ `insert_text(..., fontname="china-s", render_mode=3)` 写不可见文字。
`china-s` 是 PyMuPDF 内置简体中文字体，不依赖外部字体文件，离线可用。

**容器内实测**（同一份中文扫描件）：

| | 图片数 | 可提取文字 | 体积 |
|---|---:|---:|---:|
| 原文件 | 1 | 0 | 168,654 B |
| 可搜索版 | **1（原图保留）** | **172 字** | 171,615 B（+3 KB） |

提取出的文字与源图逐字一致。**外观零损失，但可搜索、可复制。**

> 同时修正了一个命名缺陷：下载文件名之前用的是会话内部 key
> （`1792654…_可搜索.pdf`），现在改用用户原始文件名（`设备安装说明_可搜索.pdf`）。

**目标机实测（openEuler 22.03 / 3.4 GB）**：

| 步骤 | 结果 |
|---|---|
| 普通转换扫描件 | 正确拒绝，提示改用 OCR |
| OCR 转换（1 页中文） | `done`，**12 秒**（含模型加载），产出 37 KB docx |
| OCR 转换（5 页） | `done`，**29 秒** → 稳态约 **5 秒/页**（首页含模型加载） |
| 识别文本 | 与源图逐字一致，标题/正文分段正确，零错字 |
| OCR 后内存 | 915 MB / 3409 MB（可用 2.5 GB） |
| 镜像体积 | 637 MB → **689 MB**（+52 MB） |

> 构建方式说明：本机（Windows + VPN）拉 onnxruntime 太慢，改为**在目标服务器上构建**——
> 服务器能直连 pypi.org（实测 200），只需把 `python:3.11-slim` 基础镜像推过去（46 MB）。
> 构建耗时约 2 分钟，`import cv2; from rapidocr_onnxruntime import RapidOCR` 冒烟通过。

### 5. 性能与并发：**未验证**

单次转换耗时 3.2 秒是**小文件、单任务**的数据。
老格式占一半的真实负载下的吞吐、排队、内存峰值均未压测。

---

## 三、复现方式

```bash
# 服务逻辑
python3 tools/smoke_test.py

# 真实转换
pip install reportlab
python3 tools/convert_test.py

# 静态校验
bash tools/check.sh
```

前两项在联网开发机上即可跑，不需要 Docker。
