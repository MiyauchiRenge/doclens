"""扫描件 PDF -> Word（RapidOCR 版）。

设计参照 Stirling-PDF 的 OCR 流水线思路：
  渲染页面 → 逐页识别 → 按版面把文本块合并成段落 → 生成 docx

与 PaddleOCR 的关系：RapidOCR 直接使用 PP-OCRv4 的模型（det/rec/cls），
只是把推理引擎换成 ONNX Runtime，因此**不需要 PaddlePaddle**，
模型内嵌在 wheel 里，安装即可离线使用。

局限（必须让使用者知道）：
  - 产出的是"重排后的文字"，不是原版式还原
  - 表格会按行散开，图片不带过去
  - 用于"把扫描件变成能复制、能改的文字"
"""

from __future__ import annotations

import sys
from pathlib import Path

# 渲染 DPI：200 是清晰度与耗时的平衡点；再高对识别提升有限但明显变慢
RENDER_DPI = 200

# 同一段落内允许的行间距倍数（相对行高）
PARA_GAP_RATIO = 1.6

# 行内块的水平间距合并阈值（相对字高）
WORD_GAP_RATIO = 0.35


def _render_pages(pdf: Path):
    """把 PDF 每页渲染成 BGR ndarray，逐页 yield。"""
    import fitz  # PyMuPDF
    import numpy as np

    doc = fitz.open(str(pdf))
    try:
        zoom = RENDER_DPI / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
            if pix.n == 4:  # RGBA -> BGR
                arr = arr[:, :, :3]
            # RGB -> BGR
            yield arr[:, :, ::-1].copy()
    finally:
        doc.close()


def _group_lines(results):
    """把识别结果按行聚类，再按行间距把行合并成段落。

    results: [[box(4 点), text, score], ...]
    返回 [[line_text, ...], ...]（段落列表）
    """
    items = []
    for box, text, score in results:
        if not text or not text.strip():
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        items.append({
            "text": text.strip(),
            "x0": min(xs), "x1": max(xs),
            "y0": min(ys), "y1": max(ys),
            "h": max(ys) - min(ys),
            "cy": (min(ys) + max(ys)) / 2,
        })

    if not items:
        return []

    # 按垂直中心聚成行
    items.sort(key=lambda d: d["cy"])
    lines = []
    for it in items:
        placed = False
        for line in lines:
            if abs(line["cy"] - it["cy"]) < max(it["h"], line["h"]) * 0.6:
                line["items"].append(it)
                line["cy"] = sum(i["cy"] for i in line["items"]) / len(line["items"])
                line["h"] = max(line["h"], it["h"])
                placed = True
                break
        if not placed:
            lines.append({"cy": it["cy"], "h": it["h"], "items": [it]})

    lines.sort(key=lambda l: l["cy"])

    # 行内按 x 排序，间距大的补空格
    for line in lines:
        line["items"].sort(key=lambda d: d["x0"])
        parts = []
        prev = None
        for it in line["items"]:
            if prev is not None:
                gap = it["x0"] - prev["x1"]
                if gap > max(it["h"], prev["h"]) * WORD_GAP_RATIO:
                    parts.append(" ")
            parts.append(it["text"])
            prev = it
        line["text"] = "".join(parts)
        line["x0"] = min(i["x0"] for i in line["items"])

    # 按行间距合并段落；字号差异明显的（标题 vs 正文）强制分段
    paragraphs = []
    current = [lines[0]["text"]]
    prev_line = lines[0]
    for line in lines[1:]:
        gap = line["cy"] - prev_line["cy"]
        size_ratio = abs(line["h"] - prev_line["h"]) / max(line["h"], prev_line["h"], 1)
        same_para = (
            gap < max(line["h"], prev_line["h"]) * PARA_GAP_RATIO
            and size_ratio < 0.2
        )
        if same_para:
            current.append(line["text"])
        else:
            paragraphs.append("".join(current))
            current = [line["text"]]
        prev_line = line
    paragraphs.append("".join(current))
    return paragraphs


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: ocr_worker.py <input.pdf> <output.docx>", file=sys.stderr)
        return 2

    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.is_file():
        print(f"input not found: {src}", file=sys.stderr)
        return 2

    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as exc:  # noqa: BLE001
        print(f"RapidOCR 不可用：{exc}", file=sys.stderr)
        return 4

    try:
        from docx import Document
        from docx.shared import Pt
    except Exception as exc:  # noqa: BLE001
        print(f"python-docx 不可用：{exc}", file=sys.stderr)
        return 4

    engine = RapidOCR()
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)

    total_pages = 0
    total_chars = 0

    try:
        for page_idx, img in enumerate(_render_pages(src), start=1):
            total_pages += 1
            result, _elapse = engine(img)

            if not result:
                doc.add_paragraph(f"（第 {page_idx} 页未识别到文字）")
            else:
                paragraphs = _group_lines(result)
                if page_idx > 1:
                    doc.add_page_break()
                for p in paragraphs:
                    doc.add_paragraph(p)
                    total_chars += len(p)

            if total_pages >= 200:  # 防止超大文件把队列堵死
                doc.add_paragraph("（超过 200 页，后续页面未处理）")
                break
    except Exception as exc:  # noqa: BLE001
        print(f"OCR 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 3

    if total_chars == 0:
        print("未识别到任何文字，可能是空白页或图像质量过低", file=sys.stderr)
        return 5

    doc.save(str(dst))
    if not dst.exists() or dst.stat().st_size == 0:
        print("生成 docx 失败", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
