"""fileview portal

职责（刻意保持无状态）：
  1. 接收上传 -> 落在 tmpfs 会话目录，不写持久盘
  2. 预览路由：原生格式直接回文件地址；老格式交给 kkFileView 转码
  3. PDF -> Word 转换（子进程 + 硬超时，可杀）
  4. 转换产物用一次性令牌下载，下载即删
  5. 会话 TTL 到期清理 + 主动关闭清理 + 孤儿清扫

设计前提：内部使用口径「处理期间临时驻留，会话结束销毁」。
不承诺物理不可恢复，因此不实现加密擦除等方案。
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SESSION_TTL = int(os.getenv("SESSION_TTL", "1800"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "200"))
KK_URL = os.getenv("KKFILEVIEW_URL", "http://kkfileview:8012").rstrip("/")
SELF_URL = os.getenv("SELF_URL", "http://portal:8000").rstrip("/")
CONVERT_TIMEOUT = int(os.getenv("CONVERT_TIMEOUT", "300"))
CONVERT_CONCURRENCY = max(1, int(os.getenv("CONVERT_CONCURRENCY", "1")))
OCR_TIMEOUT = int(os.getenv("OCR_TIMEOUT", "1800"))
OCR_CONCURRENCY = max(1, int(os.getenv("OCR_CONCURRENCY", "1")))
JANITOR_INTERVAL = int(os.getenv("JANITOR_INTERVAL", "30"))

ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# 浏览器可原生打开、无需服务端转码的格式
NATIVE_EXT = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
    ".txt", ".log", ".md", ".json", ".xml", ".csv",
    ".mp4", ".webm", ".mp3", ".wav",
}

# OFD：浏览器不能原生打开，但前端 flyfish 渲染器可在浏览器内解析
# （已实测：Apache-2.0 的 @file-viewer/renderer-ofd，纯前端、无外部资源）
OFD_EXT = {".ofd"}

# 需要 kkFileView（LibreOffice 系）转码的格式
KK_EXT = {
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".rtf", ".odt", ".ods", ".odp", ".wps", ".et", ".dps",
    ".zip", ".7z", ".rar", ".tar", ".gz",
    ".dwg", ".dxf", ".psd", ".eml", ".msg",
}

# 允许发起 PDF->Word 的格式
CONVERTIBLE_EXT = {".pdf"}

# 允许"导出 PDF"的格式。
# 注意这里**不含 xls/xlsx**：kkFileView 的 Excel 预览走 HTML 表格渲染，
# 不会产出 PDF，硬做只会 404。Excel 需要 PDF 时应上传扫描件或另存 PDF。
PDF_EXPORT_EXT = {".doc", ".docx", ".ppt", ".pptx", ".rtf", ".odt", ".odp", ".wps", ".dps"}

MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".log": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".xml": "application/xml",
    ".csv": "text/plain; charset=utf-8",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ofd": "application/ofd",
    ".zip": "application/zip",
}

WORKER = Path(__file__).with_name("convert_worker.py")
OCR_WORKER = Path(__file__).with_name("ocr_worker.py")
OCR_PDF_WORKER = Path(__file__).with_name("ocr_pdf_worker.py")

# --------------------------------------------------------------------------
# 内存索引
# --------------------------------------------------------------------------


@dataclass
class Session:
    id: str
    dir: Path
    created: float
    files: dict = field(default_factory=dict)  # key -> {"path": Path, "name": str, "size": int}


@dataclass
class Job:
    id: str
    sid: str
    status: str
    created: float
    token: str | None = None
    filename: str | None = None
    error: str | None = None
    workdir: Path | None = None


SESSIONS: dict[str, Session] = {}
JOBS: dict[str, Job] = {}
TOKENS: dict[str, dict] = {}  # token -> {"path": Path, "name": str, "expires": float}

SEM: asyncio.Semaphore  # 在 startup 里初始化，避免事件循环外创建
OCR_SEM: asyncio.Semaphore

app = FastAPI(title="fileview portal", docs_url=None, redoc_url=None, openapi_url=None)


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


def _sanitize(name: str) -> str:
    """只保留安全字符，彻底杜绝路径穿越；同时尽量保留中文、空格与括号。

    仅剔除路径分隔符、控制字符与其他可能被解释的符号，不改动可读性。
    """
    base = Path(name or "file").name
    # \w 在 Python3 默认匹配 Unicode 单词字符，已覆盖中文
    base = re.sub(r"[^\w.\-()（）\[\] ]", "_", base)
    base = base.lstrip(".").strip()
    if not base:
        return "file"

    # 截断时保留扩展名，避免长文件名把后缀切掉（会把 .docx 变成无类型文件）
    suffix = Path(base).suffix
    if len(base) > 120:
        stem_limit = max(1, 120 - len(suffix))
        base = Path(base).stem[:stem_limit] + suffix
    return base


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def _new_id() -> str:
    return secrets.token_urlsafe(16)


def _get_session(sid: str) -> Session:
    s = SESSIONS.get(sid)
    if s is None:
        raise HTTPException(status_code=404, detail="会话不存在或已过期")
    return s


def _b64_url(url: str) -> str:
    """kkFileView 约定：url 参数需 Base64 后再 URL 编码。"""
    return quote(base64.b64encode(url.encode("utf-8")).decode("ascii"), safe="")


def _purge_session(sid: str) -> None:
    s = SESSIONS.pop(sid, None)
    if s is None:
        return
    shutil.rmtree(s.dir, ignore_errors=True)
    for jid in [j for j, job in JOBS.items() if job.sid == sid]:
        JOBS.pop(jid, None)
    for tok in [t for t, m in TOKENS.items() if m.get("sid") == sid]:
        TOKENS.pop(tok, None)


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------


@app.on_event("startup")
async def _startup() -> None:
    global SEM, OCR_SEM
    SEM = asyncio.Semaphore(CONVERT_CONCURRENCY)
    OCR_SEM = asyncio.Semaphore(OCR_CONCURRENCY)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 容器重启即清空：残留只可能是异常退出留下的，直接全删。
    for child in DATA_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    asyncio.create_task(_janitor())


async def _janitor() -> None:
    """两级清理：会话 TTL + 令牌 TTL。"""
    while True:
        await asyncio.sleep(JANITOR_INTERVAL)
        now = time.time()
        for sid in [s.id for s in list(SESSIONS.values()) if now - s.created > SESSION_TTL]:
            _purge_session(sid)
        for tok in [t for t, m in TOKENS.items() if now > m["expires"]]:
            meta = TOKENS.pop(tok, None)
            if meta:
                meta["path"].unlink(missing_ok=True)
        # 兜底：磁盘上存在但索引里没有的目录（进程崩溃残留）
        known = {s.dir.name for s in SESSIONS.values()}
        for child in DATA_DIR.iterdir():
            if child.is_dir() and child.name not in known:
                shutil.rmtree(child, ignore_errors=True)


# --------------------------------------------------------------------------
# 接口：健康检查
# --------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "sessions": len(SESSIONS),
        "jobs": len(JOBS),
        "ttl": SESSION_TTL,
        "data_bytes": _dir_size(DATA_DIR),
    }


# --------------------------------------------------------------------------
# 接口：上传
# --------------------------------------------------------------------------


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    sid = request.headers.get("X-Session-Id", "").strip()
    if sid and not ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="非法的会话标识")

    if sid and sid in SESSIONS:
        sess = SESSIONS[sid]
    else:
        sid = _new_id()
        sdir = DATA_DIR / sid
        sdir.mkdir(parents=True, exist_ok=True)
        sess = Session(id=sid, dir=sdir, created=time.time())
        SESSIONS[sid] = sess

    name = _sanitize(file.filename or "file")
    key = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    dst = sess.dir / f"{key}{_ext(name)}"

    total = 0
    limit = MAX_UPLOAD_MB * 1024 * 1024
    try:
        with dst.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_MB}MB 上限")
                fh.write(chunk)
    except HTTPException:
        dst.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    if total == 0:
        dst.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="空文件")

    sess.files[key] = {"path": dst, "name": name, "size": total}

    ext = _ext(name)
    if ext in NATIVE_EXT:
        mode = "native"
    elif ext in OFD_EXT:
        mode = "ofd"
    else:
        mode = "kk"

    return JSONResponse(
        {
            "session": sid,
            "key": key,
            "name": name,
            "size": total,
            "kind": mode,
            "convertible": ext in CONVERTIBLE_EXT,
            "pdf_export": ext in PDF_EXPORT_EXT,
            "ocr": ext in CONVERTIBLE_EXT,
        }
    )


# --------------------------------------------------------------------------
# 接口：预览
# --------------------------------------------------------------------------


@app.get("/api/preview/{sid}/{key}")
async def preview(sid: str, key: str) -> dict:
    sess = _get_session(sid)
    meta = sess.files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = _ext(meta["name"])
    if ext in NATIVE_EXT:
        return {"mode": "native", "url": f"/api/file/{sid}/{key}"}

    # OFD 由前端渲染器在浏览器内解析，不需要后端转码。
    # 注意：**URL 必须带 .ofd 后缀**，渲染器靠后缀判定格式；
    # 用无后缀的 /api/file/{sid}/{key} 会显示"不支持该格式"。
    if ext in OFD_EXT:
        return {"mode": "ofd", "url": f"/api/file/{sid}/{key}/{quote(meta['name'])}"}

    # 交给 kkFileView：它需要一个自己容器内可访问、且**带扩展名**的地址
    # （kkFileView 依据 URL 后缀判定文件类型，没有后缀会识别失败）
    internal = f"{SELF_URL}/api/file/{sid}/{key}/{quote(meta['name'])}"
    return {"mode": "kk", "url": f"/preview/onlinePreview?url={_b64_url(internal)}"}


@app.get("/api/file/{sid}/{key}")
async def raw_file(sid: str, key: str) -> FileResponse:
    """原始文件字节流。仅限本网络内部使用（kkFileView 与浏览器直连）。"""
    return _serve_raw(sid, key)


@app.get("/api/file/{sid}/{key}/{filename}")
async def raw_file_named(sid: str, key: str, filename: str) -> FileResponse:
    """带文件名/后缀的同一份内容。

    kkFileView 需要靠 URL 后缀判断类型，因此它拿到的地址走这个路由；
    filename 仅用于其类型识别，实际取的文件仍以 key 为准，不参与路径拼接。
    """
    return _serve_raw(sid, key)


def _serve_raw(sid: str, key: str) -> FileResponse:
    sess = _get_session(sid)
    meta = sess.files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    path: Path = meta["path"]
    if not path.exists():
        raise HTTPException(status_code=410, detail="文件已被清理")

    media = MEDIA_TYPES.get(_ext(meta["name"]), "application/octet-stream")
    return FileResponse(
        path,
        media_type=media,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(meta['name'])}",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# --------------------------------------------------------------------------
# 接口：PDF -> Word
# --------------------------------------------------------------------------


def _finish_job(job: Job, dst: Path, display_name: str, suffix: str) -> None:
    """登记下载令牌。

    display_name 必须是**用户上传时的原始文件名（不含扩展名）**，
    不能用会话里的随机 key——否则下载下来是 "1792654…_可搜索.pdf" 这种名字。
    """
    token = secrets.token_urlsafe(24)
    name = f"{display_name}{suffix}"
    TOKENS[token] = {
        "path": dst,
        "name": name,
        "expires": time.time() + SESSION_TTL,
        "sid": job.sid,
    }
    job.token = token
    job.filename = name
    job.status = "done"


def _run_worker(job: Job, worker: Path, src: Path, dst: Path, timeout: int,
                label: str, suffix: str, display_name: str) -> None:
    """在子进程里执行转换，超时可强杀（线程做不到这一点）。

    子进程失败时把 stderr 最后一行作为原因——各 worker 都保证
    最后一行是可读的中文说明，前端直接展示。
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(worker), str(src), str(dst)],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        job.status = "failed"
        job.error = f"{label}超时（>{timeout}s），文件可能损坏、页数过多或内容过于复杂"
        return

    if proc.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        job.status = "failed"
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        job.error = tail[-1] if tail else f"{label}失败（退出码 {proc.returncode}）"
        return

    _finish_job(job, dst, display_name, suffix)


