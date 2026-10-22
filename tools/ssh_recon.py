"""内网目标机侦察：只读命令，不改动系统。

用法（凭据从环境变量读取，不落盘）：
    DSH_HOST / DSH_USER / DSH_PASS python tools/ssh_recon.py
"""

from __future__ import annotations

import os
import sys

import paramiko

HOST = os.environ.get("DSH_HOST", "")
USER = os.environ.get("DSH_USER", "")
PASS = os.environ.get("DSH_PASS", "")

CMDS = [
    ("系统", "cat /etc/os-release 2>/dev/null | head -4"),
    ("内核/架构", "uname -srm"),
    ("内存", "free -m | head -2"),
    ("磁盘 /opt", "df -h /opt 2>/dev/null || df -h /"),
    ("Docker", "docker --version 2>/dev/null || echo NO_DOCKER"),
    ("Compose", "docker compose version 2>/dev/null || docker-compose --version 2>/dev/null || echo NO_COMPOSE"),
    ("Docker 守护", "docker info --format '{{.ServerVersion}} {{.Driver}}' 2>/dev/null || echo DAEMON_DOWN"),
    ("8080 占用", "ss -ltn 2>/dev/null | grep -w 8080 || echo PORT_FREE"),
    ("SELinux", "getenforce 2>/dev/null || echo NOT_INSTALLED"),
    ("KYSEC", "ls /sys/kernel/security/kysec/status 2>/dev/null || command -v kysec-tool || echo NO_KYSEC"),
    ("已有部署", "ls -d /opt/fileview 2>/dev/null || echo NONE"),
    ("已有镜像", "docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | head -10 || true"),
    ("中文字体", "fc-list :lang=zh 2>/dev/null | wc -l"),
]


def main() -> int:
    if not HOST or not USER:
        print("缺少 DSH_HOST / DSH_USER", file=sys.stderr)
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, port=22, username=USER, password=PASS, timeout=15,
                       allow_agent=False, look_for_keys=False)
    except Exception as exc:  # noqa: BLE001
        print(f"连接失败: {type(exc).__name__}: {exc}")
        return 1

    print(f"已连接 {USER}@{HOST}\n")
    for label, cmd in CMDS:
        _, out, err = client.exec_command(cmd, timeout=30)
        text = (out.read() + err.read()).decode("utf-8", "replace").strip()
        print(f"[{label}]")
        print("  " + text.replace("\n", "\n  ") if text else "  (空)")
        print()

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
