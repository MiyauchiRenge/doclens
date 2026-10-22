# DocLens · 内网离线文档预览平台

> 一个可完全离线部署的文档预览 / 转换服务：**OFD、Office、PDF 在线预览**，
> **PDF ⇄ Word 互转**，**扫描件 OCR**。为内网环境设计，不依赖任何外部 CDN。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

---

## 它能做什么

| 能力 | 说明 |
|---|---|
| **PDF / 图片 / 音视频预览** | 浏览器原生渲染，不经过服务端 |
| **OFD 预览** | 纯前端渲染（[flyfish file-viewer](https://github.com/flyfish-dev/file-viewer)），无需后端转码，支持签章图层 |
| **Word / Excel / PPT 预览** | kkFileView + LibreOffice 转码 |
| **老格式 `.doc` / `.xls` / `.ppt`** | 同上，这是纯前端方案做不到的部分 |
| **PDF → Word** | pdf2docx，独立子进程 + 硬超时 |
| **Word / PPT → PDF** | 复用 LibreOffice 转码产物，零新增组件 |
| **扫描件 → 可搜索 PDF** | 原图不动，叠加不可见文字层（可搜索、可复制，外观零损失） |
| **扫描件 → 文字 Word** | RapidOCR（PP-OCRv4），纯文字、无版式 |
| Excel → PDF | ❌ 不支持（kkFileView 的 Excel 走网页表格渲染，不产 PDF） |
| Office 在线编辑 | ❌ 刻意不做，本项目的定位是「看」和「转」 |

## 界面

| OFD 预览（含签章） | Word 预览 |
|---|---|
| ![OFD](docs/ofd-render-proof.png) | ![DOCX](docs/docx-render-proof.png) |

## 为什么做这个

企业内网里常见三个痛点：

1. **OFD 没人认** —— 国产版式文档，PDF 工具打不开，kkFileView 也不支持
2. **外网依赖** —— 很多预览方案偷偷从 CDN 拉 pdf.js、字体、worker，一断网就白屏
3. **临时文件留痕** —— 上传的公文在服务器上堆着没人清

这个项目针对这三点：OFD 用纯前端渲染器、所有静态资源随包自带、
临时文件全部走 tmpfs 且会话结束即销毁。

## 架构

```
                      ┌──────────────────────────────────────┐
  浏览器  ──────────► │  nginx  (对外唯一端口)                │
                      │  /        -> 静态前端 + OFD 渲染器    │
                      │  /api/    -> portal:8000              │
                      │  /preview -> kkfileview:8012          │
                      └───────┬───────────────┬──────────────┘
                              │               │
                     ┌────────▼──────┐  ┌─────▼──────────┐
                     │ portal        │  │ kkFileView     │
                     │ 上传/路由     │  │ LibreOffice    │
                     │ 转换 / OCR    │  │ 老格式转码     │
                     │ /data = tmpfs │  │ /tmp = tmpfs   │
                     └───────────────┘  └────────────────┘

  OFD 不进服务端：浏览器加载 fv-ofd.js 后本地解析渲染。
```

三个容器，一张 bridge 网络，只有 nginx 暴露端口。

## 快速开始

部署分**两步**：先在能联网的机器上做出离线包，再把包拷进内网安装。

```
 ┌──────────── 构建机（能上外网）────────────┐      ┌───────── 内网目标机（离线）─────────┐
 │ 1. clone 仓库                            │      │ 3. 校验 + 解压                       │
 │ 2. ./build/build-bundle.sh               │ ───► │ 4. deploy/install.sh                 │
 │    → dist/doclens-1.0.0-x86_64.tar.gz    │ 传输 │    → http://<内网IP>:8080            │
 └──────────────────────────────────────────┘      └──────────────────────────────────────┘
```

> 如果目标机本身能上网，或者公司有内网镜像仓库（Harbor 等），
> 两步可以在同一台机器上完成，不必拷包。

### 第 1 步：在构建机上出离线包

**构建机要求**：x86_64 Linux + Docker + **能访问镜像仓库**（Docker Hub 或内网 Harbor）
+ **能访问 PyPI**（构建 portal 镜像要 pip 装依赖）+ 磁盘 ≥ 10 GB。

```bash
git clone https://github.com/MiyauchiRenge/doclens.git
cd doclens
./build/build-bundle.sh
# 产物：dist/doclens-1.0.0-x86_64.tar.gz（约 1.1 GB）
```

构建脚本会拉镜像、构建 portal、**跑一次健康检查冒烟**再打包——
任一步失败就停下，不会产出「看起来成功但装不上」的包。

常见变体：

```bash
# 镜像已在本地（例如刚从内网仓库拉过），跳过拉取与构建
./build/build-bundle.sh --no-pull

# 把 Docker 的 RPM 也打进包（目标机没配 dnf 源时用）
./build/build-bundle.sh --with-rpms
```

若构建机只能连内网仓库，把 `versions.env` 里的镜像名改成内网地址即可；
pip 源可换国内镜像：

```bash
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  -t fileview/portal:1.0.0 services/portal
```

### 第 2 步：拷进内网安装

**目标机要求**：x86_64 / Docker 20.10+ / Compose v2 /
内存 ≥ 4 GB（2 GB 能跑但转码吃紧）/ 磁盘 ≥ 10 GB。

传输方式随意：U 盘、WinSCP、`scp` 都行。把 `.tar.gz` 和 `.sha256` 一起拷过去。

```bash
sha256sum -c doclens-1.0.0-x86_64.tar.gz.sha256
tar -xzf doclens-1.0.0-x86_64.tar.gz
cd doclens-1.0.0-x86_64

sudo bash deploy/preflight.sh          # 可选，只读体检
sudo bash deploy/install.sh --port 8080
```

装完访问 `http://<内网IP>:8080`。

> **目标机没有 Docker？** 三种解决方式见 [docs/DEPLOY.md](docs/DEPLOY.md#无-docker-的机器)：
> 用内网 dnf 源安装、把 RPM 打进包、或手动准备 RPM。

## 配置

改 `/opt/fileview/.env` 后 `docker compose up -d` 生效。

| 参数 | 默认 | 说明 |
|---|---|---|
| `HTTP_PORT` | 8080 | 对外端口 |
| `SESSION_TTL` | 1800 | 会话存活秒数，也是下载链接有效期 |
| `MAX_UPLOAD_MB` | 200 | 单文件上限 |
| `CONVERT_TIMEOUT` | 300 | PDF→Word 硬超时 |
| `CONVERT_CONCURRENCY` | 1 | 转换并发，CPU 密集，建议 1~2 |
| `TMPFS_SIZE` | 2g | portal 内存盘上限 |
| `KK_JAVA_OPTS` | `-Xmx768m` | kkFileView 堆大小，**≤4G 内存必须限制** |
| `OCR_TIMEOUT` | 1800 | OCR 任务超时 |
| `OCR_CONCURRENCY` | 1 | OCR 并发，**不要调大**，3~4G 机器会 OOM |

部署细节、端口修改、升级卸载见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 已知边界（重要）

**1. Office 预览不是 Word 渲染，是 LibreOffice 渲染。**

版式会有差异。文本框、图文环绕、表格边框、分页位置这些**补字体也修不掉**。
要 100% 一致，请在源端另存 PDF 再上传。

字体替换是最大的坑：项目内置 Carlito（替代 Calibri）、Caladea（替代 Cambria）
和一套 fontconfig 映射，把「等线」映射到黑体而非微软雅黑——因为雅黑的行高比
宋体高 32%，换错会导致整页行距被撑开。细节见 [fonts/README.md](fonts/README.md)。

**2. 扫描件 OCR 不还原版式。**

两个产物各有取舍：

- 可搜索 PDF：外观零损失，但只能搜索/复制
- 文字 Word：可编辑，但表格散成行、图片丢失、底色没有

要「连底色图片都还原成 Word」，开源没有高保真方案（详见 README 的 OCR 章节
与 [THIRD_PARTY.md](THIRD_PARTY.md)）。

**3. 无 Office 在线编辑。** 这是刻意的范围裁剪。

## 临时文件怎么处理

四层保障，满足「会话结束即销毁」：

1. **物理层**：portal 的 `/data`、kkFileView 的 `/tmp` 与转码目录全挂 **tmpfs**
2. **会话层**：每次上传独立目录，文件名随机化，原名只存内存索引
3. **触发层**：关闭页面用 `sendBeacon` 通知服务端立即清理
4. **兜底层**：`SESSION_TTL` 到期批量清理 + 每 30 秒扫孤儿目录

> 口径是「处理期间临时驻留、会话结束销毁」，**不是**物理不可恢复。
> 对内表述请如实。

## 文档

| 文件 | 内容 |
|---|---|
| [docs/DEPLOY.md](docs/DEPLOY.md) | 部署细节、端口、升级卸载、排障表 |
| [docs/VERIFY.md](docs/VERIFY.md) | 验证记录：哪些实测过、哪些没有、踩过哪些坑 |
| [THIRD_PARTY.md](THIRD_PARTY.md) | 依赖许可清单 + 发布前检查清单 |
| [fonts/README.md](fonts/README.md) | 字体替换原理与实测数据 |

## 许可证

**AGPL-3.0**，全文见 [LICENSE](LICENSE)。

选它的原因：核心功能依赖 **PyMuPDF**，而 PyMuPDF 是 AGPL-3.0（或 Artifex 商业授权）。
`pdf2docx` 依赖它，扫描件检测与 OCR 也直接调用它，因此整体需按 AGPL 分发。

- ✅ 可自由使用、修改、分发（含商用）
- ⚠️ 分发时须提供完整对应源码并保留同一许可
- ⚠️ **通过网络提供服务时**（第 13 条），须向使用者提供获取源码的方式

内网自用不对外分发时，保持源码仓库可访问通常即可满足第 13 条。
若需闭源商用，须购买 PyMuPDF 商业授权或替换掉它。

**源码获取（第 13 条）**：https://github.com/MiyauchiRenge/doclens

第三方组件许可见 [THIRD_PARTY.md](THIRD_PARTY.md)。
