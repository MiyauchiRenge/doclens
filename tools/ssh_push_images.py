"""把本地 Docker 镜像流式推到远程主机（docker save | ssh docker load）。

不落中间文件、不占本地磁盘，适合内网一次性部署。
凭据仍走环境变量。

用法：
    DSH_HOST=... DSH_USER=... DSH_PASS=... python tools/ssh_push_images.py nginx:1.27-alpine ...
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import paramiko

CHUNK = 1024 * 1024


def push(client: paramiko.SSHClient, ref: str) -> bool:
    print(f"  -> {ref} ...", end="", flush=True)
    t0 = time.time()

    transport = client.get_transport()
    chan = transport.open_session()
    chan.exec_command("docker load")

    proc = subprocess.Popen(
        ["docker", "save", ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    total = 0
    try:
        while True:
            chunk = proc.stdout.read(CHUNK)
            if not chunk:
                break
            chan.sendall(chunk)
            total += len(chunk)
        chan.shutdown_write()
    except Exception as exc:  # noqa: BLE001
        proc.kill()
        print(f" 发送失败: {exc}")
        return False

    err = proc.stderr.read().decode("utf-8", "replace")
    proc.wait()

    out = b""
    while True:
        if chan.recv_ready():
            out += chan.recv(65536)
        elif chan.exit_status_ready():
            while chan.recv_ready():
                out += chan.recv(65536)
            break
        else:
            time.sleep(0.05)

    rc = chan.recv_exit_status()
    dur = time.time() - t0
    mb = total / 1024 / 1024
    print(f" {mb:.0f} MB, {dur:.0f}s ({mb / max(dur, 1):.1f} MB/s) rc={rc}")

    if rc != 0:
        print((out.decode("utf-8", "replace") + err)[:800])
    return rc == 0


def main() -> int:
    refs = sys.argv[1:]
    if not refs:
        print("用法: ssh_push_images.py <image:tag> [...]", file=sys.stderr)
        return 2

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        os.environ["DSH_HOST"], 22, os.environ["DSH_USER"], os.environ["DSH_PASS"],
        timeout=20, allow_agent=False, look_for_keys=False,
    )

    ok = True
    for ref in refs:
        if not push(client, ref):
            ok = False
            break

    client.close()
    print("全部推送完成" if ok else "推送中断")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
