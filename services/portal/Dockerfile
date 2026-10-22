# portal: 上传 -> 预览路由 -> PDF转Word -> 一次性下载 -> TTL 清理
# 构建需联网（pip），产物镜像可离线 save/load。

ARG PYTHON_IMAGE=python
ARG PYTHON_VERSION=3.11-slim
FROM ${PYTHON_IMAGE}:${PYTHON_VERSION}

# 构建期的 pip 源。默认官方 PyPI；国内构建可传
# --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    TZ=Asia/Shanghai

# pdf2docx 依赖 opencv-python-headless（不是 opencv-python），
# 因此**不需要** libGL / libX11 那一整套图形库；真正缺的是 glib 与 OpenMP 运行时。
# 少装这些既省 50MB+ 下载，也避开 Debian 镜像里 libgbm1 之类的易失败包。
RUN set -eux; \
    apt-get update -o Acquire::Retries=5; \
    apt-get install -y --no-install-recommends -o Acquire::Retries=5 \
        libglib2.0-0 \
        libgomp1; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt requirements-ocr.txt ./
RUN pip install -r requirements.txt

# 扫描件 OCR：RapidOCR 用的是 PP-OCRv4 的模型，但换成 ONNX Runtime 推理，
# 不需要 PaddlePaddle（那一个包就 900MB+）。模型内嵌在 wheel 里，离线可用。
#
# 必须用 --no-deps：rapidocr 声明依赖 opencv-python（GUI 版），
# 会和 pdf2docx 的 opencv-python-headless 抢 cv2，所以只装本体、复用 headless。
RUN pip install -r requirements-ocr.txt \
 && pip install --no-deps rapidocr-onnxruntime==1.4.4

# 冒烟：确认 cv2 与 OCR 引擎都能导入（缺系统库会在这里就暴露）
RUN python -c "import cv2; from rapidocr_onnxruntime import RapidOCR; print('ocr deps ok', cv2.__version__)"

COPY app/ ./app/

RUN mkdir -p /data && chmod 1777 /data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
