# Qwen-Image Service

A project workspace in front of [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1),
built for a **Tesla V100 32 GB**. The page is one address. Generation is a queued task.
Local Qwen, Wan, OpenRouter, and Higgsfield can run it. The model process is still a separate project.

```
browser ──► Auth Gateway ──► qwen-image       Flask, the workspace UI, JSON API
   https://image.filenkov.store        │      this repository, no torch, no GPU
                                        │      deploy_auth_network + models_network + common_network
                                        ▼
                                 qimg-processor     intake → gen, common_network
                                        ▼
                                 qimg-orchestrator  claims the task
                                        │            common_network + models_network
                                        │  POST /v1/generate (multipart) ──► text/event-stream
                                        ▼
                                 qwen-inference     torch, the weights, the GPU
                                                    ../../Common/models/services/inference/
```

Mongo, MinIO, and RabbitMQ live on `common_network`. The orchestrator writes status and the
image there. The browser polls `GET /api/tasks/:id`. `qwen-inference` does not see those stores.

The contract for the page is [docs/api-contract.md](docs/api-contract.md). The queue and the
network choice are [ADR 0013](docs/adr/0013-workspace-queue-and-common-network.md). The SSE
contract is [docs/inference_api.md](docs/inference_api.md).

---

## Network and access

Browsers open the service at **https://image.filenkov.store**. That is the Auth Gateway, not this process.
The gateway proxies to `http://qwen-image:8000` and injects `X-Auth-User-Id`, `X-Auth-Email`, and `X-Auth-Name`.
This service does not authenticate. A project route with no user id is 400. It never answers 401 or 403.

`qwen-image` joins three external networks:

* `deploy_auth_network` — home. The gateway is here.
* `models_network` — client, so the name `qwen-inference` resolves. Created by Switch-Service (`make network`).
* `common_network` — `mongo`, `minio`, and `rabbit`. Without this network those names do not exist.

The processor joins only `common_network`. The orchestrator joins `common_network` and `models_network`.
This repository does not create any of those networks.

The hostname `redis` on the auth network is `rejected_cache`. Do not use it as a broker.
WebStorage is no longer the archive. Results go to the MinIO bucket `qimg-images`.
`MONGO_URL`, `RABBIT_URL`, and `MINIO_ENDPOINT` are set together, or not at all.
Bootstrap steps for the bucket, the IAM user, and the queues are in [docs/ops/qimg-bootstrap.md](docs/ops/qimg-bootstrap.md).

`GET /healthz` is anonymous in the application. The gateway serves that path without a role.

> **Never add `-p 8000:8000` or a `ports` section to `compose.yaml`.** The same holds for `8010`.

---

## What the V100 requires

The V100 is Volta (SM 7.0). These limits apply to `qwen-inference`, not to the three processes in this compose file.

| Limit | How the model works around it |
|---|---|
| No hardware **bfloat16** | Weights load in `float16`; the text-encoder tail in RAM is `float16` too |
| No **FlashAttention-2** (needs SM 8.0+) | `ATTENTION_BACKEND=native` — PyTorch SDPA. The name `sdpa` is accepted as an alias |
| The whole pipeline in fp16 is about **33 GB** | `int8` compresses the DiT; the vision tower and 24 decoder layers stay in VRAM |

Qwen-Image-2.1 is a 7B DiT and a **Qwen3-VL 8B** text encoder. The encoder does not go on the card as a whole.
[`inference/pipeline/offload.py`](inference/pipeline/offload.py) proxies it so diffusers still sees `device=cuda`.

### Memory modes (`MEMORY_MODE`)

Read by `qwen-inference` only. On the measured host the loaded mode was `offload`.

| Mode | VRAM | Speed | When to use it |
|---|---|---|---|
| `int8` | ~20 GB of weights + KV cache | baseline | the normal mode for references |
| `fp16` | ~16 GB for the DiT alone | faster | text-to-image and 1–2 photos |
| `offload` | ~8 GB | 3–10× slower | when even int8 runs out of VRAM |

`true_cfg_scale` above 1 keeps a second KV cache and, with many references, overflows 32 GB.

