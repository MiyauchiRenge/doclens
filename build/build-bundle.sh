#!/usr/bin/env bash
# =============================================================================
# build-bundle.sh —— 在【联网】构建机（x86_64 Linux + Docker）上生成离线一键包
#
# 用法：
#   ./build/build-bundle.sh                 # 标准出包
#   ./build/build-bundle.sh --with-rpms     # 额外抓取 openEuler 离线 RPM（需配置 docker-ce 源）
#   ./build/build-bundle.sh --no-fonts      # 跳过字体收集
#
# 产物：dist/fileview-offline-<VERSION>-x86_64.tar.gz
# 该产物拷到内网目标机后，解压执行 deploy/install.sh 即可。
# =============================================================================
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE"

# shellcheck disable=SC1091
source "$BASE/versions.env"

VERSION="$(tr -d '[:space:]' < "$BASE/VERSION")"
ARCH="$(uname -m)"
DIST="$BASE/dist"
IMAGES="$BASE/images"
FONTS="$BASE/fonts"
RPMS="$BASE/rpms"
BUNDLE_NAME="fileview-offline-${VERSION}-${ARCH}"

WITH_RPMS=0
WITH_FONTS=1
NO_PULL=0
for arg in "$@"; do
  case "$arg" in
    --with-rpms) WITH_RPMS=1 ;;
    --no-fonts)  WITH_FONTS=0 ;;
    --no-pull)   NO_PULL=1 ;;
    -h|--help)   sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; exit 2 ;;
  esac
done

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 前置检查
# ---------------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || die "构建机没有 docker。请先安装并启动 Docker。"

if [ "$ARCH" != "x86_64" ]; then
  die "构建机架构为 ${ARCH}，本包按 x86_64 出包；跨架构请勿混用（内网目标机是 x86_64）。"
fi

docker info >/dev/null 2>&1 || die "docker 守护进程未运行，或当前用户无权限访问 docker。"

if ! docker compose version >/dev/null 2>&1 && ! command -v docker-compose >/dev/null 2>&1; then
  warn "构建机没有 compose 插件，不影响出包（目标机才需要）。"
fi

info "构建机: $(uname -srm) / docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo unknown)"
info "目标版本: ${VERSION} (${ARCH})"

# ---------------------------------------------------------------------------
# 1. 拉取基础镜像 + 构建 portal
#
# 这里是全流程唯一的外部依赖点。任一 tag 不存在会立刻报错停下，
# 不会生成一个"看起来成功但装不上"的包。
# ---------------------------------------------------------------------------
info "拉取基础镜像…"
if [ "$NO_PULL" = "1" ]; then
  info "已指定 --no-pull：跳过拉取与构建，直接使用本地镜像"
  for ref in "${NGINX_IMAGE}:${NGINX_VERSION}" "${KK_IMAGE}:${KK_VERSION}" "${PORTAL_IMAGE}:${PORTAL_VERSION}"; do
    docker image inspect "$ref" >/dev/null 2>&1 \
      || die "本地缺少镜像 ${ref}，无法用 --no-pull 出包。"
  done
  info "本地三个镜像均存在。"
else
  docker pull "${NGINX_IMAGE}:${NGINX_VERSION}" || die "拉取 ${NGINX_IMAGE}:${NGINX_VERSION} 失败 —— 请核对 versions.env 中的 tag 是否真实存在。"
  docker pull "${KK_IMAGE}:${KK_VERSION}"       || die "拉取 ${KK_IMAGE}:${KK_VERSION} 失败 —— 请核对 versions.env 中的 tag 是否真实存在。"
  docker pull "${PYTHON_IMAGE}:${PYTHON_VERSION}" || die "拉取 ${PYTHON_IMAGE}:${PYTHON_VERSION} 失败。"

  info "构建 portal 镜像…"
  docker build \
    --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE}" \
    --build-arg "PYTHON_VERSION=${PYTHON_VERSION}" \
    -t "${PORTAL_IMAGE}:${PORTAL_VERSION}" \
    "$BASE/services/portal"

  # 冒烟验证：镜像能启动、健康接口通。避免把坏镜像打进包里。
  info "portal 镜像冒烟测试…"
  SMOKE_ID="$(docker run -d --rm \
    -e DATA_DIR=/data \
    --tmpfs /data:rw,size=64m \
    "${PORTAL_IMAGE}:${PORTAL_VERSION}" 2>/dev/null)" || die "portal 镜像无法启动。"

  ok=0
  for _ in $(seq 1 30); do
    if docker exec "$SMOKE_ID" python -c \
        "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2).status==200 else 1)" >/dev/null 2>&1; then
      ok=1; break
    fi
    sleep 2
  done
  docker stop "$SMOKE_ID" >/dev/null 2>&1 || true
  [ "$ok" = "1" ] || die "portal 冒烟测试失败：健康接口 60 秒内未就绪，请查看 docker logs。"

  info "portal 冒烟测试通过。"
