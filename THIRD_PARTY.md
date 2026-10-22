# 第三方组件与许可（THIRD PARTY LICENSES）

**本项目整体以 GNU AGPL-3.0 发布**（见 [LICENSE](LICENSE)），原因见下。

本仓库自身代码与下列第三方组件的许可**相互独立**。分发本仓库或其构建产物前，
请确认已满足每一项的要求。

## ⚠️ 最重要的一条：PyMuPDF 是 AGPL-3.0

| 组件 | 许可 | 用在哪 |
|---|---|---|
| **PyMuPDF (fitz)** | **AGPL-3.0** 或 Artifex 商业授权 | `convert_worker.py` 判断扫描件、`ocr_worker.py` / `ocr_pdf_worker.py` 渲染页面 |

`pdf2docx`（MIT）依赖 PyMuPDF，本项目的 OCR 功能也直接调用 PyMuPDF。
**因此包含这些代码的整体作品需要按 AGPL-3.0 分发**，除非购买 Artifex 商业授权，
或改用不依赖 PyMuPDF 的实现。

- 内网自用（不分发）：AGPL 的主要义务是**向通过网络使用它的用户提供源码**，
  保留可访问的源码仓库通常即可满足。
- 上传到 GitHub / 发布镜像：属于分发，必须按 AGPL-3.0 提供完整对应源码与许可声明。

## 运行时组件

| 组件 | 许可 | 说明 |
|---|---|---|
| kkFileView 4.1.0 | Apache-2.0 | 独立容器进程，通过 HTTP 通信 |
| nginx | BSD-2-Clause | 官方镜像 |
| Python | PSF-2.0 | 官方镜像 |
| FastAPI / Uvicorn | MIT / BSD-3-Clause | portal 服务 |
| pdf2docx | MIT | PDF→Word（但依赖 AGPL 的 PyMuPDF） |
| python-docx | MIT | 生成 docx |
| RapidOCR (rapidocr-onnxruntime) | Apache-2.0 | OCR 引擎，模型为 PP-OCRv4 |
| ONNX Runtime | MIT | 推理后端 |
| OpenCV (headless) | Apache-2.0 | RapidOCR 依赖 |
| Flyfish File Viewer（`@file-viewer/renderer-ofd`） | Apache-2.0 | 前端 OFD 渲染器，见 `web/html/vendor/ofd/LICENSE` |

## 字体

| 字体 | 许可 | 位置 |
|---|---|---|
| Carlito | SIL OFL 1.1 | `fonts/LICENSE-OFL-Carlito.txt` |
| Caladea | SIL OFL 1.1 | `fonts/LICENSE-OFL-Caladea.txt` |
| 宋体/黑体/楷体/仿宋/微软雅黑等 | **商业字体，由 kkFileView 镜像自带** | 不在本仓库内分发 |

> 镜像里自带的微软雅黑、宋体等属于微软/方正商业授权字体，由镜像提供方负责。
> **本仓库只额外分发 OFL 许可的 Carlito / Caladea，不要把商业字体加进 `fonts/`。**

## 检查清单（发布前）

- [ ] 选定本仓库的许可证；若保留 PyMuPDF，应为 AGPL-3.0
- [ ] 保留 `web/html/vendor/ofd/LICENSE` 与 `THIRD_PARTY_LICENSES.txt`
- [ ] 保留 `fonts/LICENSE-OFL-*.txt`
- [ ] 不要把 kkFileView 镜像里的商业字体导出到 `fonts/`
- [ ] 确认仓库中没有内网 IP、主机名、账号口令
- [ ] `images/`、`dist/`、`compose/.env` 已被 `.gitignore` 排除
