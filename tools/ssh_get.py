"""从目标机下载文件（SFTP）。凭据走环境变量。

用法：
    DSH_HOST=... DSH_USER=... DSH_PASS=... python tools/ssh_get.py <remote> <local>
"""

from __future__ import annotations

import os
import sys
import time

import paramiko


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: ssh_get.py <remote> <local>", file=sys.stderr)
        return 2
    remote, local = sys.argv[1], sys.argv[2]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        os.environ["DSH_HOST"], 22, os.environ["DSH_USER"], os.environ["DSH_PASS"],
        timeout=20, allow_agent=False, look_for_keys=False,
    )
    sftp = client.open_sftp()

    size = sftp.stat(remote).st_size
    t0 = time.time()
    last = {"t": 0.0}

    def cb(sent: int, total: int) -> None:
        now = time.time()
        if now - last["t"] > 3:
            last["t"] = now
            print(f"  {sent / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} MB", flush=True)

    sftp.get(remote, local, callback=cb)
    dur = time.time() - t0
    sftp.close()
    client.close()

    mb = size / 1024 / 1024
    print(f"下载完成 {local}  {mb:.0f} MB / {dur:.0f}s ({mb / max(dur, 1):.1f} MB/s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