async def _job_task(job: Job, worker: Path, src: Path, dst: Path, timeout: int,
                    sem: asyncio.Semaphore, label: str, suffix: str,
                    display_name: str) -> None:
    async with sem:
        job.status = "running"
        try:
            await asyncio.to_thread(
                _run_worker, job, worker, src, dst, timeout, label, suffix, display_name
            )
        except Exception as exc:  # 兜底，避免 job 永久卡在 running
            job.status = "failed"
            job.error = f"内部错误：{exc}"


def _new_job(sid: str) -> tuple:
    job_id = _new_id()
    workdir = SESSIONS[sid].dir / f"job-{job_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    job = Job(id=job_id, sid=sid, status="queued", created=time.time(), workdir=workdir)
    JOBS[job_id] = job
    return job, workdir / "output.docx"


@app.post("/api/convert/{sid}/{key}")
async def convert(sid: str, key: str) -> dict:
    _get_session(sid)
    meta = SESSIONS[sid].files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if _ext(meta["name"]) not in CONVERTIBLE_EXT:
        raise HTTPException(status_code=400, detail="仅支持 PDF 转 Word")

    job, dst = _new_job(sid)
    asyncio.create_task(
        _job_task(job, WORKER, meta["path"], dst, CONVERT_TIMEOUT, SEM,
                  "转换", ".docx", Path(meta["name"]).stem)
    )
    return {"job": job.id, "status": job.status}


