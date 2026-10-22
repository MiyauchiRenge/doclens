#!/usr/bin/env bash
# =============================================================================
# install.sh —— 内网目标机（openEuler / 麒麟 x86_64）一键安装
#
# 用法：
#   sudo bash deploy/install.sh              # 默认 8080 端口
#   sudo bash deploy/install.sh --port 9080  # 指定端口
#   sudo bash deploy/install.sh --uninstall-like-keep-data   # 见 README
#
# 幂等：重复执行会重建容器，不重复装包。
# 前置：Docker 已安装（或 rpms/ 内有离线 RPM）
# =============================================================================
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE"

# shellcheck disable=SC1091
source "$BASE/versions.env"

VERSION="$(tr -d '[:space:]' < "$BASE/VERSION")"
INSTALL_DIR="${INSTALL_DIR:-/opt/fileview}"
HTTP_PORT_OVERRIDE=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port) HTTP_PORT_OVERRIDE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
if [ -n "$HTTP_PORT_OVERRIDE" ]; then HTTP_PORT="$HTTP_PORT_OVERRIDE"; fi

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
step() { printf '\n%s==>%s %s\n' "$C_OK" "$C_OFF" "$*"; }

# ---------------------------------------------------------------------------
# 0. 环境前置
# ---------------------------------------------------------------------------
[ "$(id -u)" = "0" ] || die "请用 root 执行：sudo bash deploy/install.sh"

ARCH="$(uname -m)"
[ "$ARCH" = "x86_64" ] || die "本包为 x86_64 构建，当前机器架构为 ${ARCH}，不能直接使用。"

if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  info "目标系统: ${PRETTY_NAME:-unknown} (${ARCH})"
else
  warn "无法识别 /etc/os-release，继续尝试。"
fi

