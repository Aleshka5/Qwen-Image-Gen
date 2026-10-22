# Service design

A Flask process and a pipeline process generate images with [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) on a Tesla V100 32 GB. The browser opens the Auth Gateway (`https://image.filenkov.store`). This repository is the GPU worker and three HTML forms, with no login and no history.

The workspace is one service in two containers: `qwen-image` (Flask, pages, JSON, WebStorage, no torch, no GPU) and `qwen-inference` (torch, weights, the GPU, the pipeline and its logs). They talk over one internal `text/event-stream` endpoint; the browser protocol is unchanged. See [inference_api.md](inference_api.md) and [adr/0011-split-inference-process.md](adr/0011-split-inference-process.md).

Field contract: [api-contract.md](api-contract.md). Why the network, memory, diffusers pin, archive, Podman, uv, and a single worker are set up this way: [adr/](adr/).

Application behavior does not change when packaging or deploy changes. The runtime is Podman Compose and uv. The old Quadlet unit is described in the historical stories [user-stories/user-story-behind-auth-gateway.md](user-stories/user-story-behind-auth-gateway.md) and [user-stories/user-story-webstorage-generate.md](user-stories/user-story-webstorage-generate.md).

## Module map

### `qwen-image` — the orchestrator (`Dockerfile.web`, no torch)

| Module | Role |
|---|---|
| `wsgi.py` | `app = create_app()` for `gunicorn wsgi:app`. `python wsgi.py` listens on `HOST`/`PORT` |
| `app/__init__.py` | Flask factory: body limit, JSON, blueprint, 500 handler, background inference-state probe |
| `app/config.py` | Frozen `Settings` from the environment, read once at import: inference URL, timeouts, limits |
| `app/routes.py` | Forms and JSON: parse fields, call the client, write PNGs, call the archive |
| `app/imaging.py` | Presets, file validation, `PhotoFrame` for customize, PNG names, directory prune |
| `app/maskfill.py` | Pair the main photo and the mask, fit both, restore the result. Public entry `prepare_mask_fill` |
| `app/inference.py` | `InferenceClient`: multipart → `POST /v1/generate`, SSE → log lines and PNGs |
| `app/generator.py` | Facade re-exporting that client under the names the routes already use |
| `app/webstorage.py` | `POST {WEBSTORAGE_URL}/api/generated` on the stdlib, no SDK |
| `app/logbuf.py` | Ring of 400 lines for `GET /api/logs`: local records plus the pushed pipeline lines |
| `app/templates/`, `app/static/` | Forms `index.html`, `customize.html`, and `mask_fill.html`, log polling |

### `qwen-inference` — the pipeline (`Dockerfile`, torch 2.7.1+cu126)

| Module | Role |
|---|---|
| `inference/wsgi.py` | `app = create_app()` for `gunicorn inference.wsgi:app` |
| `inference/app.py` | `GET /healthz`, `POST /v1/generate` → SSE (`start`, `log`*, `done` / `error`) |
| `inference/logstream.py` | `logging` handler bound to a per-request `ContextVar` → queue → `event: log` |
| `inference/config.py` | Frozen `Settings` for the model: mode, precision, port |
| `inference/pipeline/generator.py` | Load diffusers, memory modes, one `threading.Lock` on the GPU (moved as is) |
| `inference/pipeline/offload.py` | `CpuHostedTextEncoder`: Qwen3-VL tail in RAM, looks like a GPU module to the pipeline |
| `inference/pipeline/types.py` | `GenerationRequest`, `GenerationResult`, `PipelineError` — shared, no torch |

Tests replace `app.generator` before the package import and build `inference.app` with a fake pipeline factory, so the HTTP layer is checked without torch. See [tests.md](tests.md).

## Request flow

```
browser → Auth Gateway → qwen-image:8000
                              │
                              ▼
                         routes.py
                              │
         imaging.py, or maskfill.prepare_mask_fill
                              │
                              ▼
                    app/inference.py
                    POST http://qwen-inference:8010/v1/generate
                              │  multipart: prompt, size, steps, seed, PNGs
                              ▼
              inference/app.py → pipeline generator
              under _gpu_lock (one GPU)  ──► event: log ─┐
                              │                        │ (the same stream
                              ▼                        │  the orchestrator reads
                    PNGs in event: done                │  and pushes into logbuf)
                              │                        │
              imaging.save_outputs → PNG in OUTPUT_DIR │
                              │◄───────────────────────┘
    POST /api/generate or POST /api/mask-fill, and a UUID?
                     │ yes
                     ▼
              webstorage.save_generated
              (an archive error does not cancel the 200)
                              │
                              ▼
                         JSON to the client
```

