"""上传文件到目标机（SFTP）。凭据走环境变量。

用法：
    DSH_HOST=... DSH_USER=... DSH_PASS=... python tools/ssh_put.py <本地文件> <远端路径>
"""

from __future__ import annotations

import os
import sys
import time

import paramiko


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: ssh_put.py <local> <remote>", file=sys.stderr)
        return 2
    local, remote = sys.argv[1], sys.argv[2]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        os.environ["DSH_HOST"], 22, os.environ["DSH_USER"], os.environ["DSH_PASS"],
        timeout=20, allow_agent=False, look_for_keys=False,
    )
    sftp = client.open_sftp()

    total = os.path.getsize(local)
    t0 = time.time()
    done = {"n": 0}

    def cb(sent: int, _total: int) -> None:
        done["n"] = sent

    sftp.put(local, remote, callback=cb)
    dur = time.time() - t0
    sftp.close()
    client.close()

    mb = total / 1024 / 1024
    print(f"上传完成 {remote}  {mb:.1f} MB / {dur:.0f}s ({mb / max(dur, 1):.1f} MB/s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
