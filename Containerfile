# Tesla V100 (SM 7.0): CUDA 12.1 + колёса torch cu121 — последняя связка,
# в которой sm_70 собирается штатно и есть свежий diffusers.
FROM docker.io/nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv \
        libgl1 libglib2.0-0 ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

# Torch отдельным слоем: тяжёлый и меняется реже прикладных зависимостей
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cu121 \
        torch==2.5.1 torchvision==0.20.1

WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py .

# Кеш весов и результаты — на томах, чтобы не тонули вместе с контейнером
RUN useradd --create-home --uid 1000 service \
    && mkdir -p /data/huggingface /data/outputs \
    && chown -R service:service /data /srv/app
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
