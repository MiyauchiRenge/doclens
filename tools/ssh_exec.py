"""在目标机上执行单条命令（凭据走环境变量，不落盘）。

用法：
    DSH_HOST=... DSH_USER=... DSH_PASS=... python tools/ssh_exec.py "uname -a"
    DSH_HOST=... DSH_USER=... DSH_PASS=... python tools/ssh_exec.py -b <base64>

Windows 上引号/分号容易被吃掉，复杂命令建议用 -b 传 base64，
远端解码后交给 bash 执行，可彻底避开引号问题。
"""

from __future__ import annotations

import base64
import os
import sys

import paramiko


def main() -> int:
    if len(sys.argv) < 2:
        print('用法: ssh_exec.py "命令" | ssh_exec.py -b <base64>', file=sys.stderr)
        return 2

    if sys.argv[1] == "-b":
        cmd = base64.b64decode(sys.argv[2]).decode("utf-8")
        wrapped = f"echo {sys.argv[2]} | base64 -d | bash"
    else:
        cmd = sys.argv[1]
        wrapped = cmd

    host = os.environ.get("DSH_HOST", "")
    user = os.environ.get("DSH_USER", "")
    pwd = os.environ.get("DSH_PASS", "")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, 22, user, pwd, timeout=20, allow_agent=False, look_for_keys=False)

    _, out, err = client.exec_command(wrapped, timeout=1800)
    stdout = out.read().decode("utf-8", "replace")
    stderr = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    client.close()

    if cmd != wrapped:
        print(f"$ {cmd}")
    if stdout:
        sys.stdout.write(stdout)
    if stderr:
        sys.stderr.write(stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