step "检查 Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "未检测到 docker，尝试从 rpms/ 离线安装…"
  if ls "$BASE"/rpms/*.rpm >/dev/null 2>&1; then
    if command -v dnf >/dev/null 2>&1; then
      # 把 rpms/ 当作临时仓库，只安装需要的包，依赖由 dnf 从本地解析。
      # 直接 `dnf install rpms/*.rpm` 会把包里携带的系统基础包（bash/glib2 等）
      # 一并纳入事务，在版本略有差异的机器上有覆盖风险。
      if dnf install -y --disablerepo='*' \
           --repofrompath="local,$BASE/rpms" --enablerepo=local --nogpgcheck \
           docker-ce docker-ce-cli containerd.io docker-compose-plugin; then
        info "已通过本地仓库安装 Docker。"
      else
        warn "按包名安装失败，回退为安装目录内全部 RPM。"
        dnf install -y --disablerepo='*' --nogpgcheck "$BASE"/rpms/*.rpm || die "离线 RPM 安装失败。"
      fi
    elif command -v rpm >/dev/null 2>&1; then
      rpm -Uvh --nodeps "$BASE"/rpms/docker-ce*.rpm "$BASE"/rpms/containerd.io*.rpm 2>/dev/null \
        || die "离线 RPM 安装失败。"
    else
      die "系统没有 dnf/rpm，无法安装 Docker。"
    fi
  else
    cat >&2 <<'EOF'

[FAIL] 目标机没有 Docker，且包内 rpms/ 为空。

处理方式（任选其一）：
  A. 目标机若有内网 yum/dnf 源：dnf install -y docker docker-compose-plugin
  B. 在联网构建机执行 ./build/build-bundle.sh --with-rpms 后重新出包
  C. 手动把 docker-ce / containerd.io / docker-compose-plugin 的 rpm 放进 rpms/ 再执行本脚本

EOF
    exit 1
  fi
fi

systemctl enable --now docker >/dev/null 2>&1 || warn "systemctl 启动 docker 失败，请手动确认 docker 是否在运行。"
docker info >/dev/null 2>&1 || die "docker 守护进程不可用。"

# compose 命令探测（v2 插件优先，兼容 v1）
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  die "未找到 compose（docker compose 或 docker-compose）。请安装 docker-compose-plugin。"
fi
info "使用 compose 命令: ${DC}"

# ---------------------------------------------------------------------------
# 1. 部署目录
# ---------------------------------------------------------------------------
step "准备部署目录 ${INSTALL_DIR}"
mkdir -p "$INSTALL_DIR"
if [ "$BASE" != "$INSTALL_DIR" ]; then
  rm -rf "$INSTALL_DIR/compose" "$INSTALL_DIR/web" "$INSTALL_DIR/deploy"
  cp -r "$BASE/compose" "$BASE/web" "$BASE/deploy" "$INSTALL_DIR/"
  mkdir -p "$INSTALL_DIR/fonts"
  # 保留用户在目标机上已补的字体，不覆盖
  cp -rn "$BASE/fonts/." "$INSTALL_DIR/fonts/" 2>/dev/null || true
else
  info "部署目录即包目录，跳过自拷贝。"
fi
cp -f "$BASE/versions.env" "$INSTALL_DIR/versions.env"
cp -f "$BASE/VERSION" "$INSTALL_DIR/VERSION"
cp -f "$BASE/README.md" "$INSTALL_DIR/README.md" 2>/dev/null || true

# 生成 .env（已存在则保留用户改动，仅补齐缺省值）
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
  cat > "$ENV_FILE" <<EOF
# 由 install.sh 生成；修改后需重新执行 compose up -d
COMPOSE_PROJECT_NAME=fileview
HTTP_PORT=${HTTP_PORT}
SESSION_TTL=${SESSION_TTL}
MAX_UPLOAD_MB=${MAX_UPLOAD_MB}
CONVERT_TIMEOUT=${CONVERT_TIMEOUT}
CONVERT_CONCURRENCY=${CONVERT_CONCURRENCY}
OCR_TIMEOUT=${OCR_TIMEOUT}
OCR_CONCURRENCY=${OCR_CONCURRENCY}
TMPFS_SIZE=${TMPFS_SIZE}
KK_JAVA_OPTS="${KK_JAVA_OPTS}"
NGINX_IMAGE=${NGINX_IMAGE}
NGINX_VERSION=${NGINX_VERSION}
KK_IMAGE=${KK_IMAGE}
KK_VERSION=${KK_VERSION}
PORTAL_IMAGE=${PORTAL_IMAGE}
PORTAL_VERSION=${PORTAL_VERSION}
EOF
  info "已生成 ${ENV_FILE}"
else
  info "沿用已有 ${ENV_FILE}（如需改端口请编辑后重启）"
fi

# ---------------------------------------------------------------------------
# 2. 离线导入镜像
# ---------------------------------------------------------------------------
step "导入离线镜像"
shopt -s nullglob
TARS=("$BASE"/images/*.tar)
shopt -u nullglob

if [ ${#TARS[@]} -gt 0 ]; then
  for t in "${TARS[@]}"; do
    info "docker load <- $(basename "$t")"
    docker load -i "$t" >/dev/null || die "导入失败: $t"
  done
else
  # 支持两种场景：镜像已通过管道预加载，或从内网 Registry 直接存在
  warn "images/ 下没有 .tar，改为检查本地是否已存在所需镜像…"
  for ref in "${NGINX_IMAGE}:${NGINX_VERSION}" "${KK_IMAGE}:${KK_VERSION}" "${PORTAL_IMAGE}:${PORTAL_VERSION}"; do
    docker image inspect "$ref" >/dev/null 2>&1 \
      || die "缺少镜像 ${ref}：images/ 为空且本地不存在。请放入 tar 或先 docker load。"
  done
  info "本地已存在全部所需镜像，跳过导入。"
fi

for ref in "${NGINX_IMAGE}:${NGINX_VERSION}" "${KK_IMAGE}:${KK_VERSION}" "${PORTAL_IMAGE}:${PORTAL_VERSION}"; do
  docker image inspect "$ref" >/dev/null 2>&1 || die "镜像缺失: ${ref}（导入未成功或版本号不匹配）"
done
info "三个镜像均已就绪。"

# ---------------------------------------------------------------------------
# 3. 端口检查
# ---------------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :${HTTP_PORT} )" 2>/dev/null | grep -q ":${HTTP_PORT}"; then
    warn "端口 ${HTTP_PORT} 已被占用，安装仍继续；若启动失败请换端口重试：--port <PORT>"
  fi
fi

# ---------------------------------------------------------------------------
# 4. 启动
# ---------------------------------------------------------------------------
step "启动服务"
cd "$INSTALL_DIR/compose"
# 兼容 v1/v2：v2 用 --env-file，v1 需把 .env 放在当前目录
cp -f "$INSTALL_DIR/.env" .env

$DC up -d --remove-orphans

# ---------------------------------------------------------------------------
# 5. 健康检查
# ---------------------------------------------------------------------------
step "等待服务就绪"
READY=0
for i in $(seq 1 60); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${HTTP_PORT}/api/health" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  echo >&2
  warn "健康检查未通过（120 秒）。容器状态如下："
  $DC ps || true
  echo >&2
  warn "排查命令（在 ${INSTALL_DIR}/compose 下执行）："
  echo "  $DC logs --tail=100 portal" >&2
  echo "  $DC logs --tail=100 kkfileview" >&2
  echo "  $DC logs --tail=100 nginx" >&2
  die "安装未完成。"
fi

IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[ -n "$IP" ] || IP="<本机IP>"

cat <<EOF

${C_OK}================ 安装完成 ================${C_OFF}

访问地址 : http://${IP}:${HTTP_PORT}
部署目录 : ${INSTALL_DIR}
会话 TTL : ${SESSION_TTL} 秒（超时自动销毁临时文件）

常用操作（在 ${INSTALL_DIR}/compose 下）：
  状态 : ${DC} ps
  日志 : ${DC} logs -f portal
  重启 : ${DC} restart
  停止 : ${DC} down
  卸载 : sudo bash ${INSTALL_DIR}/deploy/uninstall.sh

提示：
  * 首次预览老格式（.doc/.xls）会触发 LibreOffice 转码，需数秒，后续命中内存缓存会快很多。
  * 临时文件存于容器 tmpfs，容器重启即清空，物理上不落持久盘。
EOF