---

## Requirements

* Podman with `podman compose`
* For the **model** project only: GPU passthrough (`nvidia-container-toolkit` + CDI, device `nvidia.com/gpu=all`), NVIDIA driver 560+, ~40 GB of disk for the weights, ~40 GB of RAM
* The three processes here need no GPU. They need `common_network`, `models_network`, and `deploy_auth_network` to already exist

---

## Quick start

### 1. Configure CDI for the GPU (once per host, for the model)

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

Check that Podman sees the card through CDI:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

The model's compose file requests the same device. This repository's compose file does not.

### 2. Prepare the environment

```bash
cp .env.example .env
```

Set `MONGO_URL`, `RABBIT_URL`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, and `SETTINGS_SECRET`.
`INFERENCE_URL` is `http://qwen-inference:8010`.
Model settings (`MEMORY_MODE`, `TEXT_ENCODER_*`, `VAE_*`) live in
[`Common/models/services/inference/env.example`](../Common/models/services/inference/env.example).

Leaving `MONGO_URL`, `RABBIT_URL`, and `MINIO_ENDPOINT` empty starts an in-memory store. That is for tests and a local page, not for compose on the host.

### 3. Build the images

Both Dockerfiles live here. This compose file builds only the web image. The model image is built by the inference project:

```bash
podman build -t localhost/qwen-inference:latest -f Dockerfile .
```

```bash
podman build -t localhost/qwen-image-service:latest -f Dockerfile.web .
```

`Dockerfile` installs `uv sync --frozen --no-dev --group ml`.
`Dockerfile.web` installs `uv sync --frozen --no-dev --no-install-project` and copies `app/` and `qimg/`.
The `dev` group is not installed in either image.

To check only the ML stack:

```bash
podman build --target ml -t qwen-image-ml -f Dockerfile .
```

### 4. Networks and the bucket

Neither network is created here. `models_network` comes from Switch-Service:

```bash
cd ../Switch-Service && make network
```

`deploy_auth_network` belongs to the Auth stack. `common_network` belongs to the shared backends.
The MinIO bucket `qimg-images` and the IAM user `qimg` are created by hand. See [docs/ops/qimg-bootstrap.md](docs/ops/qimg-bootstrap.md).

```bash
podman volume create qwen-outputs
```

The weight cache `qwen-hf-cache` is mounted by the model project, not by this one.

### 5. Start

The model first, from its own project or from Switch-Service:

```bash
cd ../Switch-Service && .venv/bin/switch-service --url http://127.0.0.1:8095 state image gpu
```

```bash
cd ../Common/models/services/inference && ./run.sh
```

Then this stack. `podman compose` starts `qwen-image`, `qimg-processor`, and `qimg-orchestrator` from one image.

```bash
podman compose up -d
```

A single web container, when you are not using compose, still has to reach all three networks:

```bash
podman run -d --name qwen-image \
  --network "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" \
  --network models_network \
  --network common_network \
  --env-file .env \
  -v qwen-outputs:/data/outputs \
  --memory=2g \
  localhost/qwen-image-service:latest
```

That command starts only the web process. The processor and the orchestrator are the other two compose services (`python -m qimg.processor` and `python -m qimg.orchestrator`).

Readiness, from inside the container:

```bash
podman exec qwen-image curl -fsS http://127.0.0.1:8000/healthz
```

```bash
podman exec qwen-inference curl -fsS http://127.0.0.1:8010/healthz
```

The UI is at `https://image.filenkov.store/`. Nothing on the host should be listening on 8000:

```bash
podman port qwen-image; ss -ltn | grep -E ':(8000)'
```

To watch the inference stream from inside `models_network`:

```bash
podman run --rm --network models_network -v "$PWD":/work:ro -w /work docker.io/curlimages/curl -N \
  -X POST http://qwen-inference:8010/v1/generate \
  -F "prompt=a cinematic portrait, soft rim light" -F "width=512" -F "height=512" -F "steps=4" -F "seed=7"
```

### Podman Compose

