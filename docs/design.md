# Service design

A Flask process generates images with [Qwen/Qwen-Image-2.1](https://huggingface.co/Qwen/Qwen-Image-2.1) on a Tesla V100 32 GB. The browser opens the Auth Gateway (`https://image.filenkov.store`). This repository is the GPU worker and two HTML forms, with no login and no history.

Field contract: [api-contract.md](api-contract.md). Why the network, memory, diffusers pin, archive, Podman, uv, and a single worker are set up this way: [adr/](adr/).

Application behavior does not change when packaging or deploy changes. The runtime is Podman Compose and uv. The old Quadlet unit is described in the historical stories [user-stories/user-story-behind-auth-gateway.md](user-stories/user-story-behind-auth-gateway.md) and [user-stories/user-story-webstorage-generate.md](user-stories/user-story-webstorage-generate.md).

## Module map

| Module | Role |
|---|---|
| `wsgi.py` | `app = create_app()` for `gunicorn wsgi:app`. `python wsgi.py` listens on `HOST`/`PORT` |
| `app/__init__.py` | Flask factory: body limit, JSON, blueprint, 500 handler, background preload |
| `app/config.py` | Frozen `Settings` from the environment, read once at import |
| `app/routes.py` | Forms and JSON: parse fields, call the generator, write PNGs, call the archive |
| `app/imaging.py` | Presets, file validation, `PhotoFrame` for customize, PNG names, directory prune |
| `app/generator.py` | Load diffusers, memory modes, one `threading.Lock` on the GPU |
| `app/offload.py` | `CpuHostedTextEncoder`: Qwen3-VL tail in RAM, looks like a GPU module to the pipeline |
| `app/webstorage.py` | `POST {WEBSTORAGE_URL}/api/generated` on the stdlib, no SDK |
| `app/logbuf.py` | Ring of 400 lines for `GET /api/logs` |
| `app/templates/`, `app/static/` | Forms `index.html` and `customize.html`, log polling |

Tests replace `app.generator` before the package import, so the HTTP layer is checked without torch. See [tests.md](tests.md).

## Request flow

```
browser → Auth Gateway → qwen-image:8000
                              │
                              ▼
                         routes.py
                              │
              imaging.py: size, files, PhotoFrame
                              │
                              ▼
                    generator.generate()
                    under _gpu_lock (one GPU)
                              │
                              ▼
              imaging.save_outputs → PNG in OUTPUT_DIR
                              │
              POST /api/generate and a UUID?
                     │ yes
                     ▼
              webstorage.save_generated
              (an archive error does not cancel the 200)
                              │
                              ▼
                         JSON to the client
```

`GET /outputs/<name>` reads a PNG already on disk and does not call the generator. `GET /healthz` and `GET /api/config` do not run inference either. `GET /api/logs` reads the buffer while generation occupies another thread.

Customize (`POST /api/customize`) uses the same generator, but the JSON size is the original photo: `prepare_photo` builds the model frame, and `PhotoFrame.restore` returns the PNG to the file's width and height. The archive is not called.

Generation is serialized by `_gpu_lock` in `QwenImageGenerator`. Concurrent HTTP requests wait on that lock. The image therefore runs one gunicorn worker and four threads: the generation thread must not block `/healthz` and `/api/logs`. Details: [adr/0008-single-worker-gpu-lock.md](adr/0008-single-worker-gpu-lock.md).

V100 memory (int8 DiT, part of the encoder in VRAM, the tail in RAM, VAE tiling as a fallback) lives in `generator.py` and `offload.py`. `MEMORY_MODE` values: `int8` (default), `fp16`, `offload`. See [adr/0003-v100-memory-and-torch-cu126.md](adr/0003-v100-memory-and-torch-cu126.md).

## Configuration

`Settings` reads `os.environ` when `app.config` is imported. The application does not load `.env` itself: the file reaches the process through the container `env_file`. `OUTPUT_DIR` and the path from `HF_HOME` are created at startup (`OUTPUT_DIR` via `_path`).

Variable groups (defaults are in `.env.example`):

| Group | Variables |
|---|---|
| Model | `MODEL_ID`, `MODEL_REVISION`, `PIPELINE_CLASS` |
| Memory | `MEMORY_MODE`, `DEVICE`, `TEXT_ENCODER_DEVICE`, `TEXT_ENCODER_DTYPE`, `TEXT_ENCODER_GPU_LAYERS`, `TEXT_ENCODER_GPU_VISION`, `ATTENTION_BACKEND`, `VAE_TILING`, `VAE_SLICING`, `VAE_TILE_SIZE`, `PRELOAD_MODEL` |
| Generation | `DEFAULT_STEPS`, `MAX_STEPS`, `DEFAULT_TRUE_CFG_SCALE`, `DEFAULT_NEGATIVE_PROMPT`, `MIN_SIDE`, `MAX_SIDE` |
| Input | `MAX_IMAGES`, `MAX_UPLOAD_MB`, `INPUT_MAX_SIDE` |
| Service | `HOST`, `PORT`, `OUTPUT_DIR`, `HF_HOME`, `KEEP_OUTPUTS`, `LOG_LEVEL`, `WEBSTORAGE_URL`, `WEBSTORAGE_TIMEOUT` |

Only `python wsgi.py` uses `HOST` and `PORT`. Gunicorn in the image binds `0.0.0.0:8000` with its own `--bind`. `REQUEST_TIMEOUT` is on `Settings` and is not read by the routes. Long inference is limited by gunicorn `--timeout 0`, and the archive client by `WEBSTORAGE_TIMEOUT`.

The application does not read `HF_TOKEN`. huggingface_hub picks it up when the repository is gated.

## Preload

When `PRELOAD_MODEL=true` (the default), `create_app` starts a daemon thread `model-preload` and calls `generator.ensure_loaded()`. A failure is written to the log and to `load_error`; the next attempt is the first `generate`. `/healthz` still answers immediately: `loaded: false` until the weights are in place. Tests set `PRELOAD_MODEL=false` before import so the factory does not pull the model.

The first container start downloads weights onto the `HF_HOME` volume (`/data/huggingface`) and, in `int8` mode, quantizes the DiT. That takes tens of minutes. Ready means `{"loaded": true}` on `/healthz`.

## External systems

**Auth Gateway.** The only entry point for the browser. The gateway checks the `image` role (`FAMILY` or `ADMIN` in User-Service). It strips client `X-Auth-*` headers and substitutes its own. Requests that arrive here are already allowed. `qwen_image_gen_network` must contain only the gateway and the `qwen-image` container: any other member of that network bypasses the role check. The host port is not published. See [adr/0001-network-trust-unpublished-port.md](adr/0001-network-trust-unpublished-port.md).

**Hugging Face.** `DiffusionPipeline.from_pretrained(MODEL_ID)` (or the class from `PIPELINE_CLASS`). The cache is the volume at `/data/huggingface`. The `QwenImage21Pipeline` class is pinned to a diffusers commit; see [adr/0004-diffusers-git-pin.md](adr/0004-diffusers-git-pin.md).

**WebStorage.** After a successful generation the process, on the Auth network, calls the DNS name `app` (`WEBSTORAGE_URL`, default `http://app:8000`). WebStorage is not on `qwen_image_gen_network`. An archive failure does not fail generation. See [adr/0002-dual-networks.md](adr/0002-dual-networks.md) and [adr/0005-webstorage-best-effort.md](adr/0005-webstorage-best-effort.md).

## Deploy

Image `qwen-image-service:latest`, container name `qwen-image`. Listens on 8000 and does not publish a host port. Networks: `qwen_image_gen_network` and `deploy_auth_network`. Device `nvidia.com/gpu=all`. Volumes for the HF cache (`/data/huggingface`) and outputs (`/data/outputs`). `shm` 8g, memory 64g. Command: gunicorn, 1 worker, 4 threads, `--timeout 0`, `--graceful-timeout 60`, `--access-logfile -`, `wsgi:app`.

Files: `Dockerfile`, `compose.yaml`, `.dockerignore`. uv owns dependencies: `pyproject.toml`, `uv.lock`, Python `>=3.10,<3.12`, `.python-version` `3.10`. The image installs the ML group from the frozen lock and does not install the dev group. See [adr/0006-podman-compose-deploy.md](adr/0006-podman-compose-deploy.md) and [adr/0007-uv-package-manager.md](adr/0007-uv-package-manager.md).