@app.post("/api/ocr/{sid}/{key}")
async def ocr_convert(sid: str, key: str) -> dict:
    """扫描件 OCR 提取文字 → 纯文字 Word。

    注意：产出**没有版式**（表格散成行、图片丢失）。
    要保留原貌请用 /api/ocrpdf。
    """
    _get_session(sid)
    meta = SESSIONS[sid].files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if _ext(meta["name"]) not in CONVERTIBLE_EXT:
        raise HTTPException(status_code=400, detail="OCR 目前仅支持 PDF")

    job, dst = _new_job(sid)
    asyncio.create_task(
        _job_task(job, OCR_WORKER, meta["path"], dst, OCR_TIMEOUT, OCR_SEM,
                  "OCR", "_OCR文字.docx", Path(meta["name"]).stem)
    )
    return {"job": job.id, "status": job.status}


@app.post("/api/ocrpdf/{sid}/{key}")
async def ocr_searchable_pdf(sid: str, key: str) -> dict:
    """扫描件 OCR → 可搜索 PDF。

    原页面**一个像素都不动**，只在文字位置叠加不可见文本层：
    看到的仍是原件（底色/图片/印章/版式全保留），但可以搜索、选中、复制。
    """
    _get_session(sid)
    meta = SESSIONS[sid].files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if _ext(meta["name"]) not in CONVERTIBLE_EXT:
        raise HTTPException(status_code=400, detail="OCR 目前仅支持 PDF")

    job, dst = _new_job(sid)
    asyncio.create_task(
        _job_task(job, OCR_PDF_WORKER, meta["path"], dst, OCR_TIMEOUT, OCR_SEM,
                  "OCR", "_可搜索.pdf", Path(meta["name"]).stem)
    )
    return {"job": job.id, "status": job.status}


