"""portal 服务本地冒烟测试（不依赖 Docker、不依赖 pdf2docx）。

验证：健康检查 / 上传 / 原生预览路由 / 老格式路由（含扩展名与 base64） /
原始字节一致性 / 会话关闭清理 / 一次性下载令牌语义 / 路径穿越防护。

用法（在包根目录）：
    python3 tools/smoke_test.py
"""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import unquote, urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "services" / "portal"))

TMP = tempfile.mkdtemp(prefix="portal-smoke-")
os.environ["DATA_DIR"] = TMP
os.environ["SESSION_TTL"] = "60"
os.environ["MAX_UPLOAD_MB"] = "5"

from fastapi.testclient import TestClient  # noqa: E402

from app import main as portal  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  [ OK ] {name}")
    else:
        print(f"  [FAIL] {name} {detail}")
        FAILS.append(name)


def main() -> int:
    with TestClient(portal.app) as c:
        print("== 健康检查 ==")
        r = c.get("/api/health")
        check("health 200", r.status_code == 200, r.text)
        check("health status ok", r.json().get("status") == "ok")

        print("== 上传 PDF ==")
        pdf_bytes = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        r = c.post("/api/upload", files={"file": ("测试 文档.pdf", pdf_bytes, "application/pdf")})
        check("upload 200", r.status_code == 200, r.text)
        up = r.json()
        sid, key = up["session"], up["key"]
        check("kind=native", up["kind"] == "native", str(up))
        check("convertible=true", up.get("convertible") is True, str(up))
        check("文件名保留中文", up["name"] == "测试 文档.pdf", up["name"])

        print("== 原生预览 ==")
        r = c.get(f"/api/preview/{sid}/{key}")
        check("preview 200", r.status_code == 200, r.text)
        check("mode=native", r.json().get("mode") == "native", r.text)

        r = c.get(f"/api/file/{sid}/{key}")
        check("raw 字节一致", r.content == pdf_bytes)
        check("raw content-type", r.headers["content-type"].startswith("application/pdf"),
              r.headers.get("content-type", ""))
        check("raw no-store", r.headers.get("cache-control") == "no-store")

        print("== 上传老格式 .doc ==")
        r = c.post("/api/upload", files={"file": ("公文.doc", b"\xd0\xcf\x11\xe0junk", "application/msword")},
                   headers={"X-Session-Id": sid})
        check("upload 200", r.status_code == 200, r.text)
        up2 = r.json()
        check("复用同一会话", up2["session"] == sid, str(up2))
        check("kind=kk", up2["kind"] == "kk", str(up2))
        check("convertible=false", up2.get("convertible") is False, str(up2))

        r = c.get(f"/api/preview/{sid}/{up2['key']}")
        check("preview 200", r.status_code == 200, r.text)
        url = r.json().get("url", "")
        check("走 kkFileView", url.startswith("/preview/onlinePreview?url="), url)

        qs = parse_qs(urlparse(url).query)
        decoded = base64.b64decode(unquote(qs["url"][0])).decode("utf-8")
        # 路径段是 percent-encoded，先解码再比对后缀
        check("base64 内层 URL 带扩展名", unquote(decoded).endswith("/公文.doc"), decoded)
        check("内层 URL 指向本服务", decoded.startswith("http://portal:8000/api/file/"), decoded)

        print("== 带扩展名路由可用 ==")
        r = c.get(f"/api/file/{sid}/{up2['key']}/公文.doc")
        check("named raw 200", r.status_code == 200)
        check("named raw 内容一致", r.content == b"\xd0\xcf\x11\xe0junk")

        print("== 上传 OFD ==")
        r = c.post("/api/upload", files={"file": ("发票.ofd", b"PK\x03\x04fake-ofd", "application/ofd")},
                   headers={"X-Session-Id": sid})
        check("upload 200", r.status_code == 200, r.text)
        up3 = r.json()
        check("kind=ofd", up3["kind"] == "ofd", str(up3))

        r = c.get(f"/api/preview/{sid}/{up3['key']}")
        check("preview 200", r.status_code == 200, r.text)
        check("OFD 走前端渲染", r.json().get("mode") == "ofd", r.text)
        ofd_url = unquote(r.json().get("url", ""))
        check("OFD 地址带 .ofd 后缀（渲染器靠后缀判类型）",
              ofd_url.endswith("/发票.ofd"), ofd_url)
        check("OFD 地址指向原始文件（非 kk 转码）",
              f"/api/file/{sid}/{up3['key']}/" in ofd_url, ofd_url)

        print("== 路径穿越防护 ==")
        r = c.post("/api/upload", files={"file": ("../../etc/passwd", b"x", "text/plain")})
        name = r.json().get("name", "")
        check("文件名被净化", "/" not in name and ".." not in name, name)

        print("== 一次性下载令牌 ==")
        tmp_out = Path(TMP) / "out.docx"
        tmp_out.write_bytes(b"PK\x03\x04fake-docx")
        portal.TOKENS["tok-abc"] = {
            "path": tmp_out,
            "name": "结果.docx",
            "expires": time.time() + 60,
            "sid": sid,
        }
        r = c.get("/api/download/tok-abc")
        check("首次下载 200", r.status_code == 200, r.text)
        check("首次下载内容一致", r.content == b"PK\x03\x04fake-docx")
        r2 = c.get("/api/download/tok-abc")
        check("重复下载仍可用（不再一次性）", r2.status_code == 200, r2.text)

        print("== 会话关闭即清理 ==")
        r = c.post(f"/api/session/{sid}/close")
        check("close 200", r.status_code == 200, r.text)
        check("会话已移除", sid not in portal.SESSIONS)
        r = c.get(f"/api/file/{sid}/{key}")
        check("关闭后取文件 404", r.status_code == 404, r.text)

        print("== 转换任务状态机（不要求真转成功） ==")
        r = c.post("/api/upload", files={"file": ("bad.pdf", b"%PDF-1.4\nbroken", "application/pdf")})
        s2 = r.json()
        r = c.post(f"/api/convert/{s2['session']}/{s2['key']}")
        check("convert 受理 200", r.status_code == 200, r.text)
        job = r.json().get("job")
        check("返回 job id", bool(job), str(r.json()))

        final = None
        deadline = time.time() + 120
        while time.time() < deadline:
            r = c.get(f"/api/convert/status/{job}")
            st = r.json()
            if st["status"] in ("done", "failed"):
                final = st
                break
            time.sleep(0.5)

        check("任务在超时前落地（不卡死）", final is not None, "任务一直处于 running/queued")
        if final:
            check("终态为 done 或 failed", final["status"] in ("done", "failed"), str(final))
            if final["status"] == "failed":
                check("失败时带可读原因", bool(final.get("error")), str(final))
            else:
                check("成功时给出一次性下载链接", bool(final.get("download")), str(final))

        print("== 非 PDF 不允许转换 ==")
        r = c.post("/api/upload", files={"file": ("x.txt", b"hello", "text/plain")})
        s3 = r.json()
        r = c.post(f"/api/convert/{s3['session']}/{s3['key']}")
        check("拒绝非 PDF 转换", r.status_code == 400, r.text)
        c.post(f"/api/session/{s3['session']}/close")

        print("== PDF 导出能力判定 ==")
        r = c.post("/api/upload", files={"file": ("a.docx", b"PK\x03\x04x", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        d1 = r.json()
        check("docx 可导出 PDF", d1.get("pdf_export") is True, str(d1))
        check("docx 不可转 Word", d1.get("convertible") is False, str(d1))

        r = c.post("/api/upload", files={"file": ("b.xlsx", b"PK\x03\x04y", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        d2 = r.json()
        check("xlsx 不提供 PDF 导出", d2.get("pdf_export") is False, str(d2))

        r = c.get(f"/api/pdf/{d2['session']}/{d2['key']}")
        check("xlsx 导出返回 415（明确不支持）", r.status_code == 415, r.text)

        r = c.post("/api/upload", files={"file": ("c.pdf", b"%PDF-1.4\nx", "application/pdf")})
        d3 = r.json()
        r = c.get(f"/api/pdf/{d3['session']}/{d3['key']}")
        check("PDF 文件请求导出返回 400", r.status_code == 400, r.text)

        r = c.post(f"/api/ocr/{d3['session']}/{d3['key']}")
        check("PDF 可发起 OCR（受理 200）", r.status_code == 200, r.text)
        r = c.post(f"/api/ocr/{d1['session']}/{d1['key']}")
        check("非 PDF 拒绝 OCR（400）", r.status_code == 400, r.text)

        r = c.post(f"/api/ocrpdf/{d3['session']}/{d3['key']}")
        check("PDF 可发起可搜索 PDF（受理 200）", r.status_code == 200, r.text)
        r = c.post(f"/api/ocrpdf/{d1['session']}/{d1['key']}")
        check("非 PDF 拒绝可搜索 PDF（400）", r.status_code == 400, r.text)

        c.post(f"/api/session/{d1['session']}/close")
        c.post(f"/api/session/{d2['session']}/close")
        c.post(f"/api/session/{d3['session']}/close")

    print()
    if FAILS:
        print(f"冒烟测试失败 {len(FAILS)} 项: {FAILS}")
        return 1
    print("冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
