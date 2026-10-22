# Tests

The suite in `tests/` runs under pytest and, by default, needs no GPU, no network and no models. It locks the HTTP contract, the trust boundary, image handling, the orchestrator's sequencing, and the deploy invariants. Real inference is out of scope — that is `../Common/models/scripts/parity.sh`.

`pytest.ini`: `testpaths = tests`, `pythonpath = .`, marker `podman` for the container smoke test.

## Layout

| File | What it holds |
|---|---|
| `tests/conftest.py` | Temporary `OUTPUT_DIR`, `PRELOAD_MODEL=false`, the `generator` stub, Flask client |
| `tests/test_routes.py` | URL map, generate, customize, errors, archive, `/healthz` shape |
| `tests/test_split_pipeline.py` | The real `SplitPipeline` against a faked transport: hop order, payloads, `img_shapes`, error mapping, stage probing |
| `tests/test_geometry_contract.py` | The resize / latent grid / `img_shapes` numbers the stages pin too |
| `tests/test_no_auth_in_app.py` | No login; `X-Auth-User-Id` and `X-Auth-Email` are read only in `api_generate` and `api_mask_fill`, to sign the WebStorage save |
| `tests/test_imaging.py` | `fit_model_size` and `PhotoFrame` |
| `tests/test_generate_form.py` | The form shows `storage_error` only when `saved` is false |
| `tests/test_deploy_docs.py` | No published 8000, networks, container name and alias, healthcheck, gunicorn, the stage-contract link |
| `tests/test_podman_network.py` | Podman network smoke, only with `RUN_PODMAN_TESTS=1` |

## The generator stub

`app.generator` no longer imports torch — the models live in the inference services — so the module is imported for real. `conftest.py` replaces only the `generator` object, in both namespaces that hold a reference: `app.generator` (the factory's background probe) and `app.routes` (the blueprint). `FakeGenerator.generate` records the request and returns a tiny RGB image. `PRELOAD_MODEL=false` and a temporary `OUTPUT_DIR` are set before import, because `Settings` reads the environment once.

Duck typing is enough: the fake accepts a real `GenerationRequest` and returns a real `GenerationResult`, because `conftest.py` re-exports those classes from `app.generator` instead of defining its own.

The `app` fixture builds the app with `TESTING` and, after each test, checks that no response was 401, 403, or a redirect to login. That keeps the "auth is outside" boundary from drifting.

`uv run pytest` installs the web runtime (Flask, gunicorn, Pillow, requests) and the dev group (pytest); the `ml` group is not installed.

## Commands

```bash
uv sync
uv run pytest
```

Container smoke (needs a local image and `podman`):

```bash
RUN_PODMAN_TESTS=1 uv run pytest -m podman
```

The marker skips the test when the variable is not `1` or the image is missing. No GPU: the container starts with `PRELOAD_MODEL=false` and `HF_HUB_OFFLINE=1`, and the check is `GET /healthz` — which in this service probes the three stages, so `loaded` is `false` and each stage reports its own `load_error` (connection refused). That is the expected state without the stages running.

## What `test_deploy_docs.py` guards

The file reads the README, `Dockerfile`, and `compose.yaml`. It does not start a container. Invariants it keeps:

- No published 8000: no `-p 8000:8000`, no `ports: 8000:8000`, no `localhost:8000` from the host (`podman exec … localhost:8000` is allowed). `compose.yaml` publishes only `127.0.0.1:18000:8000`.
- `compose.yaml` describes the orchestrator only: image `localhost/qwen-image-service:latest`, container name `qwen-image-split`, network alias `qwen-image` on `models_network`, plus `deploy_auth_network`. No `devices:`, no `nvidia.com/gpu=all`, no weights volume — the models are not here.
- The three inference stages do not appear in this compose file.
- The README states the start order (base image, then the stages from `../Common/models`, then `podman compose up -d`), the public URL, `Auth Gateway`, `models_network`, upstream `http://qwen-image:8000`, `X-Auth-User-Id`, the trust-boundary sentence, and links to `docs/models_inference_api.md` and ADR 0009.
- `Dockerfile`: exactly one `CMD`, gunicorn, `--bind 0.0.0.0:8000`, `--workers 1`, `--timeout 0`, argument `wsgi:app`. Install is `uv sync --frozen --no-dev --group ml --no-install-project`, with no `pip install`. The file names `localhost/qwen-image-service:latest` and `Common/models`, because it is their base image.
- `HEALTHCHECK` calls `curl` on `:8000/healthz`.
- `Containerfile` and `deploy/qwen-image.container` are absent. The README documents `uv sync` and `uv run pytest` and links to `docs/`.

`--threads 8` is not asserted on the gunicorn command. See [adr/0006-podman-compose-deploy.md](adr/0006-podman-compose-deploy.md).

`test_podman_network.py` starts a temporary Podman network and container with alias `qwen-image`, without a GPU and without a published port, and from another container on that network requests `http://qwen-image:8000/healthz`. It does not touch the real `models_network` or the `qwen-image-split` container. `podman port` and `PortBindings` must be empty.

## Not covered

- Real inference, quantization, VRAM, and image quality: `../Common/models/scripts/parity.sh` (diffusers' own pipeline as the reference) and `scripts/smoke.sh` (a real 512² generation).
- The stage maths: `../Common/models/tests/` plus the parity harness.
- CI. The repository has no workflow that runs pytest.
- Image build and `podman compose config`. The network smoke is skipped until `RUN_PODMAN_TESTS=1` and a local image exist.
