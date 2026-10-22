#!/usr/bin/env bash
# 本地冒烟测试运行器：必要时安装测试依赖，然后跑 tools/smoke_test.py。
# 不启动 Docker、不联网部署，只在开发机验证 portal 逻辑。
set -u
cd "$(dirname "$0")/.." || exit 1

echo "== 检查测试依赖 =="
if ! python3 -c 'import fastapi, httpx, multipart' >/dev/null 2>&1; then
  echo "  缺少依赖，尝试安装（需要网络）…"
  python3 -m pip install --quiet --user fastapi uvicorn python-multipart httpx || {
    echo "  依赖安装失败，无法运行冒烟测试。"
    exit 2
  }
fi
echo "  依赖就绪"

echo "== 运行冒烟测试 =="
python3 tools/smoke_test.py
