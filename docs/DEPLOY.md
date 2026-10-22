# 部署与运维

面向运维人员的细节文档。快速上手看 [README](../README.md)。

## 目录

- [目录结构](#目录结构)
- [构建机要求](#构建机要求)
- [安装](#安装)
- [改端口](#改端口)
- [参数详解](#参数详解)
- [临时文件的生命周期](#临时文件的生命周期)
- [升级与卸载](#升级与卸载)
- [无 Docker 的机器](#无-docker-的机器)
- [排障](#排障)
- [资源占用](#资源占用)

---

## 目录结构

```
doclens/
├── VERSION / versions.env     版本与镜像坐标（唯一需要改的地方）
├── LICENSE / README.md
├── THIRD_PARTY.md             依赖许可清单
├── docs/                      部署与验证文档
├── tools/
│   ├── check.sh               静态自检（shell/python/yaml/前端语法）
│   ├── smoke_test.py          服务逻辑冒烟测试
│   └── convert_test.py        真实 PDF→Word 转换测试
├── build/build-bundle.sh      联网构建机：出离线包
├── deploy/
│   ├── preflight.sh           目标机：安装前体检（只读）
│   ├── install.sh             目标机：一键安装
│   └── uninstall.sh           目标机：停止/清理
├── compose/
│   ├── docker-compose.yml     三容器编排
│   └── .env.example           运行时参数说明
├── web/
│   ├── nginx.conf             路由与安全头
│   └── html/                  前端（零外部依赖，无 CDN）
│       └── vendor/ofd/        flyfish OFD 渲染器 + 其 Apache-2.0 许可证
├── services/portal/           portal 服务源码 + Dockerfile
├── fonts/                     Carlito / Caladea + fontconfig 映射
├── rpms/                      Docker 离线 RPM（可选）
└── images/                    出包后生成的镜像 tar（不入库）
```

## 构建机要求

- x86_64 Linux
- Docker（能访问镜像仓库）
- 能访问 PyPI（构建 portal 镜像时 pip 要装依赖）
- 磁盘 ≥ 10 GB 空闲（镜像 tar 约 2.3 GB，压缩包约 1.1 GB）

若构建机访问 Docker Hub 受限，可把 `versions.env` 里的镜像名改成内网仓库地址，
例如 `NGINX_IMAGE="harbor.local/library/nginx"`，后续脚本无需改动。

**已在目标服务器上构建过的情况**，可用 `--no-pull` 跳过拉取与构建，直接用本地镜像出包：

```bash
./build/build-bundle.sh --no-pull
```

## 安装

```bash
sha256sum -c doclens-1.0.0-x86_64.tar.gz.sha256
tar -xzf doclens-1.0.0-x86_64.tar.gz
cd doclens-1.0.0-x86_64

sudo bash deploy/preflight.sh            # 只读体检，建议先跑
sudo bash deploy/install.sh              # 默认 8080
sudo bash deploy/install.sh --port 7783  # 指定端口
```

`install.sh` 会做：检查 root/架构/Docker → 复制到 `/opt/fileview` →
生成 `.env` → 导入镜像 → 启动 → 健康检查（120 秒）。

若 `images/` 为空但本地已有三个镜像（例如通过 registry 预先拉取），脚本会自动跳过导入。

## 改端口

不用重装，两步：

```bash
cd /opt/fileview/compose
sed -i 's/^HTTP_PORT=.*/HTTP_PORT=7783/' .env
docker compose up -d
```

端口冲突排查：`ss -ltnp | grep -w 7783`。

## 参数详解

改 `/opt/fileview/.env`，改完 `docker compose up -d` 生效。

| 参数 | 默认 | 说明 |
|---|---|---|
| `HTTP_PORT` | 8080 | 对外端口 |
| `SESSION_TTL` | 1800 | 会话存活秒数，也是下载令牌有效期 |
| `MAX_UPLOAD_MB` | 200 | 单文件上传上限 |
| `CONVERT_TIMEOUT` | 300 | PDF→Word 硬超时，超时强杀子进程 |
| `CONVERT_CONCURRENCY` | 1 | 并发转换数，CPU 密集，建议 1~2 |
| `TMPFS_SIZE` | 2g | portal 内存盘上限，按并发大文件调整 |
| `KK_JAVA_OPTS` | `-Xmx768m` | kkFileView 的 JVM 堆。**≤4G 内存必须限制**，≥8G 可放宽到 `-Xmx1536m` |
| `OCR_TIMEOUT` | 1800 | OCR 单任务超时 |
| `OCR_CONCURRENCY` | 1 | OCR 并发，**固定 1**，3~4G 机器同时跑两个会 OOM |
| `KK_OFFICE_PREVIEW_TYPE` | `image` | Office 预览方式。`image` 稳定；`pdf` 是矢量但**实测 pdf.js 显示 0/0，未通过验证** |

## 临时文件的生命周期

1. **物理层**：portal 的 `/data`、kkFileView 的 `/tmp` 与 `/opt/kkFileView-4.1.0/file`
   全部挂 **tmpfs**（内存盘），容器停止或宿主重启，磁盘上不留东西。
   > 注意：kkFileView 的转码产物原本落在容器可写层（重启才清），
   > 已改为 tmpfs。实测重启前 13 个文件 → 重启后 0 个。
2. **会话层**：每次上传生成独立会话目录，存储文件名随机化，原始文件名只存内存索引。
3. **触发层**：关闭页面（`pagehide`/`beforeunload`）用 `sendBeacon` 通知服务端立即清理；
   "关闭并清理"按钮同理。
4. **兜底层**：`SESSION_TTL` 到期批量清理；每 30 秒扫一次磁盘上没有索引的孤儿目录。

转换产物走**短时效下载令牌**：会话有效期内可重复下载，会话关闭或 TTL 到期即删除。
（早期版本做成"一次性令牌"，浏览器重复请求就 410，体验很差，已改掉。）

> **口径提醒**：以上是"处理期间临时驻留、会话结束销毁"，不是"物理不可恢复"。
> 对内表述请如实，不要宣称"零存储"。

## 升级与卸载

**升级**：替换 `images/` 下的 tar 或改 `versions.env` 的版本号，然后

```bash
cd /opt/fileview/compose
docker compose up -d --force-recreate
```

**停止**：`docker compose down`

**卸载**（保留镜像）：`sudo bash /opt/fileview/deploy/uninstall.sh`

**彻底清理**（含镜像）：`sudo bash /opt/fileview/deploy/uninstall.sh --purge`

## 无 Docker 的机器

三种方式：

1. 目标机若能连内网 dnf 源：`dnf install -y docker-ce docker-compose-plugin`
2. 在能访问源的同版本机器上抓包，放进包的 `rpms/` 目录，`install.sh` 会自动安装：
   ```bash
   dnf download --resolve --alldeps --destdir rpms/ \
     docker-ce docker-ce-cli containerd.io docker-compose-plugin
   ```
   然后重新出包：`./build/build-bundle.sh --with-rpms`
3. 手动准备 RPM 放到 `rpms/`

> **注意**：openEuler 各版本、麒麟 V10 的包名与依赖并不完全一致。
> 最稳妥是在**与目标机同版本**的系统上抓包。

## 排障

| 现象 | 排查方向 |
|---|---|
| 安装卡在"等待服务就绪" | `docker compose logs portal`；多为 tmpfs 不足或端口占用 |
| 预览老格式一直转圈 | kkFileView 转码慢或队列堵塞：`docker compose logs kkfileview` |
| kkFileView 反复重启、JVM `fatal error` 或 `unable to allocate file descriptor table` | 宿主 `nr_open` 过大被继承；确认 compose 的 `ulimits.nofile: 65535` 生效 |
| 日志报 `started but its pid could not be found` + 出现 `<defunct>` soffice | 容器内 PID 1 不回收子进程；确认 `init: true` 生效 |
| 内存小的机器上服务被 OOM 或转码时卡死 | 调小 `KK_JAVA_OPTS` 的 `-Xmx`，并降低 `CONVERT_CONCURRENCY` |
| DOCX/XLSX 一直 loading、图片不显示 | 代理丢了端口：kkFileView 用 `Host` 头拼图片绝对地址，`$host` 会丢端口导致浏览器去连 80。nginx 已改用 `$http_host`，勿改回 |
| OFD 打不开 / 白屏 | 浏览器控制台看 `/vendor/ofd/fv-ofd.js` 是否 404；该文件随包提供 |
| OFD 显示"不支持当前文件格式" | 预览 URL 丢了 `.ofd` 后缀。渲染器靠**后缀**判定类型，勿改成无后缀形式 |
| OFD 能打开但签章不显示 | 前端渲染器对国密签名的支持有限 |
| kkFileView 预览页白屏/样式错乱 | 其资源用了绝对路径；nginx 已配 `@kk_fallback` 兜底转发 |
| 转出的 PDF 中文变方块 | 字体缺失，见 [fonts/README.md](../fonts/README.md) |
| 字看起来比原文大、行距变松 | 字体映射错了。检查 `99-font-alias.conf`，尤其是「等线」不能映射到微软雅黑 |
| 转 Word 报"转换超时" | 该 PDF 可能损坏或页数过多，可调大 `CONVERT_TIMEOUT` |
| 转 Word 提示"扫描件" | 正常行为，改用 OCR（两种产物任选） |
| 下载链接失效 | 令牌随会话清理/过期了；重新点一次转换 |
| SELinux 阻止挂载 | `getenforce`；必要时对部署目录打标签 |
| 麒麟 KYSEC 拦截 | 检查安全策略；麒麟特有机制，报错通常很隐晦 |

## 资源占用

实测环境：openEuler 22.03 LTS-SP3 / 3.4 GB 内存 / Docker 26.1.3。

| 指标 | 数值 |
|---|---|
| 三容器稳态内存 | 约 1.0 GB |
| 镜像体积 | kkFileView 1.6 GB、portal 689 MB、nginx 48 MB |
| 磁盘占用（含镜像） | 约 11 GB |
| PDF→Word（4 页测试文档） | 3.2 秒 |
| OCR（单页中文） | 8~12 秒（含模型加载） |
| OCR（5 页） | 29 秒 → 稳态约 5 秒/页 |
