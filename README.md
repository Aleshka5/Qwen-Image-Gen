# Qwen-Image Service

A Flask image-generation service based on [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1),
built for a **Tesla V100 32 GB**: up to 10 reference photos, a choice of resolution, and an English prompt.

---

## Network and access

Browsers open the service at **https://image.filenkov.store**. That is the Auth Gateway, not this process.
The `qwen-image` container is reachable only on `qwen_image_gen_network`: the gateway proxies to the upstream `http://qwen-image:8000`.
This repository creates the network. Auth-Service attaches to it from its own compose file as `external: true`.

The gateway decides who is allowed: the `image` role (`FAMILY` or `ADMIN` in User-Service).
The gateway strips incoming client `X-Auth-*` headers. This service has no login, sessions, role check, or JWT check.
The exception is `POST /api/generate` and `POST /api/mask-fill`: `X-Auth-User-Id` and `X-Auth-Email` are read only to sign the save into WebStorage.
`GET` routes, including `GET /mask-fill`, do not use those headers. A request that arrived here is already allowed.
The trust boundary is the network itself: `qwen_image_gen_network` may contain only the gateway and `qwen-image`.
WebStorage is not on `qwen_image_gen_network`. Any other container on that network bypasses the role check.

To call WebStorage after a successful generation or mask fill, this container also joins the Auth network.
`AUTH_DOCKER_NETWORK` is the Auth stack network (`deploy_auth_network` when Auth is started from `Auth-Service/deploy`).
This repository does not create it: the Auth stack must already be up.
On that network the container keeps the name `qwen-image` and calls the DNS name `app`, which is what WebStorage is called there.
Do not rename this container to `app`.

After a successful generation or mask fill the process `POST`s to `WEBSTORAGE_URL` (default `http://app:8000`) at `/api/generated`.
It forwards `X-Auth-User-Id` and, if that header was on the incoming request, `X-Auth-Email`.
`Cookie`, `Authorization`, and `X-Auth-Role` are not sent. This service does not verify a JWT.
The archive address in `.env` is `WEBSTORAGE_URL=http://app:8000`.

`GET /healthz` is anonymous in the application. The gateway serves that path without a role, for operator probes. Everything else requires the `image` role.

> **Never add `-p 8000:8000` or a `ports` section to `compose.yaml`.** That would put the GPU UI on the host network next to the gateway and bypass it.

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
would break `__call__`. Instead, [`app/offload.py`](app/offload.py) proxies the encoder: from the outside it
reports `device=cuda`, inside it computes on CPU (except vision and the GPU layers), and it returns embeddings to the GPU.

### Memory modes (`MEMORY_MODE`)

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

`compose.yaml` requests the same device: `devices: [nvidia.com/gpu=all]`. Without a CDI spec on the host, the GPU does not enter the container.

### 2. Prepare the environment

```bash
cp .env.example .env
```

In `.env`, set at least `MODEL_ID` / `PIPELINE_CLASS` (if you need another revision) and, for gated repositories, `HF_TOKEN`.

### 3. Build the image

```bash
podman build -t qwen-image-service:latest -f Dockerfile .
```