@app.get("/api/convert/status/{job_id}")
async def convert_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return {
        "job": job.id,
        "status": job.status,
        "error": job.error,
        "download": f"/api/download/{job.token}" if job.token else None,
        "filename": job.filename,
    }


# --------------------------------------------------------------------------
# 接口：一次性下载
# --------------------------------------------------------------------------


@app.get("/api/download/{token}")
async def download(token: str) -> FileResponse:
    """下载转换产物。

    早期版本做成了"一次性令牌"，但实测体验很差：浏览器可能重复请求
    （下载管理器、用户点两次、断点续传），第二次必然 410。
    现在改为**会话有效期内可重复下载**，文件仍随会话/TTL 一起销毁，
    清理语义不变，只是不再"用一次就废"。
    """
    meta = TOKENS.get(token)
    if meta is None:
        raise HTTPException(status_code=410, detail="下载链接不存在或随会话已清理")

    if time.time() > meta["expires"]:
        TOKENS.pop(token, None)
        meta["path"].unlink(missing_ok=True)
        raise HTTPException(status_code=410, detail="下载链接已过期（超过会话有效期）")

    path: Path = meta["path"]
    if not path.exists():
        TOKENS.pop(token, None)
        raise HTTPException(status_code=410, detail="文件已被清理")

    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=meta["name"],
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------------------------------
# 接口：Office → PDF 导出
#
# 复用 kkFileView 已经具备的 LibreOffice 能力，不额外起转换容器：
#   1. 先请求它的 onlinePreview，触发 docx/xlsx/pptx -> PDF 转换
#   2. 再把生成好的 <basename>.pdf 取回来，直接流给浏览器
# 优点：零新增组件、与预览用的是同一套渲染，版式一致。
# 缺点：产物在 kk 的文件目录里，靠 tmpfs 随容器销毁（见 compose）。
# --------------------------------------------------------------------------


