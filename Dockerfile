# Tesla V100 (SM 7.0). torch 2.7.1 + cu126: the cu126 wheels are still built with sm_70,
# and current diffusers/optimum-quanto require torch>=2.6 (cu121 stops at 2.5.1).
# CUDA 13 (cu130) no longer supports Volta — do not move past cu126/cu128.
# Torch ships its own CUDA libraries (nvidia-* packages). The base image only
# needs the runtime minimum. Host driver 560+ (checked on 580).
#
# Stages:
#   base — CUDA runtime + system Python 3.10 (Ubuntu 22.04) and uv
#   ml   — uv sync: project dependencies and the ml group (torch 2.7.1+cu126 from the lock);
#          heavy, rebuilt when pyproject.toml or uv.lock changes
#   app  — service code; rebuilds in seconds
#
# The uv cache is a cache mount: it survives even `podman build --no-cache`,
# so the torch wheels (~3 GB) are not downloaded again.

# ─────────────────────────────── base ───────────────────────────────
FROM docker.io/nvidia/cuda:12.6.3-base-ubuntu22.04 AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_PREFERENCE=only-system \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 \
        libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

# ──────────────────────────────── ml ────────────────────────────────
FROM base AS ml

WORKDIR /srv/app

# The pytorch-cu126 index and the torch==2.7.1 / torchvision==0.22.1 pins come from uv.lock.
# The dev group (pytest) is not installed in the image.
ENV VIRTUAL_ENV=/srv/app/.venv \
    PATH="/srv/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --group ml --no-install-project

# If the lock swaps torch, the build fails here, not at runtime.
# The sm_70 check does not need a GPU: the architecture list is compiled into torch.
RUN python -c "import torch, torchvision; from torchvision.ops import nms; \
from transformers import PreTrainedModel; import diffusers, optimum.quanto; \
assert torch.__version__.startswith('2.7.1'), torch.__version__; \
assert torchvision.__version__.startswith('0.22.1'), torchvision.__version__; \
archs = torch._C._cuda_getArchFlags() or ''; \
assert 'sm_70' in archs, 'torch was built without sm_70 (V100): ' + archs; \
print('torch', torch.__version__, 'torchvision', torchvision.__version__, \
'diffusers', diffusers.__version__, 'archs', archs)"

# ──────────────────────────────── app ───────────────────────────────
FROM ml AS app

# Weight cache and results live on volumes so they are not lost with the container
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

# One worker: the model fills VRAM, and extra workers would duplicate it.
# timeout 0 — generation on a V100 easily exceeds the default 30s.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "0", "--graceful-timeout", "60", "--access-logfile", "-", "wsgi:app"]
