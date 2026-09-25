# Tesla V100 (SM 7.0). torch 2.7.1 + cu126: колёса cu126 ещё собраны с sm_70,
# а свежие diffusers/optimum-quanto требуют torch>=2.6 (cu121 заканчивается на 2.5.1).
# CUDA 13 (cu130) Volta уже не поддерживает — выше cu126/cu128 не поднимать.
# CUDA-библиотеки torch приносит сам (пакеты nvidia-*), от базового образа нужен
# только рантайм-минимум. Драйвер хоста — 560+ (проверено на 580).
#
# Стадии:
#   base — CUDA-рантайм + системный Python
#   ml   — torch + diffusers/transformers/quanto; тяжёлая, пересобирается
#          только при изменении requirements-ml.txt или версий torch
#   app  — веб-зависимости и код сервиса; пересобирается за секунды
#
# pip-кеш вынесен в cache-mount: он переживает даже `podman build --no-cache`,
# поэтому колёса torch (~3 GB) повторно не скачиваются.

# ─────────────────────────────── base ───────────────────────────────
FROM docker.io/nvidia/cuda:12.6.3-base-ubuntu22.04 AS base

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

ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
ARG TORCH_INDEX=https://download.pytorch.org/whl/cu126

# Constraints не дают pip молча заменить torch на другой с PyPI: так в образ
# попал torch 2.14 при torchvision 0.20.1 → "operator torchvision::nms does not exist".
# Они действуют и в стадии app: попытка сменить torch там тоже уронит сборку.
RUN printf 'torch==%s\ntorchvision==%s\n' "$TORCH_VERSION" "$TORCHVISION_VERSION" \
        > /etc/pip-constraints.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "$TORCH_INDEX" -c /etc/pip-constraints.txt torch torchvision

COPY requirements-ml.txt /tmp/requirements-ml.txt
# Если какой-то пакет потребует другой torch — сборка упадёт здесь, а не в рантайме.
# Проверка sm_70 не требует GPU: список архитектур зашит в сборку torch.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /etc/pip-constraints.txt -r /tmp/requirements-ml.txt \
    && python -c "import torch, torchvision; from torchvision.ops import nms; \
from transformers import PreTrainedModel; import diffusers, optimum.quanto; \
assert torch.__version__.startswith('$TORCH_VERSION'), torch.__version__; \
archs = torch._C._cuda_getArchFlags() or ''; \
assert 'sm_70' in archs, 'torch собран без sm_70 (V100): ' + archs; \
print('torch', torch.__version__, 'torchvision', torchvision.__version__, \
'diffusers', diffusers.__version__, 'archs', archs)"

# ──────────────────────────────── app ───────────────────────────────
FROM ml AS app

WORKDIR /srv/app
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -c /etc/pip-constraints.txt -r requirements.txt

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
