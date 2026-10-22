#!/usr/bin/env bash
# =============================================================================
# uninstall.sh —— 停止并清理部署
#
# 用法：
#   sudo bash uninstall.sh           # 停止容器、删除部署目录（镜像保留）
#   sudo bash uninstall.sh --purge   # 同时删除本包导入的三个镜像
#
# 注意：临时文件在 tmpfs，容器一停即消失，无需额外清理。
# =============================================================================
set -euo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE"

# shellcheck disable=SC1091
[ -f "$BASE/.env" ] && source "$BASE/.env"
[ -f "$BASE/versions.env" ] && source "$BASE/versions.env"

INSTALL_DIR="${INSTALL_DIR:-/opt/fileview}"
PURGE=0
if [ "${1:-}" = "--purge" ]; then PURGE=1; fi

C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_OFF=$'\033[0m'
info() { printf '%s[INFO]%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s[WARN]%s %s\n' "$C_WARN" "$C_OFF" "$*"; }

[ "$(id -u)" = "0" ] || { echo "请用 root 执行"; exit 1; }

if docker compose version >/dev/null 2>&1; then DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then DC="docker-compose"
else DC=""; fi

if [ -n "$DC" ] && [ -f "$INSTALL_DIR/compose/docker-compose.yml" ]; then
  info "停止容器…"
  ( cd "$INSTALL_DIR/compose" && $DC down --remove-orphans ) || warn "compose down 失败，继续清理。"
fi

if [ "$PURGE" = "1" ]; then
  info "删除镜像…"
  docker rmi \
    "${NGINX_IMAGE:-nginx}:${NGINX_VERSION:-1.27-alpine}" \
    "${KK_IMAGE:-keking/kkfileview}:${KK_VERSION:-4.4.0}" \
    "${PORTAL_IMAGE:-fileview/portal}:${PORTAL_VERSION:-1.0.0}" \
    2>/dev/null || warn "部分镜像删除失败（可能被其他容器使用）。"
fi

if [ -d "$INSTALL_DIR" ]; then
  info "删除部署目录 ${INSTALL_DIR}"
  rm -rf "$INSTALL_DIR"
fi

info "卸载完成。"
if [ "$PURGE" = "0" ]; then
  echo "镜像仍保留在本地；需要彻底清理请加 --purge。"
fi