fi

# ---------------------------------------------------------------------------
# 2. 导出镜像
# ---------------------------------------------------------------------------
rm -rf "$IMAGES"
mkdir -p "$IMAGES"

save_image() {
  local ref="$1" out="$2"
  info "导出 ${ref} -> images/${out}"
  docker save -o "$IMAGES/$out" "$ref"
}

save_image "${NGINX_IMAGE}:${NGINX_VERSION}"   "nginx.tar"
save_image "${KK_IMAGE}:${KK_VERSION}"         "kkfileview.tar"
save_image "${PORTAL_IMAGE}:${PORTAL_VERSION}" "portal.tar"

# 基础镜像不必导出（portal 是最终镜像，python 只是构建期依赖）

# ---------------------------------------------------------------------------
# 3. 收集中文字体（可跳过）
#
# 老 Office 文档对宋体/仿宋依赖极重，字体缺失会直接让转码结果变形。
# 优先从构建机本地拷贝，其次尝试下载开源字体。
# 只收开源字体：商业字体（宋体/微软雅黑）不可随包分发。
# ---------------------------------------------------------------------------
if [ "$WITH_FONTS" = "1" ]; then
  mkdir -p "$FONTS"
  if find "$FONTS" -type f \( -name '*.ttf' -o -name '*.ttc' -o -name '*.otf' \) | grep -q .; then
    info "fonts/ 已有字体文件，跳过收集。"
  else
    info "尝试从构建机本地收集 CJK 字体…"
    local_count=0
    for d in /usr/share/fonts/opentype/noto /usr/share/fonts/truetype/noto /usr/share/fonts/truetype/wqy /usr/share/fonts/wqy-zenhei /usr/share/fonts/opentype/source-han-sans; do
      [ -d "$d" ] || continue
      while IFS= read -r f; do
        cp -f "$f" "$FONTS/" 2>/dev/null && local_count=$((local_count + 1))
      done < <(find "$d" -maxdepth 2 -type f \
                 \( -iname '*cjk*' -o -iname '*wqy*' -o -iname '*han*' \
                    -o -iname '*hei*' -o -iname '*song*' \) 2>/dev/null)
    done

    if [ "$local_count" = "0" ]; then
      warn "本地未找到 CJK 字体，尝试下载 Noto Sans CJK（开源许可）…"
      for url in \
        "https://github.com/notofonts/noto-cjk/raw/main/Sans/OTC/NotoSansCJK-Regular.ttc" \
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTC/NotoSansCJK-Regular.ttc" ; do
        if curl -fsSL --connect-timeout 15 -o "$FONTS/NotoSansCJK-Regular.ttc" "$url" 2>/dev/null; then
          local_count=1; break
        fi
      done
    fi

    if [ "$local_count" = "0" ]; then
      warn "字体收集失败。包仍可构建，但老 Office 文档转码可能出现字体替换。"
      warn "补救：手动把 .ttf/.ttc 放进 fonts/ 后重新运行本脚本（或直接在目标机补入）。"
      rm -f "$FONTS/NotoSansCJK-Regular.ttc"
    else
      info "已收集 $local_count 个字体文件。"
    fi
  fi
  if [ -d "$FONTS" ] && [ -z "$(ls -A "$FONTS" 2>/dev/null)" ]; then
    printf '%s\n' "占位文件：把开源中文字体（.ttf/.ttc/.otf）放到本目录，重建容器后生效。" > "$FONTS/.keep"
  fi