`GET /outputs/<name>` reads a PNG already on disk and does not call the pipeline. `GET /healthz` and `GET /api/config` do not run inference either: they republish the inference container's state. `GET /api/logs` reads the buffer while the request thread waits on the stream.

Customize (`POST /api/customize`) uses the same generator, but the JSON size is the original photo: `prepare_photo` builds the model frame, and `PhotoFrame.restore` returns the PNG to the file's width and height. The archive is not called.

Mask fill (`GET /mask-fill`, `POST /api/mask-fill`) uses the same generator. `GET /mask-fill` does not read identity headers. `prepare_mask_fill` fits the main photo and scales the mask with nearest-neighbour. The pipeline and the archive both receive, in order, the main photo, the mask (white is the area to fill), then the reference photos. The JSON `width` and `height`, and those fields on the archive, are the uploaded photo's size. Save rules match generation: a UUID `X-Auth-User-Id` saves and returns `saved: true`; no UUID sets `storage_error` to `not saved: no user id` and skips Storage; a storage failure is still HTTP 200 with the PNG; `400`, `413`, and `503` do not call Storage.

Generation is serialized by `_gpu_lock` in `QwenImageGenerator`, which now lives in the inference container. Concurrent requests wait on that lock inside `qwen-inference`; the orchestrator simply has more threads blocked on their streams. That is why the inference container runs one gunicorn worker and four threads (one holds the SSE stream, another must answer `/healthz`), and the orchestrator one worker and eight threads (one waits on the stream, the page keeps polling `/api/logs`). Details: [adr/0008-single-worker-gpu-lock.md](adr/0008-single-worker-gpu-lock.md).

V100 memory (int8 DiT, part of the encoder in VRAM, the tail in RAM, VAE tiling as a fallback) lives in `inference/pipeline/generator.py` and `inference/pipeline/offload.py`. `MEMORY_MODE` values: `int8` (default), `fp16`, `offload`. See [adr/0003-v100-memory-and-torch-cu126.md](adr/0003-v100-memory-and-torch-cu126.md).

## Configuration

`Settings` reads `os.environ` when `app.config` (or `inference.config`) is imported. Neither application loads `.env` itself: the file reaches both processes through the container `env_file`, and each reads its own keys.

Variable groups (defaults are in `.env.example`), by the process that reads them:

| Process | Group | Variables |
|---|---|---|
| `qwen-inference` | Model | `MODEL_ID`, `MODEL_REVISION`, `PIPELINE_CLASS`, `HF_HOME` |
| `qwen-inference` | Memory | `MEMORY_MODE`, `DEVICE`, `TEXT_ENCODER_DEVICE`, `TEXT_ENCODER_DTYPE`, `TEXT_ENCODER_GPU_LAYERS`, `TEXT_ENCODER_GPU_VISION`, `ATTENTION_BACKEND`, `VAE_TILING`, `VAE_SLICING`, `VAE_TILE_SIZE`, `PRELOAD_MODEL` |
| `qwen-inference` | Service | `HOST`, `PORT`, `LOG_LEVEL` |
| `qwen-image` | Inference | `INFERENCE_URL`, `INFERENCE_TIMEOUT`, `INFERENCE_HEALTH_TIMEOUT`, `PRELOAD_MODEL` |
| `qwen-image` | Generation | `DEFAULT_STEPS`, `MAX_STEPS`, `DEFAULT_TRUE_CFG_SCALE`, `DEFAULT_NEGATIVE_PROMPT`, `MIN_SIDE`, `MAX_SIDE` |
| `qwen-image` | Input | `MAX_IMAGES`, `MAX_UPLOAD_MB`, `INPUT_MAX_SIDE` |
| `qwen-image` | Service | `HOST`, `PORT`, `OUTPUT_DIR`, `KEEP_OUTPUTS`, `LOG_LEVEL`, `WEBSTORAGE_URL`, `WEBSTORAGE_TIMEOUT` |

Generation limits appear on both sides on purpose: the orchestrator validates the form, the inference container validates the job it was handed.
Only `python wsgi.py` uses `HOST` and `PORT`. Gunicorn binds with its own `--bind`: `0.0.0.0:8000` for the orchestrator, `0.0.0.0:8010` for the pipeline.
Long inference is limited by gunicorn `--timeout 0` and by `INFERENCE_TIMEOUT` on the client; the archive client by `WEBSTORAGE_TIMEOUT`.

