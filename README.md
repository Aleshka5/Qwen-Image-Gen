# Qwen-Image Service

A Flask image-generation service based on [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1),
built for a **Tesla V100 32 GB**: up to 10 reference photos, a choice of resolution, and an English prompt.

It runs as **two independent containers with one API**, in two different networks:

```
browser ──► Auth Gateway ──► qwen-image      Flask, UI, JSON API, WebStorage, no torch, no GPU
   (deploy_auth_network)         │            this repository, compose.yaml, ~212 MB
                                 │
                                 │  POST /v1/generate (multipart)  ──►  text/event-stream
                                 ▼            via models_network (name resolution only)
                             qwen-inference  torch 2.7.1+cu126, Qwen-Image 2.1, the GPU,
                                             weight cache, the pipeline and its logs
                                             ../../Common/models/services/inference/
                                             switch-service `image` on master-node
```

The two halves are separate projects: separate images, separate compose files, separate
networks, separate lifecycles. This repository owns **the web face only**; the model
container is launched from
[`Common/models/services/inference`](../Common/models/services/inference/README.md) (compose +
`run.sh`) or, on master-node, by Switch-Service (`switch-service state image gpu`).
Its network is `models_network`; the web face joins that network as a client because that is
where the name `qwen-inference` resolves, and it never publishes a port to the host.

The browser-facing protocol is unchanged: same pages, same JSON, same `/api/logs` polling.
What changed is where the weights live — the pipeline moved, as is, into its own container and
streams its log lines back over SSE. The contract is in [docs/inference_api.md](docs/inference_api.md),
the reasoning in [ADR 0011](docs/adr/0011-split-inference-process.md).

---

## Network and access

Browsers open the service at **https://image.filenkov.store**. That is the Auth Gateway, not this process.
The `qwen-image` container is reachable on `deploy_auth_network` (its home network): the gateway proxies to the upstream `http://qwen-image:8000`.
It also joins `models_network`, and that is where it resolves `qwen-inference:8010`.
Both networks are external to this repository: `models_network` is created by Switch-Service (`make network`), the auth network by the Auth stack.

The gateway decides who is allowed: the `image` role (`FAMILY` or `ADMIN` in User-Service).
The gateway strips incoming client `X-Auth-*` headers. This service has no login, sessions, role check, or JWT check.
The exception is `POST /api/generate` and `POST /api/mask-fill`: `X-Auth-User-Id` and `X-Auth-Email` are read only to sign the save into WebStorage.
`GET` routes, including `GET /mask-fill`, do not use those headers. A request that arrived here is already allowed.
The trust boundary is the network itself: `deploy_auth_network` may contain only the gateway, this service and the Auth stack.
`qwen-inference` is the model half of the same service, serves no UI, is called only by `qwen-image`
over its private HTTP contract, and lives in `models_network`; do not put any other container on that network's behalf.
WebStorage is not on `models_network` — that is why the web face has two networks and not one.

To call WebStorage after a successful generation or mask fill, `qwen-image` also joins the Auth network.
`AUTH_DOCKER_NETWORK` is the Auth stack network (`deploy_auth_network` when Auth is started from `Auth-Service/deploy`).
This repository does not create it: the Auth stack must already be up.
On that network the container keeps the name `qwen-image` and calls the DNS name `app`, which is what WebStorage is called there.
Do not rename this container to `app`.

After a successful generation or mask fill the process `POST`s to `WEBSTORAGE_URL` (default `http://app:8000`) at `/api/generated`.
It forwards `X-Auth-User-Id` and, if that header was on the incoming request, `X-Auth-Email`.
`Cookie`, `Authorization`, and `X-Auth-Role` are not sent. This service does not verify a JWT.
The archive address in `.env` is `WEBSTORAGE_URL=http://app:8000`.

`GET /healthz` is anonymous in the application. The gateway serves that path without a role, for operator probes. Everything else requires the `image` role.