The build is multi-stage: `base` (CUDA + system Python 3.10) → `ml` (`uv sync --frozen --no-dev --group ml`: torch 2.7.1+cu126, diffusers, transformers, optimum-quanto from the lock) → `app` (service code; gunicorn is already a project dependency).
A code change rebuilds only `app`; the `ml` stage comes from the layer cache.
Do not use `--no-cache` without a reason — it reinstalls the whole ML stack (the wheels themselves
still come from the build's uv cache, not from the network). The `dev` group is not installed in the image.

To check only the ML stack, without building the service:

```bash
podman build --target ml -t qwen-image-ml -f Dockerfile .
```

### 4. Volumes for the weight cache and results, and the network

Weights download once and must survive recreating the container:

```bash
podman volume create qwen-hf-cache && podman volume create qwen-outputs
```

The network the Auth Gateway uses to reach the service (once):

```bash
podman network create qwen_image_gen_network
```

The Auth network (`AUTH_DOCKER_NETWORK`; `deploy_auth_network` when started from `Auth-Service/deploy`) is not created here.
The Auth stack must already be up before start: on that network WebStorage answers as `app`.

### 5. Start

Port 8000 is not published on the host — see [Network and access](#network-and-access).
The container joins both networks: `qwen_image_gen_network` and the Auth network.

```bash
podman run -d --name qwen-image \
  --network qwen_image_gen_network \
  --network "${AUTH_DOCKER_NETWORK:-deploy_auth_network}" \
  --device nvidia.com/gpu=all \
  --env-file .env \
  -v qwen-hf-cache:/data/huggingface \
  -v qwen-outputs:/data/outputs \
  --shm-size=8g --memory=64g \
  qwen-image-service:latest
```

The first start downloads ~33 GB of weights and quantizes the DiT. That takes tens of minutes. Progress is in the logs:

```bash
podman logs -f qwen-image
```

Readiness is checked from inside the network or from inside the container (the image has `curl`):

```bash
podman run --rm --network qwen_image_gen_network docker.io/curlimages/curl -s http://qwen-image:8000/healthz
```

```bash
podman exec qwen-image curl -fsS http://127.0.0.1:8000/healthz
```

`{"loaded": true}` means the model is in memory and the service accepts requests. The UI is at `https://image.filenkov.store/` (through the gateway).

Nothing listens on public `:8000` on the host. Both commands should print nothing:

```bash
podman port qwen-image; ss -ltn | grep :8000
```

### Podman Compose

`compose.yaml` starts the same `qwen-image` container (image `qwen-image-service:latest`) on two external networks at once: `qwen_image_gen_network` and `deploy_auth_network`. Both networks must exist before start. Step 4 creates the first. The Auth stack creates the second; this repository does not.

The file sets `env_file: .env`, volumes `qwen-hf-cache` and `qwen-outputs`, `shm_size: 8gb`, `mem_limit: 64g`, and the CDI device `nvidia.com/gpu=all`. There is no `ports` section: port 8000 stays inside the networks. `restart: always` brings the container back after the Podman daemon restarts.

```bash
podman compose up -d
```

`podman compose up -d` on an already running service recreates the container and loads the model again. Logs: `podman compose logs -f`.

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
podman run --rm --network qwen_image_gen_network -v "$PWD":/work:ro -w /work docker.io/curlimages/curl -X POST http://qwen-image:8000/api/generate -F "prompt=A cinematic portrait of the person, soft rim light, 85mm lens" -F "resolution=2048x2048" -F "steps=40" -F "true_cfg_scale=1" -F "images=@face1.jpg" -F "images=@face2.jpg" > result.json
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

### Other endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Web form for generation |
| `GET` | `/customize` | Web form for customizing one photo |
| `GET` | `/mask-fill` | Web form for filling a painted region of one photo |
| `GET` | `/healthz` | Status, memory mode, model load error |
| `GET` | `/api/config` | Limits and the list of resolution presets |
| `GET` | `/outputs/<name>` | Finished image (the latest `KEEP_OUTPUTS` files are kept) |

---

## Layout

```
app/
  config.py      snapshot of settings from the environment
  offload.py     text-encoder proxy (weights in RAM, interface "as if on GPU")
  generator.py   pipeline load, memory modes, generation under the GPU lock
  imaging.py     resolution presets, upload validation, saving results
  maskfill.py    pair the main photo, the mask, and ordered references
  routes.py      web form and JSON API
  __init__.py    application factory, background model preload
tests/           pytest suite, runs without a GPU and without ML dependencies
wsgi.py          gunicorn entry point
pyproject.toml   web dependencies, ml group (torch cu126), and dev group
Dockerfile       CUDA 12.6 + uv image; gunicorn listens on 0.0.0.0:8000
compose.yaml     two external networks, GPU, volumes, shm 8gb, memory 64g; no host port
```

Generation is serialized with `threading.Lock`: one card, one job at a time. Concurrent HTTP requests wait
in the queue instead of fighting over VRAM. That is why gunicorn starts with `--workers 1 --threads 4 --timeout 0`.

---

## Tests

Tests replace the generator with a stub: torch, diffusers, and a GPU are not required.

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
- [Design](docs/design.md)
- [Architecture decisions](docs/adr/)
- [Tests](docs/tests.md)

---

## Diagnostics

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

**Weights download on every start** — the volume `qwen-hf-cache:/data/huggingface` is not attached, or `HF_HOME` in `.env`
points somewhere else.
