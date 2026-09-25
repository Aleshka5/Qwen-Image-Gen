# Tesla V100 (SM 7.0): CUDA 12.1 + колёса torch cu121 — последняя связка,
# в которой sm_70 собирается штатно и есть свежий diffusers.
#
# Стадии:
#   base — CUDA-рантайм + системный Python
#   ml   — torch + diffusers/transformers/quanto; тяжёлая, пересобирается
#          только при изменении requirements-ml.txt
#   app  — веб-зависимости и код сервиса; пересобирается за секунды
#
# pip-кеш вынесен в cache-mount: он переживает даже `podman build --no-cache`,
# поэтому колёса torch (~2.5 GB) повторно не скачиваются.

# ─────────────────────────────── base ───────────────────────────────
FROM docker.io/nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04 AS base

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip \
        libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

# ──────────────────────────────── ml ────────────────────────────────
FROM base AS ml

# Constraints не дают pip молча заменить torch на свежий с PyPI (без sm_70 и
# несовместимый с torchvision 0.20.1 → "operator torchvision::nms does not exist").
RUN printf 'torch==2.5.1\ntorchvision==0.20.1\n' > /etc/pip-constraints.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url https://download.pytorch.org/whl/cu121 \
        -c /etc/pip-constraints.txt torch torchvision

COPY requirements-ml.txt /tmp/requirements-ml.txt
# Если какой-то пакет потребует новее torch — сборка упадёт здесь, а не в рантайме.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /etc/pip-constraints.txt -r /tmp/requirements-ml.txt \
    && python -c "import torch, torchvision; from torchvision.ops import nms; \
from transformers import PreTrainedModel; import diffusers, optimum.quanto; \
assert torch.__version__.startswith('2.5.1'), torch.__version__; \
print('torch', torch.__version__, 'torchvision', torchvision.__version__, 'diffusers', diffusers.__version__)"

# ──────────────────────────────── app ───────────────────────────────
FROM ml AS app

WORKDIR /srv/app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /etc/pip-constraints.txt -r requirements.txt \
    && python -c "import torch; assert torch.__version__.startswith('2.5.1'), torch.__version__"

# Кеш весов и результаты — на томах, чтобы не тонули вместе с контейнером
RUN useradd --create-home --uid 1000 service \
    && mkdir -p /data/huggingface /data/outputs \
    && chown -R service:service /data

COPY --chown=service:service app ./app
COPY --chown=service:service wsgi.py .
USER service

ENV HF_HOME=/data/huggingface \
    OUTPUT_DIR=/data/outputs \
    PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Один воркер: модель занимает всю VRAM, параллельные воркеры её продублируют.
# timeout 0 — генерация на V100 легко превышает дефолтные 30 с.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", \
     "--timeout", "0", "--graceful-timeout", "60", "--access-logfile", "-", "wsgi:app"]