The orchestrator does not read `HF_TOKEN`; huggingface_hub picks it up in the inference container when the repository is gated.

## Preload

`PRELOAD_MODEL=true` (the default) means two different, smaller things now.

In `qwen-inference`, `create_app` starts a daemon thread `model-preload` and calls `ensure_loaded()` on the pipeline. A failure is written to the log and to `load_error`; the next attempt is the first job. `/healthz` still answers immediately: `loaded: false` until the weights are in place.

In `qwen-image`, `create_app` probes the inference `/healthz` once in a daemon thread (`InferenceClient.ensure_loaded`). Nothing is loaded there; the line exists so a missing inference container shows up in the log at start instead of at the first user request.

The first start of `qwen-inference` downloads weights onto the `HF_HOME` volume (`/data/huggingface`) and, in `int8` mode, quantizes the DiT. That takes tens of minutes. Ready means `{"loaded": true}` on the inference `/healthz`; the orchestrator reports the same value.

## External systems

**Auth Gateway.** The only entry point for the browser. The gateway checks the `image` role (`FAMILY` or `ADMIN` in User-Service). It strips client `X-Auth-*` headers and substitutes its own. Requests that arrive here are already allowed. On `qwen_image_gen_network` may sit only the gateway, the `qwen-image` container and its pipeline `qwen-inference`: any other member of that network bypasses the role check, and the pipeline answers generation to whoever can reach it. The host port is not published. See [adr/0001-network-trust-unpublished-port.md](adr/0001-network-trust-unpublished-port.md).

**Internal pipeline.** `POST http://qwen-inference:8010/v1/generate`, multipart in, `text/event-stream` out; `GET /healthz` for state. Contract: [inference_api.md](inference_api.md). The pipeline is stateless between jobs; the only state is the loaded model and its GPU lock.

**Hugging Face.** `DiffusionPipeline.from_pretrained(MODEL_ID)` (or the class from `PIPELINE_CLASS`) in `qwen-inference`. The cache is the volume at `/data/huggingface`. The `QwenImage21Pipeline` class is pinned to a diffusers commit; see [adr/0004-diffusers-git-pin.md](adr/0004-diffusers-git-pin.md).

**WebStorage.** After a successful `POST /api/generate` or `POST /api/mask-fill` the orchestrator, on the Auth network, calls the DNS name `app` (`WEBSTORAGE_URL`, default `http://app:8000`). WebStorage is not on `qwen_image_gen_network`. An archive failure does not fail the run. See [adr/0002-dual-networks.md](adr/0002-dual-networks.md) and [adr/0005-webstorage-best-effort.md](adr/0005-webstorage-best-effort.md).

## Deploy

Two images and two containers.

`qwen-inference`: image `localhost/qwen-inference:latest`, container `qwen-inference`, built from `Dockerfile` (CUDA 12.6 + the `ml` group). Listens on 8010, does not publish a host port. Network `qwen_image_gen_network`. Device `nvidia.com/gpu=all`. Volume for the HF cache (`/data/huggingface`). `shm` 8g, memory 64g. Command: gunicorn, 1 worker, 4 threads, `--timeout 0`, `--graceful-timeout 60`, `--access-logfile -`, `inference.wsgi:app`.

`qwen-image`: image `localhost/qwen-image-service:latest`, container `qwen-image`, built from `Dockerfile.web` (python:3.10-slim, no `ml` group). Listens on 8000, does not publish a host port. Networks `qwen_image_gen_network` and `deploy_auth_network`. Volume for outputs (`/data/outputs`), memory 2g. Command: gunicorn, 1 worker, 8 threads, `--timeout 0`, `--graceful-timeout 60`, `--access-logfile -`, `wsgi:app`.

Files: `Dockerfile`, `Dockerfile.web`, `compose.yaml`, `.dockerignore`. uv owns dependencies: `pyproject.toml`, `uv.lock`, Python `>=3.10,<3.12`, `.python-version` `3.10`. The inference image installs the ML group from the frozen lock; the orchestrator image installs only the project dependencies, and neither installs the dev group. See [adr/0006-podman-compose-deploy.md](adr/0006-podman-compose-deploy.md) and [adr/0007-uv-package-manager.md](adr/0007-uv-package-manager.md).