`compose.yaml` starts three containers from `localhost/qwen-image-service:latest`. The model is a separate project, so there is no `depends_on`. There is no `ports` section. `restart: always` brings the containers back after the Podman daemon restarts. The workers disable the image healthcheck because they do not listen on 8000.

Logs: `podman compose logs -f qwen-image`.

> **On `master-node` the model container belongs to Switch-Service**, not to this compose file. Start it with `switch-service state image gpu`.

---

## API

The page is `GET /`. Old form URLs are gone. Field-by-field detail is [docs/api-contract.md](docs/api-contract.md).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Workspace shell |
| `GET` | `/healthz` | This process, plus whether inference is loaded |
| `GET` | `/api/config` | Limits, aspects, colors |
| `GET` | `/api/catalog` | Provider kinds and models |
| `GET` | `/api/settings` | The caller's providers, secrets redacted |
| `GET`/`POST` | `/api/projects` | List, or upload a photo / start from scratch |
| `POST` | `/api/generations` | Queue one batch. Identical inputs return the existing batch |
| `GET` | `/api/tasks/:id` | Status. Local jobs include `progress.stage` / `total` |
| `POST` | `/api/renders` | Composite selected variants onto the original |
| `GET` | `/api/media/<key>` | A PNG the caller owns |

`POST /v1/generate` is the internal inference contract, from the orchestrator to `http://qwen-inference:8010`, never from the gateway. The response is `text/event-stream`: `start`, `progress`, `log`, then `done` or `error`. [Change request 0001](docs/change-requests/0001-inference-progress-events.md) is implemented.

---

## Layout

```
app/                HTTP boundary (routes, imaging, inference client)
qimg/               workspace use cases, workers, providers, memory and real backends
  processor.py      python -m qimg.processor
  orchestrator.py   python -m qimg.orchestrator
frontend/           Vite + React source. The build is committed under app/static/ui
inference/          the pipeline image only (Dockerfile, port 8010)
wsgi.py             gunicorn entry of the web process
tests/              pytest, no GPU
pyproject.toml      web dependencies, ml group, dev group
Dockerfile          CUDA image for qwen-inference
Dockerfile.web      python:3.10-slim for the three processes
compose.yaml        qwen-image, qimg-processor, qimg-orchestrator; no host port
```

---

## Tests

Tests use the in-memory store. Torch, a GPU, Mongo, RabbitMQ, and MinIO are not required.

```bash
uv sync
```

```bash
uv run pytest
```

The Podman network smoke test runs only when asked. It needs a locally built image `qwen-image-service:latest`. A GPU is not required:

```bash
RUN_PODMAN_TESTS=1 uv run pytest -m podman
```

---

## Documentation

- [API contract](docs/api-contract.md)
- [Internal inference contract (SSE)](docs/inference_api.md)
- [Design](docs/design.md)
- [Architecture decisions](docs/adr/)
- [Tests](docs/tests.md)
- [Operator bootstrap](docs/ops/qimg-bootstrap.md)
- [Inference progress change request](docs/change-requests/0001-inference-progress-events.md)

---

## Diagnostics

**`{"loaded": false}` from `qwen-image`** — ask the pipeline:
`podman exec qwen-inference curl -fsS http://127.0.0.1:8010/healthz`.
If the name does not resolve, `qwen-image` is missing `models_network`.

**The process exits on startup with `MONGO_URL`** — the three backend variables must be set together, and the container must be on `common_network`. An empty trio is the in-memory mode and is not what compose on the host wants.

**`redis` answers, `mongo` does not** — the container is on the auth network only. `redis` there is `rejected_cache`.

**External provider returns no image** — the key was rejected, or the model does not accept the mode. The task status is `failed` and `error` has the provider text. The key itself is not in the response.

**A result vanishes after delete** — that is the rule. The worker drops the blob when the project is already deleted.

**`CUDA out of memory`** — that is the model container. Drop references, lower the size, leave `true_cfg_scale=1`, then `MEMORY_MODE=offload`.

**Weights download on every model start** — `qwen-hf-cache` is not on `qwen-inference`.
