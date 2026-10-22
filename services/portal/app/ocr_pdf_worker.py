"""扫描件 -> 可搜索 PDF。

与 ocr_worker.py 的区别：
  - ocr_worker：提取文字，重排成纯文字 docx（版式全丢）
  - 本文件：**原页面一个像素都不动**，只在对应位置叠加一层「不可见文字」

效果：
  - 翻页看到的还是原始扫描件（底色、图片、印章、版式 100% 保留）
  - 但可以 Ctrl+F 搜索、可以选中复制、可以被全文检索系统抓取

实现要点：
  - 用 PyMuPDF 把每页渲染成位图交给 RapidOCR 识别，拿到文字框坐标
  - 坐标要从像素换算回 PDF 点（1pt = 1/72 inch）
  - 用 render_mode=3（不可见）写字，字体用 PyMuPDF 内置的 china-s（简体中文）
    —— 内置 CJK 字体不需要外部字体文件，保证离线可用
"""

from __future__ import annotations

import sys
from pathlib import Path

RENDER_DPI = 200

# 文字基线相对字框底部的偏移比例（让选中框与文字大致对齐）
BASELINE_RATIO = 0.80

# 字号相对字框高度的比例（略小一点，避免超出字框）
FONT_SIZE_RATIO = 0.82


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: ocr_pdf_worker.py <input.pdf> <output.pdf>", file=sys.stderr)
        return 2

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.is_file():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    try:
        import fitz
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # noqa: BLE001
        print(f"依赖不可用：{exc}", file=sys.stderr)
        return 4

    engine = RapidOCR()
    scale = 72.0 / RENDER_DPI  # 像素 -> PDF 点

    doc = fitz.open(str(src))
    total_chars = 0
    try:
        for page in doc:
            pix = page.get_pixmap(dpi=RENDER_DPI, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 3:      # RGB -> BGR（RapidOCR 按 OpenCV 约定）
                arr = arr[:, :, ::-1]
            arr = np.ascontiguousarray(arr)

            result, _elapse = engine(arr)
            if not result:
                continue

            for box, text, _score in result:
                text = (text or "").strip()
                if not text:
                    continue
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0 = min(xs) * scale
                y_bottom = max(ys) * scale
                height = (max(ys) - min(ys)) * scale

                baseline = y_bottom - height * (1 - BASELINE_RATIO)
                fontsize = max(1.0, height * FONT_SIZE_RATIO)

                try:
                    page.insert_text(
                        (x0, baseline),
                        text,
                        fontname="china-s",   # PyMuPDF 内置简体中文字体
                        fontsize=fontsize,
                        render_mode=3,        # 3 = 不可见（但可被提取）
                        color=(0, 0, 0),
                    )
                    total_chars += len(text)
                except Exception:  # noqa: BLE001
                    # 单个文本块失败不影响整页
                    continue
    except Exception as exc:  # noqa: BLE001
        print(f"处理失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        doc.close()
        return 3

    if total_chars == 0:
        doc.close()
        print("未识别到任何文字，可能是空白页或图像质量过低", file=sys.stderr)
        return 5

    # garbage=3 清理冗余对象；deflate 压缩
    doc.save(str(dst), garbage=3, deflate=True)
    doc.close()

    if not dst.exists() or dst.stat().st_size == 0:
        print("生成 PDF 失败", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
