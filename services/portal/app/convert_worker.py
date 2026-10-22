"""PDF -> Word 转换子进程。

单独进程的意义：主服务可以用 subprocess 的 timeout 强杀，
而在线程里调用 pdf2docx 一旦卡死在坏文件上就无法回收。
"""

from __future__ import annotations

import sys
from pathlib import Path


def _text_chars(src: Path) -> int | None:
    """统计 PDF 可提取的字符数，用来判断是不是扫描件。

    返回 None 表示无法判断（缺少 PyMuPDF 等），调用方应按正常流程继续。
    """
    try:
        import fitz  # PyMuPDF，pdf2docx 的依赖，随包安装
    except Exception:  # noqa: BLE001
        return None

    try:
        with fitz.open(str(src)) as doc:
            total = 0
            for page in doc:
                total += len(page.get_text("text") or "")
            return total
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: convert_worker.py <input.pdf> <output.docx>", file=sys.stderr)
        return 2

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.is_file():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    # 扫描件没有文字层，pdf2docx 只会产出一个空壳（或整页图片），
    # 直接给出明确原因，比返回一个"转换成功但没内容"的文件好得多。
    chars = _text_chars(src)
    if chars is not None and chars < 20:
        print(
            f"该 PDF 没有文字层（可提取字符仅 {chars} 个），属于扫描件。"
            "当前未启用 OCR，无法转成可编辑文字；请改用 OCR 工具或提供文字版 PDF。",
            file=sys.stderr,
        )
        return 5

    try:
        from pdf2docx import Converter
    except Exception as exc:  # noqa: BLE001
        print(f"pdf2docx unavailable: {exc}", file=sys.stderr)
        return 4

    conv = None
    try:
        conv = Converter(str(src))
        conv.convert(str(dst))
    except Exception as exc:  # noqa: BLE001
        print(f"convert failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 3
    finally:
        if conv is not None:
            try:
                conv.close()
            except Exception:  # noqa: BLE001
                pass

    if not dst.exists() or dst.stat().st_size == 0:
        print("convert produced empty output", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
