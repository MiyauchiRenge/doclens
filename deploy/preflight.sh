#!/usr/bin/env bash
# =============================================================================
# preflight.sh —— 安装前环境体检（只读，不改系统）
#
# 用法：  sudo bash deploy/preflight.sh
#
# 目的：在真正 install 之前，把"装到一半才发现不行"的问题提前暴露。
# =============================================================================
set -uo pipefail

BASE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BASE"

PASS=0; FAIL=0; WARN=0; SKIP=0
ok()   { printf '  \033[32m[ OK ]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m[FAIL]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33m[WARN]\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
head_() { printf '\n\033[1m%s\033[0m\n' "$*"; }

head_ "1. 系统与架构"
if [ -f /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  ok "系统: ${PRETTY_NAME:-unknown}"
else
  warn "无法读取 /etc/os-release"
fi

ARCH="$(uname -m)"
if [ "$ARCH" = "x86_64" ]; then ok "架构: x86_64"; else bad "架构: ${ARCH}（本包要求 x86_64）"; fi
ok "内核: $(uname -r)"

if [ "$(id -u)" = "0" ]; then ok "以 root 运行"; else warn "非 root（安装阶段需要 root）"; fi

head_ "2. glibc 与基础能力"
GLIBC="$(ldd --version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+$' || echo unknown)"
ok "glibc: ${GLIBC}"
command -v curl >/dev/null 2>&1 && ok "curl 存在" || warn "curl 缺失（健康检查会用到，建议安装）"
command -v ss >/dev/null 2>&1 && ok "ss 存在" || warn "ss 缺失（端口检查会跳过）"

head_ "3. Docker"
if command -v docker >/dev/null 2>&1; then
  ok "docker: $(docker --version 2>/dev/null | head -1)"
  if docker info >/dev/null 2>&1; then
    ok "docker 守护进程可访问"
    ok "存储驱动: $(docker info --format '{{.Driver}}' 2>/dev/null)"
  else
    bad "docker 守护进程不可访问（未启动或权限不足）"
  fi
else
  bad "未安装 docker"
  if ls "$BASE"/rpms/*.rpm >/dev/null 2>&1; then
    ok "包内存在离线 RPM：$(ls "$BASE"/rpms/*.rpm | wc -l) 个"
  else
    warn "包内 rpms/ 为空，需自备离线源"
  fi
fi

if docker compose version >/dev/null 2>&1; then
  ok "compose: $(docker compose version --short 2>/dev/null)"
elif command -v docker-compose >/dev/null 2>&1; then
  warn "仅找到 docker-compose v1（可用，但建议装 compose 插件）"
else
  bad "未找到 compose"
fi

head_ "4. 内核 cgroup"
if [ -f /sys/fs/cgroup/cgroup.controllers ]; then ok "cgroup v2"
elif [ -d /sys/fs/cgroup/memory ]; then ok "cgroup v1"
else warn "无法判定 cgroup 版本"; fi

head_ "5. 安全模块"
SELINUX_STATE="$(getenforce 2>/dev/null || echo 'not-installed')"
case "$SELINUX_STATE" in
  Enforcing) warn "SELinux=Enforcing：若挂载报权限错误，需对部署目录打标签或调整策略" ;;
  Permissive) ok "SELinux=Permissive" ;;
  Disabled|not-installed) ok "SELinux 未强制" ;;
  *) warn "SELinux 状态未知: $SELINUX_STATE" ;;
esac
if [ -e /sys/kernel/security/kysec/status ] || command -v kysec-tool >/dev/null 2>&1; then
  warn "检测到麒麟安全模块（KYSEC）：可能拦截未签名程序/挂载，安装失败时优先查这里"
else
  ok "未检测到 KYSEC"
fi

head_ "6. 资源"
MEM_MB="$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)"
if [ "${MEM_MB:-0}" -ge 3800 ]; then
  ok "内存: ${MEM_MB} MB"
elif [ "${MEM_MB:-0}" -ge 1900 ]; then
  warn "内存: ${MEM_MB} MB（偏低，LibreOffice 转码时可能吃紧，建议 ≥4G）"
else
  bad "内存: ${MEM_MB} MB（不足，需 ≥4G）"
fi

AVAIL_GB="$(df -Pk "$BASE" 2>/dev/null | awk 'NR==2 {printf "%d", $4/1024/1024}')"
if [ "${AVAIL_GB:-0}" -ge 10 ]; then ok "可用磁盘: ${AVAIL_GB} GB"
else warn "可用磁盘: ${AVAIL_GB} GB（建议 ≥10G）"; fi

head_ "7. 包完整性"
for f in compose/docker-compose.yml web/nginx.conf web/html/index.html services/portal/app/main.py versions.env; do
  [ -f "$BASE/$f" ] && ok "存在 $f" || bad "缺失 $f"
done
shopt -s nullglob
TARS=("$BASE"/images/*.tar)
shopt -u nullglob
if [ ${#TARS[@]} -ge 3 ]; then
  ok "镜像包: ${#TARS[@]} 个"
  for t in "${TARS[@]}"; do printf '        %s (%s)\n' "$(basename "$t")" "$(du -h "$t" | cut -f1)"; done
else
  bad "镜像包不足（需 3 个，实际 ${#TARS[@]} 个）—— 包可能未构建或不完整"
fi

FONT_N="$(find "$BASE/fonts" -type f \( -name '*.ttf' -o -name '*.ttc' -o -name '*.otf' \) 2>/dev/null | wc -l)"
if [ "$FONT_N" -gt 0 ]; then ok "字体文件: ${FONT_N} 个"
else warn "无中文字体：老 Office 文档转码可能出现字体替换"; fi

head_ "8. 端口"
PORT="${HTTP_PORT:-8080}"
if command -v ss >/dev/null 2>&1 && ss -ltn "( sport = :${PORT} )" 2>/dev/null | grep -q ":${PORT}"; then
  warn "端口 ${PORT} 已被占用，安装时建议 --port 换个端口"
else
  ok "端口 ${PORT} 空闲"
fi

echo
printf '\033[1m结果：\033[0m %d 通过, %d 警告, %d 失败\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  echo "存在失败项，请先解决再执行 deploy/install.sh"
  exit 1
fi
echo "可以执行：sudo bash deploy/install.sh"
