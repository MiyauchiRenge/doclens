#!/usr/bin/env bash
# 本地自检脚本：只做静态语法检查，不产生任何部署动作。
set -u

cd "$(dirname "$0")/.." || exit 1

fail=0

echo "=== bash 语法检查 ==="
for f in build/*.sh deploy/*.sh; do
  if bash -n "$f" 2>/tmp/bn.err; then
    echo "  OK   $f"
  else
    echo "  FAIL $f"
    sed 's/^/       /' /tmp/bn.err
    fail=1
  fi
done

echo "=== python 语法检查 ==="
if command -v python3 >/dev/null 2>&1; then
  if python3 -m py_compile services/portal/app/main.py services/portal/app/convert_worker.py; then
    echo "  OK   services/portal/app/*.py"
  else
    echo "  FAIL python"
    fail=1
  fi
else
  echo "  SKIP 无 python3"
fi

echo "=== YAML 检查 ==="
if python3 -c 'import yaml' >/dev/null 2>&1; then
  python3 - <<'PY'
import yaml, sys
with open('compose/docker-compose.yml', encoding='utf-8') as f:
    d = yaml.safe_load(f)
svcs = d.get('services', {})
print('  OK   services:', ', '.join(sorted(svcs)))
for name, s in svcs.items():
    img = s.get('image', '')
    if '${' not in img:
        print(f'  WARN {name} image 未使用变量: {img}')
PY
else
  echo "  SKIP 无 pyyaml"
fi

echo "=== 关键文件存在性 ==="
for f in VERSION versions.env README.md \
         compose/docker-compose.yml compose/.env.example \
         web/nginx.conf web/html/index.html web/html/app.js web/html/style.css \
         services/portal/Dockerfile services/portal/requirements.txt \
         services/portal/app/main.py services/portal/app/convert_worker.py \
         build/build-bundle.sh deploy/install.sh deploy/preflight.sh deploy/uninstall.sh \
         fonts/README.md rpms/README.md; do
  if [ -f "$f" ]; then
    echo "  OK   $f"
  else
    echo "  MISS $f"
    fail=1
  fi
done

echo
if [ "$fail" -eq 0 ]; then
  echo "自检通过"
else
  echo "自检存在失败项"
fi
exit "$fail"