> **Never add `-p 8000:8000` or a `ports` section to `compose.yaml`.** That would put the UI on the host network next to the gateway and bypass it.
> The same holds for `8010` — the inference port stays inside `models_network` (the model's compose publishes it on `127.0.0.1` for the health probe only).

---

## What the V100 requires

The V100 is Volta (SM 7.0), and that imposes three hard limits:

| Limit | How we work around it |
|---|---|
| No hardware **bfloat16** | Weights load in `float16`; the text-encoder tail in RAM is `float16` too |
| No **FlashAttention-2** (needs SM 8.0+) | `ATTENTION_BACKEND=native` — PyTorch SDPA (`mem_efficient` / `math`). The name `sdpa` is accepted as an alias |
| The whole pipeline in fp16 is about **33 GB** (DiT 7B ~14 GB, Qwen3-VL 8B ~17.5 GB, VAE ~1.4 GB) | The encoder does not go on the card as a whole. `int8` compresses the DiT to ~7 GB; the vision tower and 24 decoder layers stay in VRAM, and the encoder tail stays in RAM in `float16` |

Qwen-Image-2.1 is a 7B single-stream DiT and a **Qwen3-VL 8B** text encoder.
The encoder **does not go on the card as a whole**: together with the DiT and the VAE it takes about 33 GB. The vision tower and the first decoder layers live in VRAM (references pass through them); the tail lives in RAM.

Diffusers assumes every pipeline module is on one device, so `text_encoder.to("cpu")`
would break `__call__`. Instead, [`inference/pipeline/offload.py`](inference/pipeline/offload.py) proxies the encoder: from the outside it
reports `device=cuda`, inside it computes on CPU (except vision and the GPU layers), and it returns embeddings to the GPU.

### Memory modes (`MEMORY_MODE`)

Read by `qwen-inference` only.

| Mode | VRAM | Speed | When to use it |
|---|---|---|---|
| `int8` *(default)* | ~20 GB of weights (DiT int8 + VAE + 24 encoder layers) + KV cache | baseline | the normal mode for references: DiT ~7 GB, the rest of VRAM is the encoder and KV. Each reference is about 2 GB of KV in fp16 |
| `fp16` | ~16 GB for the DiT alone, encoder almost entirely in RAM | faster | text-to-image and 1–2 photos. Ten references do not fit this budget; RAM fills before VRAM |
| `offload` | ~8 GB | 3–10× slower | when even int8 runs out of VRAM |

Compute stays fp16 even in `int8`: that is what the V100 tensor cores do. Quantization saves weight memory; it does not speed up matmul.
`true_cfg_scale` above 1 keeps a second KV cache and, with ten references, overflows 32 GB in every mode.

---

## Requirements

* Podman with `podman compose` and GPU passthrough (`nvidia-container-toolkit` + CDI, device `nvidia.com/gpu=all`)
* NVIDIA driver 560+ (checked on 580.178.04)
* **~40 GB** of free disk for the weights (the files themselves are about 33 GB)
* **~40 GB** of free RAM: the Qwen3-VL tail in fp16 plus headroom while loading. 64 GB is the margin if you return to `TEXT_ENCODER_DTYPE=float32`

---

## Quick start

### 1. Configure CDI for the GPU (once per host)

```bash
sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
```

Check that Podman sees the card through CDI:

```bash
podman run --rm --device nvidia.com/gpu=all docker.io/nvidia/cuda:12.6.3-base-ubuntu22.04 nvidia-smi
```

The model's compose file requests the same device on `qwen-inference`: `devices: [nvidia.com/gpu=all]` in
[`Common/models/services/inference/compose.yaml`](../Common/models/services/inference/compose.yaml). Without a CDI spec on the host, the GPU does not enter the container.

### 2. Prepare the environment

```bash
cp .env.example .env
```

This `.env` is the web face's: it holds `INFERENCE_URL`, the generation limits and the WebStorage address.
Set at least `INFERENCE_URL` and, if WebStorage lives elsewhere, `WEBSTORAGE_URL`.
`INFERENCE_URL` is how `qwen-image` finds the pipeline: `http://qwen-inference:8010` inside the network.

The web face no longer shares an env file with the model: model settings (`MEMORY_MODE`,
`TEXT_ENCODER_*`, `VAE_*`, `PRELOAD_MODEL`) live in
[`Common/models/services/inference/env.example`](../Common/models/services/inference/env.example).
The keys in this repository's `.env` that the model used to read are ignored by the web face.

### 3. Build the images

Two images, because they have two different contracts and two different lifetimes. Both
`Dockerfile`s live here; **only the web one is built by this repository's compose**. The
model image is built by [`Common/models/services/inference/run.sh`](../Common/models/services/inference/run.sh):

```bash
podman build -t localhost/qwen-inference:latest -f Dockerfile .
```

```bash
podman build -t localhost/qwen-image-service:latest -f Dockerfile.web .
```

`Dockerfile` builds the inference image: `base` (CUDA + system Python 3.10) → `ml`
(`uv sync --frozen --no-dev --group ml`: torch 2.7.1+cu126, diffusers, transformers, optimum-quanto from the lock) → `app` (the `inference/` package).
A code change rebuilds only `app`; the `ml` stage comes from the layer cache.

`Dockerfile.web` builds the orchestrator from the same lock **without the `ml` group**: Flask, gunicorn, Pillow.
That is ~200 MB instead of ~8 GB, and it cannot import torch even by accident.

Do not use `--no-cache` without a reason — it reinstalls the whole ML stack (the wheels themselves
still come from the build's uv cache, not from the network). The `dev` group is not installed in either image.

To check only the ML stack, without building the service:

```bash
podman build --target ml -t qwen-image-ml -f Dockerfile .
```

### 4. Volumes and the two networks

Results survive recreating the container:

```bash
podman volume create qwen-outputs
```

Neither network is created here, and both must exist before start:

```bash
cd ../Switch-Service && make network    # models_network (+ attaches the gateway and rag-chat)
```

The auth network (`AUTH_DOCKER_NETWORK`; `deploy_auth_network` when started from `Auth-Service/deploy`) belongs to the
Auth stack. It must already be up before start: on that network WebStorage answers as `app`, and the gateway is what
sends browser traffic to `http://qwen-image:8000`.

The weight cache `qwen-hf-cache` is not this project's volume any more: the model container mounts it from
[`Common/models/services/inference`](../Common/models/services/inference/compose.yaml).

### 5. Start

The model first: it needs the card, and it loads the weights. It is **another project**, so this repository does not
start it — either the switch, or that project's own script:

```bash
cd ../Switch-Service && .venv/bin/switch-service --url http://127.0.0.1:8095 state image gpu
```

```bash
cd ../Common/models/services/inference && ./run.sh
```

Then the web face. It needs no GPU and no weight cache; it keeps the network name `qwen-image`, which is what the
gateway proxies to, and it joins `models_network` only to resolve `qwen-inference:8010`.

```bash
podman run -d --name qwen-image \
  --network "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" \
  --network models_network \
  --env-file .env \
  -v qwen-outputs:/data/outputs \
  --memory=2g \
  localhost/qwen-image-service:latest
```

The first start of `qwen-inference` downloads ~33 GB of weights and, in `int8`, quantizes the DiT.
That takes tens of minutes. Progress is in the logs:

```bash
podman logs -f qwen-inference
```

Every generation logs to the same place, one line per pipeline step; the browser reads the same lines through `/api/logs`.

Readiness is checked from inside the container (both images have `curl`).
The web face reports its own state **and** the inference state:

```bash
podman exec qwen-image curl -fsS http://127.0.0.1:8000/healthz
```

```bash
podman exec qwen-inference curl -fsS http://127.0.0.1:8010/healthz
```

`{"loaded": true}` from `qwen-image` means the pipeline behind it is loaded and the service accepts requests.
The UI is at `https://image.filenkov.store/` (through the gateway).

Nothing listens on public `:8000` on the host, and nothing listens on `:8010` outside the container's network.
These should print nothing:

```bash
podman port qwen-image; ss -ltn | grep -E ':(8000)'
```

To watch the stream the web face consumes, ask the inference container directly (it is the only container in
`models_network`, so run the client inside that network):

```bash
podman run --rm --network models_network -v "$PWD":/work:ro -w /work docker.io/curlimages/curl -N \
  -X POST http://qwen-inference:8010/v1/generate \
  -F "prompt=a cinematic portrait, soft rim light" -F "width=512" -F "height=512" -F "steps=4" -F "seed=7"
```

### Podman Compose

`compose.yaml` starts **one** container: the web face. The model is a separate project with its own compose file
([`Common/models/services/inference/compose.yaml`](../Common/models/services/inference/compose.yaml)) and its own
network, so there is no `depends_on` between them and no shared project name.

The file sets `env_file: .env`, `mem_limit: 2g`, the volume `qwen-outputs`, and the networks
`deploy_auth_network` (home) and `models_network` (client). There is no `ports` section: 8000 stays inside the
networks. `restart: always` brings the container back after the Podman daemon restarts.

```bash
podman compose up -d
```

`podman compose up -d` on an already running stack recreates the container; the weights are not touched, because
they belong to the model container. Logs: `podman compose logs -f qwen-image`.

> **On `master-node` the model container belongs to Switch-Service**, not to compose: the catalog
> `Switch-Service/config/services.models-network.yaml` declares it as service `image` (container name
> `qwen-inference`, GPU, weights, 28672 MiB of VRAM) and creates it in `models_network`. Start it with
> `switch-service state image gpu`. Using the other project's `run.sh` there is refused on purpose: two owners of
> one container name recreate it every reconcile, and only the switch enforces the VRAM budget.

---

## API

### `POST /api/generate` — `multipart/form-data`

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | — | Required, English only (Cyrillic is rejected) |
| `negative_prompt` | string | `" "` | English as well |
| `images` | file[] | — | Up to 10 files: JPEG/PNG/WEBP/BMP, together up to `MAX_UPLOAD_MB` |
| `size_mode` | string | — | `preset` or `custom`. Without the field, the old `resolution` input still works |
| `quality` | string | `high` | With `size_mode=preset`: `high` (~2K) or `medium` (same aspect, about 1024²) |
| `aspect` | string | `1:1` | With `size_mode=preset`: `1:1`, `4:3`, `3:4`, `3:2`, `2:3`, `16:9`, `9:16` |
| `width`, `height` | int | — | With `size_mode=custom`: each side 32…3000 px, then rounded down to a multiple of 32 |
| `resolution` | string | `2048x2048` | Legacy input: a preset key or `WxH` in the same 32…3000 range |
| `steps` | int | 40 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 1.0 | 1.0…10.0. `1.0` means no guidance, the normal mode for 2.1 |
| `seed` | int | `-1` | `-1` means random |

From outside, requests go to `https://image.filenkov.store/api/generate` and need a gateway session with the `image` role.
For an operator debug session, from inside the network, call `http://qwen-image:8000` directly:

```bash
podman run --rm --network models_network -v "$PWD":/work:ro -w /work docker.io/curlimages/curl -X POST http://qwen-image:8000/api/generate -F "prompt=A cinematic portrait of the person, soft rim light, 85mm lens" -F "resolution=2048x2048" -F "steps=40" -F "true_cfg_scale=1" -F "images=@face1.jpg" -F "images=@face2.jpg" > result.json
```

Response after a successful save to WebStorage:

```json
{
  "seed": 1823486689,
  "duration": 96.4,
  "width": 2048,
  "height": 2048,
  "images": [{"name": "20260922-124332-3b47ec64.png", "url": "/outputs/20260922-124332-3b47ec64.png"}],
  "saved": true,
  "storage_id": "20260927T115012Z-3b47ec64"
}
```

If `X-Auth-User-Id` is not a UUID, or WebStorage returned an error, timed out, or was unreachable, generation is still `200`: `saved` is `false`, `storage_error` is the error text, there is no `storage_id`, and `images` still point at the local PNG.

Validation errors are `400`, a pipeline that is unavailable or out of memory is `503`, and an upload that is too large is `413`. WebStorage is not called in those cases.

### `POST /api/customize` — `multipart/form-data`

Edits one photo. The form has no resolution: the size comes from the file. The response is the same width and height.

| Field | Type | Default | Description |
|---|---|---|---|
| `system_prompt` | string | — | Required, English only. What to change in the photo |
| `negative_prompt` | string | `""` | English as well |
| `image` | file | — | Exactly one file: JPEG/PNG/WEBP/BMP |
| `steps` | int | 40 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 1.0 | 1.0…10.0 |
| `seed` | int | `-1` | `-1` means random |

Fitting to a multiple of 32 and the side limit, then restoring the result to the original width and height, is hidden in `PhotoFrame` (`app/imaging.py`). In the JSON, `width` and `height` are the uploaded photo's size, not the intermediate model frame.

### `POST /api/mask-fill` — `multipart/form-data`

Fills the white region of a mask on one photo. There is no size picker. `width` and `height` in the JSON are the uploaded photo's size. The page is `GET /mask-fill`; that GET does not read identity headers.

| Field | Type | Default | Description |
|---|---|---|---|
| `prompt` | string | — | Required, English only (Cyrillic is rejected) |
| `negative_prompt` | string | `""` | English as well |
| `image` | file | — | Exactly one main photo: JPEG/PNG/WEBP/BMP |
| `mask` | file | — | Exactly one file. The page sends a PNG. White is the area to fill. Same pixel size as the main photo |
| `images` | file[] | — | Ordered reference photos, same formats as generate. At most `MAX_IMAGES - 2` |
| `steps` | int | 40 | 1…`MAX_STEPS` |
| `true_cfg_scale` | float | 1.0 | 1.0…10.0 |
| `seed` | int | `-1` | `-1` means random |

The pipeline and the WebStorage archive both receive files in this order: the main photo, the mask, then the reference photos. The prompt is sent as written.

From outside, requests go to `https://image.filenkov.store/api/mask-fill` and need a gateway session with the `image` role. The JSON shape matches generation, including `saved` and `storage_id` after a successful save.

Save rules match `POST /api/generate`. A UUID `X-Auth-User-Id` saves the run and returns `saved: true`. Without a UUID, WebStorage is not called and `storage_error` is `not saved: no user id`. If WebStorage returned an error, timed out, or was unreachable, the response is still `200`: `saved` is `false`, `storage_error` is the error text, there is no `storage_id`, and `images` still point at the local PNG. Validation errors are `400`, a pipeline that is unavailable or out of memory is `503`, and an upload that is too large is `413`. WebStorage is not called in those cases.

### `POST /v1/generate` — the internal contract

`qwen-image` → `qwen-inference`, never exposed to the gateway. `multipart/form-data` in,
`text/event-stream` out: `start`, one `log` per pipeline line, then `done` with the PNGs in base64
or `error`. Field by field, with timeouts and failure semantics: [docs/inference_api.md](docs/inference_api.md).

### Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Web form for generation |
| `GET` | `/customize` | Web form for customizing one photo |
| `GET` | `/mask-fill` | Web form for filling a painted region of one photo |
| `GET` | `/healthz` | Status, memory mode, model load error, and the inference URL it polls |
| `GET` | `/api/config` | Limits and the list of resolution presets |
| `GET` | `/api/logs` | Log lines after an id: the local ones plus the pipeline lines from the SSE stream |
| `GET` | `/outputs/<name>` | Finished image (the latest `KEEP_OUTPUTS` files are kept) |

---

## Layout

```
app/                the orchestrator (image: localhost/qwen-image-service:latest)
  config.py         settings of this process: inference URL, timeouts, limits
  inference.py      HTTP + SSE client of the inference container
  generator.py      facade over that client, under the names routes already used
  imaging.py        resolution presets, upload validation, saving results
  maskfill.py       pair the main photo, the mask, and ordered references
  logbuf.py         ring buffer behind /api/logs (local lines + pipeline lines)
  routes.py         web form and JSON API
  __init__.py       application factory
inference/          the pipeline (image: localhost/qwen-inference:latest)
  app.py            POST /v1/generate -> SSE, GET /healthz
  logstream.py      logging -> per-request queue -> event: log
  config.py         model, memory mode, precision, port
  pipeline/
    generator.py    pipeline load, memory modes, generation under the GPU lock (moved as is)
    offload.py      text-encoder proxy (weights in RAM, interface "as if on GPU")
    types.py        request/result/error shared by both sides
wsgi.py             gunicorn entry point of the orchestrator
inference/wsgi.py   gunicorn entry point of the pipeline
tests/              pytest suite, runs without a GPU and without ML dependencies
pyproject.toml      web dependencies, ml group (torch cu126), and dev group
Dockerfile          CUDA 12.6 + torch image; gunicorn on 0.0.0.0:8010 (inference)
Dockerfile.web      python:3.10-slim image; gunicorn on 0.0.0.0:8000 (orchestrator)
compose.yaml        the web face only: deploy_auth_network (home) + models_network (client); no host port
```

Generation is serialized with `threading.Lock` inside `qwen-inference`: one card, one job at a time. Concurrent requests wait
in the queue instead of fighting over VRAM. That is why both containers run with `--workers 1`.
The inference container runs `--threads 4`: one thread holds the SSE stream, another must answer `/healthz`
while a job is running. The orchestrator runs `--threads 8`: one thread waits on the stream, the page keeps polling `/api/logs`.

---

## Tests

Tests replace the pipeline: torch, diffusers, a GPU, and a running container are not required.
`app.generator` is stubbed before the package is imported; `inference.app` is built with a fake pipeline factory;
`app.inference` is exercised against a real SSE server on `127.0.0.1`.

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

---

## Diagnostics

**`{"loaded": false}` from `qwen-image`** — ask the pipeline itself, it carries the error text:
`podman exec qwen-inference curl -fsS http://127.0.0.1:8010/healthz`. If that is unreachable, `qwen-image` cannot resolve
`INFERENCE_URL` (wrong network, or the inference container is down); `load_error` on the orchestrator starts with "инференс недоступен".

**`CUDA out of memory`** — drop extra references, lower the resolution to `1024x1024`, and leave `true_cfg_scale=1`.
Ten photos with guidance above 1 keep two KV caches and do not fit in 32 GB. Then set `MEMORY_MODE=offload`.
Check that another process is not holding VRAM: `nvidia-smi`.

**`module diffusers has no attribute QwenImage…Pipeline` / `Cannot find class …` / `KeyError` while loading** — the installed diffusers is older than the model.
In the `ml` group of `pyproject.toml`, pin diffusers to a fresh main commit:
`diffusers @ https://github.com/huggingface/diffusers/archive/<sha>.tar.gz` (the image does not need git)
and rebuild the image (`uv lock`, then `podman build`). That is already how `QwenImage21Pipeline` (Qwen-Image-2.1) is pinned.

**The model does not accept images** — the chosen `MODEL_ID` points at a text-to-image variant with no
image condition. Use an edit revision of the model, or stop uploading photos.

**The process is killed by the OOM killer at start or on references** — the encoder is still in RAM. Check
`TEXT_ENCODER_DTYPE=float16` and `TEXT_ENCODER_GPU_LAYERS` (24 by default). `MEMORY_MODE=fp16`
leaves the DiT at 14 GB of VRAM and almost the whole encoder in RAM, so references run out of RAM.
If the container limit is too low, raise `mem_limit` in `compose.yaml` (or `--memory` on `podman run`).

**`RuntimeError: "addmm_impl_cpu_" not implemented for 'Half'`** — an old PyTorch CPU backend cannot do fp16
on CPU. Go back to `TEXT_ENCODER_DTYPE=float32`.

**Slow, several minutes per frame** — expected for 2048 and 40 steps on a V100 without FlashAttention.
`true_cfg_scale` above 1 doubles the transformer pass; for 2.1 leave it at 1. Resolution `1024x1024` is noticeably faster.

**Weights download on every start** — the volume `qwen-hf-cache:/data/huggingface` is not attached to `qwen-inference`, or `HF_HOME` in `.env`
points somewhere else.

**The page shows the pipeline log only after the image is ready** — something between the two containers buffers the stream.
The response sets `X-Accel-Buffering: no`; check that no proxy in front of `qwen-inference` ignores it, and that
`INFERENCE_URL` points at gunicorn directly (it must not go through the gateway).