else
  warn "已跳过字体收集（--no-fonts）。老格式转码的字体还原度可能下降。"
fi

# ---------------------------------------------------------------------------
# 4. 可选：抓取 openEuler 离线 RPM（目标机没装 Docker 时用）
# ---------------------------------------------------------------------------
if [ "$WITH_RPMS" = "1" ]; then
  mkdir -p "$RPMS"
  if command -v dnf >/dev/null 2>&1; then
    info "抓取 Docker 相关 RPM（需要构建机已配置 docker-ce 或 openEuler 相应源）…"
    dnf download --resolve --alldeps --destdir "$RPMS" \
      docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin \
      2>/dev/null || \
    dnf download --resolve --alldeps --destdir "$RPMS" \
      docker docker-compose 2>/dev/null || \
    warn "RPM 抓取失败：目标机需自备 Docker 离线源，或在联网机手动准备 rpm 到 rpms/。"
  else
    warn "构建机没有 dnf，跳过 RPM 抓取。"
  fi
  if [ -z "$(ls -A "$RPMS" 2>/dev/null)" ]; then
    printf '%s\n' "占位文件：把 Docker/containerd/compose 的 .rpm 放到本目录，install.sh 会自动安装。" > "$RPMS/.keep"
  fi
fi

# ---------------------------------------------------------------------------
# 5. 组装并打包
# ---------------------------------------------------------------------------
info "组装离线包…"
rm -rf "$DIST"
mkdir -p "$DIST/$BUNDLE_NAME"

cp -r "$BASE/compose" "$BASE/deploy" "$BASE/web" "$BASE/services" "$BASE/fonts" \
      "$BASE/docs" "$BASE/tools" \
      "$BASE/versions.env" "$BASE/VERSION" "$BASE/README.md" "$DIST/$BUNDLE_NAME/"
mkdir -p "$DIST/$BUNDLE_NAME/images"
cp -f "$IMAGES"/*.tar "$DIST/$BUNDLE_NAME/images/"
[ -d "$RPMS" ] && cp -r "$RPMS" "$DIST/$BUNDLE_NAME/" || mkdir -p "$DIST/$BUNDLE_NAME/rpms"

chmod +x "$DIST/$BUNDLE_NAME/deploy/"*.sh

cat > "$DIST/$BUNDLE_NAME/MANIFEST.txt" <<EOF
包名      : $BUNDLE_NAME
版本      : $VERSION
构建时间  : $(date '+%Y-%m-%d %H:%M:%S %z')
构建主机  : $(uname -srm)
镜像      : ${NGINX_IMAGE}:${NGINX_VERSION}
            ${KK_IMAGE}:${KK_VERSION}
            ${PORTAL_IMAGE}:${PORTAL_VERSION}
镜像体积  : $(du -sh "$DIST/$BUNDLE_NAME/images" | cut -f1)
字体数量  : $(find "$FONTS" -type f \( -name '*.ttf' -o -name '*.ttc' -o -name '*.otf' \) 2>/dev/null | wc -l)
EOF

info "生成压缩包（校验和）…"
( cd "$DIST" && tar -czf "${BUNDLE_NAME}.tar.gz" "$BUNDLE_NAME" )
( cd "$DIST" && sha256sum "${BUNDLE_NAME}.tar.gz" > "${BUNDLE_NAME}.tar.gz.sha256" )

SIZE="$(du -h "$DIST/${BUNDLE_NAME}.tar.gz" | cut -f1)"
cat <<EOF

${C_OK}================ 出包完成 ================${C_OFF}
产物 : dist/${BUNDLE_NAME}.tar.gz  (${SIZE})
校验 : dist/${BUNDLE_NAME}.tar.gz.sha256

内网目标机（openEuler / 麒麟 x86_64）上：
  1) 上传该 tar.gz 与 .sha256
  2) tar -xzf ${BUNDLE_NAME}.tar.gz && cd ${BUNDLE_NAME}
  3) sudo bash deploy/install.sh

细节与排障见包内 README.md
EOF
