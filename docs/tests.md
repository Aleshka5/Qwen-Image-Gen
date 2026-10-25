# Tests

The suite in `tests/` runs under pytest and, by default, needs no GPU, torch, diffusers, or network. It locks the HTTP contract, the trust boundary, image handling, and deploy invariants. Real inference is out of scope.

`pytest.ini`: `testpaths = tests`, `pythonpath = .`, marker `podman` for the container smoke test.

## Layout

| File | What it holds |
|---|---|
| `tests/conftest.py` | Stub for `app.generator`, output directory, Flask client |
| `tests/test_workspace.py` | Projects, shapes, queue, idempotency, render, secrets, feed replay fields, and task removal when a project or part is deleted, on the memory store |
| `tests/test_backends.py` | The queue's invariants without a broker: publish holds the connection lock, an intake failure fails the task, a restart fails orphaned `running` tasks |
| `tests/test_account_header.py` | The gateway name in the header, and no name when the header is missing |
| `tests/test_maskfill.py` | Mask pairing: the fitted photo and the nearest-neighbour mask |
| `tests/test_openrouter.py` | OpenRouter: catalog entry, sealed key, the chat-completions body, a reply without images |
| `tests/test_no_auth_in_app.py` | No login; identity headers are read only in `_caller` and `_account_name` |
| `tests/test_imaging.py` | `fit_model_size` and `PhotoFrame` |
| `tests/test_inference_app.py` | `inference/app.py` SSE contract with a fake pipeline: order of events, errors, keepalive, PNG encoding |
| `tests/test_inference_client.py` | `app/inference.py` against a real SSE server on `127.0.0.1`: multipart out, log lines into the buffer, PNGs from `done` |
| `tests/test_deploy_docs.py` | No published port; web plus two workers; `common_network`; healthchecks; gunicorn |
| `tests/test_podman_network.py` | Podman network smoke, only with `RUN_PODMAN_TESTS=1` |

## Pipeline stub

Before `import app`, `conftest.py` puts a stub module in `sys.modules["app.generator"]` with the same `GenerationRequest`, `GenerationResult`, `PipelineError`, and `generator` object. `FakeGenerator.generate` records the request and returns a tiny RGB image without torch. `PRELOAD_MODEL=false` and a temporary `OUTPUT_DIR` are set before import, because `Settings` reads the environment once.

The `app` fixture builds the app with `TESTING` and, after each test, checks that no response was 401, 403, or a redirect to login. That keeps the "auth is outside" boundary from drifting.

`inference/app.py` never imports torch at module level: the pipeline class is imported on the first request (or by the preload thread). `create_app(generator_factory=...)` therefore takes a fake pipeline in tests, and `tests/test_inference_app.py` drives the real SSE layer — including keepalive and error events — with no GPU.

`tests/test_inference_client.py` does not mock `urlopen`: it starts a `ThreadingHTTPServer` that answers `POST /v1/generate` with a real `text/event-stream`, so the client's parsing, the ring buffer and the PNG decoding are exercised end to end.

`uv run pytest` therefore installs the web runtime and the dev group (pytest, Pillow) and does **not** install the `ml` group. The `app.generator` import in tests is the stub; `inference.pipeline.generator` is never imported. `app/imaging.py` and the forms need only Pillow.

## Commands

```bash
uv sync
uv run pytest
```

`uv sync` installs project dependencies and the dev group. The `ml` group (torch, diffusers, quanto) is not in that environment. Container smoke:

```bash
RUN_PODMAN_TESTS=1 uv run pytest -m podman
```

This needs a local image `qwen-image-service:latest` and `podman`. The marker skips the test when the variable is not `1` or the image is missing. No GPU: the container starts with `PRELOAD_MODEL=false` and `HF_HUB_OFFLINE=1`, and the check is `GET /healthz` with `loaded: false`.

## What `test_deploy_docs.py` guards

The file reads the README, `Dockerfile`, `Dockerfile.web`, and `compose.yaml`. It does not start a container. Invariants it keeps:

- The service start has no `-p`, `--publish`, `-P`, `8000:8000`, or `8010:8010`. README commands do not publish 8000 and do not use `localhost:8000` from the host (`podman exec … localhost:8000` is allowed).
- Three processes from the web image: `qwen-image` (outputs volume, `deploy_auth_network` + `models_network` + `common_network`), `qimg-processor` (`common_network` only), `qimg-orchestrator` (`common_network` + `models_network`). The model container is a separate project. `compose.yaml` has no `ports` section. In `podman run` the auth network may be `${AUTH_DOCKER_NETWORK:-deploy_auth_network}`.
- The GPU, the weight cache and `shm_size`/`--shm-size` stay on the inference container and never appear on the orchestrator.
- The README has the public URL, `Auth Gateway`, `common_network`, upstream `http://qwen-image:8000`, the internal `http://qwen-inference:8010`, `text/event-stream`, `X-Auth-User-Id`, `MONGO_URL`, and the bucket `qimg-images`. WebStorage is no longer the archive.
- `Dockerfile`: exactly one `CMD`, gunicorn, `--bind 0.0.0.0:8010`, `--workers 1`, `--timeout 0`, argument `inference.wsgi:app`; `HEALTHCHECK` curls `:8010/healthz`; install is `uv sync --frozen --no-dev --group ml --no-install-project`, with no `pip install`.
- `Dockerfile.web`: exactly one `CMD`, gunicorn, `--bind 0.0.0.0:8000`, `--workers 1`, `--timeout 0`, argument `wsgi:app`; `HEALTHCHECK` curls `:8000/healthz`; install is `uv sync --frozen --no-dev --no-install-project` — the `ml` group must not be there. The image copies `app/` and `qimg/`. Workers override the command and disable the healthcheck in compose.
- `Containerfile` and `deploy/qwen-image.container` are absent. The README documents `uv sync` and `uv run pytest` and links to `docs/`.

Thread counts are asserted only as “more than one” on the inference side: the split needs a thread for the stream and a thread for `/healthz`. See [adr/0006-podman-compose-deploy.md](adr/0006-podman-compose-deploy.md) and [adr/0011-split-inference-process.md](adr/0011-split-inference-process.md).

`test_podman_network.py` starts a temporary Podman network and container with alias `qwen-image`, without a GPU and without a published port, and from another container on that network requests `http://qwen-image:8000/healthz`. It does not touch the real `models_network` or the `qwen-image` container. `podman port` and `PortBindings` must be empty.

## Not covered

- Real inference, quantization, VRAM, and image quality. The stub does not call `QwenImageGenerator`.
- Unit tests for `inference/pipeline/offload.py` and the load paths in `inference/pipeline/generator.py` (attention, VAE tiling, parking the DiT).
- CI. The repository has no workflow that runs pytest.
- Image build and `podman compose config`. The network smoke is skipped until `RUN_PODMAN_TESTS=1` and a local image exist.
- A live Mongo, MinIO, or RabbitMQ. `tests/test_backends.py` covers the queue's locking and failure rules with fakes, not the drivers.