def _http_get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "fileview-portal/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


@app.get("/api/pdf/{sid}/{key}")
async def office_to_pdf(sid: str, key: str) -> Response:
    sess = _get_session(sid)
    meta = sess.files.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail="文件不存在")

    ext = _ext(meta["name"])
    if ext not in KK_EXT:
        raise HTTPException(status_code=400, detail="该格式无需转换（PDF/图片等本身就是目标格式）")
    if ext not in PDF_EXPORT_EXT:
        raise HTTPException(
            status_code=415,
            detail="该格式暂不支持导出 PDF（Excel 走网页表格预览，不生成 PDF）。"
                   "需要 PDF 版请在源端另存为 PDF 后上传。",
        )

    internal = f"{SELF_URL}/api/file/{sid}/{key}/{quote(meta['name'])}"
    trigger = f"{KK_URL}/onlinePreview?url={_b64_url(internal)}"

    try:
        await asyncio.to_thread(_http_get, trigger, 180)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"触发转换失败：{exc}") from exc

    pdf_name = Path(meta["name"]).stem + ".pdf"
    pdf_url = f"{KK_URL}/{quote(pdf_name)}"
    try:
        data = await asyncio.to_thread(_http_get, pdf_url, 300)
    except urllib.error.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"转换未完成或不支持的格式（{exc.code}）。该文件可能无法被 LibreOffice 解析。",
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"取回 PDF 失败：{exc}") from exc

    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=502, detail="转换结果不是有效的 PDF，请确认源文件未损坏")

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(pdf_name)}",
            "Content-Length": str(len(data)),
            "Cache-Control": "no-store",
        },
    )


# --------------------------------------------------------------------------
# 接口：会话关闭（前端退出时调用；TTL 仍是最终兜底）
# --------------------------------------------------------------------------


@app.post("/api/session/{sid}/close")
async def close_session(sid: str) -> dict:
    _purge_session(sid)
    return {"closed": True}


@app.post("/api/session/close")
async def close_session_beacon(request: Request) -> dict:
    """sendBeacon 无法设置自定义头，走这里用 body 传 sid。"""
    try:
        body = await request.json()
    except Exception:
        body = {}
    sid = str(body.get("session", ""))
    if ID_RE.match(sid):
        _purge_session(sid)
    return {"closed": True}
