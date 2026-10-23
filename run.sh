#!/usr/bin/env bash
# =============================================================================
# run.sh —— 一键运行（适用于能访问镜像仓库与 PyPI 的机器）
#
#   bash run.sh                # 默认端口 8080
#   bash run.sh --port 7783
#   bash run.sh --check        # 只做检查与配置校验，不启动
#
# 做的事：检查环境 -> 生成 compose/.env -> 构建 portal 镜像 -> 启动 -> 健康检查
#
# 内网离线部署请勿使用本脚本，改用完整离线包 + deploy/install.sh，
# 详见 README「快速开始」。
# =============================================================================
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE"

CHECK_ONLY=0
PORT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_OFF=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
die()  { printf '%s[FAIL]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
step() { printf '\n%s==>%s %s\n' "$C_OK" "$C_OFF" "$*"; }

# shellcheck source=versions.env
. "$BASE/versions.env"

# ---------------------------------------------------------------------------
# 1. 环境检查
# ---------------------------------------------------------------------------
step "检查环境"
command -v docker >/dev/null 2>&1 || die "未安装 Docker。"
docker info >/dev/null 2>&1 || die "Docker 未运行，或当前用户无权限（试试 sudo）。"

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else die "未找到 compose 命令（需要 Docker Compose v2）。"; fi
info "docker: $(docker --version | cut -d, -f1)"
info "compose: $($DC version --short 2>/dev/null || echo v1)"

# ---------------------------------------------------------------------------
# 2. 生成 compose/.env
# ---------------------------------------------------------------------------
step "准备配置"
ENV_FILE="$BASE/compose/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$BASE/compose/.env.example" "$ENV_FILE"
  info "已由 .env.example 生成 compose/.env"
else
  info "沿用已有 compose/.env"
fi

# 端口：命令行 > 已有配置 > 8080
if [ -n "$PORT" ]; then
  sed -i "s/^HTTP_PORT=.*/HTTP_PORT=${PORT}/" "$ENV_FILE"
fi
PORT=$(grep -E '^HTTP_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2 | tr -d '" ')
PORT="${PORT:-8080}"

# 镜像坐标以 versions.env 为准，避免模板里的版本与构建版本不一致
sed -i "s|^PORTAL_IMAGE=.*|PORTAL_IMAGE=${PORTAL_IMAGE}|"   "$ENV_FILE"
sed -i "s|^PORTAL_VERSION=.*|PORTAL_VERSION=${PORTAL_VERSION}|" "$ENV_FILE"
sed -i "s|^KK_IMAGE=.*|KK_IMAGE=${KK_IMAGE}|"               "$ENV_FILE"
sed -i "s|^KK_VERSION=.*|KK_VERSION=${KK_VERSION}|"         "$ENV_FILE"
sed -i "s|^NGINX_IMAGE=.*|NGINX_IMAGE=${NGINX_IMAGE}|"      "$ENV_FILE"
sed -i "s|^NGINX_VERSION=.*|NGINX_VERSION=${NGINX_VERSION}|" "$ENV_FILE"

info "端口 ${PORT}，portal 镜像 ${PORTAL_IMAGE}:${PORTAL_VERSION}"

if [ "$CHECK_ONLY" = "1" ]; then
  step "校验 compose 配置"
  ( cd compose && $DC config >/dev/null && echo "  配置有效" )
  info "--check 结束，未启动任何容器。"
  exit 0
fi

# ---------------------------------------------------------------------------
# 3. 构建 portal 镜像
# ---------------------------------------------------------------------------
if docker image inspect "${PORTAL_IMAGE}:${PORTAL_VERSION}" >/dev/null 2>&1; then
  info "portal 镜像已存在，跳过构建（如需重建：docker rmi ${PORTAL_IMAGE}:${PORTAL_VERSION}）"
else
  step "构建 portal 镜像 ${PORTAL_VERSION}"
  docker build -t "${PORTAL_IMAGE}:${PORTAL_VERSION}" "$BASE/services/portal" \
    || die "构建失败。若在受限网络，可用 --build-arg PIP_INDEX_URL=<内网源> 重试。"
fi

# ---------------------------------------------------------------------------
# 4. 启动
# ---------------------------------------------------------------------------
step "启动容器"
( cd compose && $DC up -d )

# ---------------------------------------------------------------------------
# 5. 健康检查
# ---------------------------------------------------------------------------
step "健康检查（端口 ${PORT}）"
READY=0
for _ in $(seq 1 45); do
  if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
    READY=1; break
  fi
  sleep 2
done

if [ "$READY" != "1" ]; then
  ( cd compose && $DC ps ) || true
  die "启动未完成，查看日志：cd compose && $DC logs --tail=80"
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
IP="${IP:-<本机IP>}"
cat <<EOF

${C_OK}================ 已启动 ================${C_OFF}

  访问  : http://${IP}:${PORT}
  自检  : http://${IP}:${PORT}/diag.html
  停止  : cd compose && $DC down
  日志  : cd compose && $DC logs -f

  首次启动时 kkFileView 需要 30~60 秒完成初始化，
  期间预览 Office 文件可能返回 502，稍后重试即可。
EOF
