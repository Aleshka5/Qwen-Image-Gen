# Tests

The suite in `tests/` runs under pytest and, by default, needs no GPU, torch, diffusers, or network. It locks the HTTP contract, the trust boundary, image handling, and deploy invariants. Real inference is out of scope.

`pytest.ini`: `testpaths = tests`, `pythonpath = .`, marker `podman` for the container smoke test.

## Layout

| File | What it holds |
|---|---|
| `tests/conftest.py` | Stub for `app.generator`, output directory, Flask client |
| `tests/test_routes.py` | URL map, generate, customize, errors, archive |
| `tests/test_no_auth_in_app.py` | No login; `X-Auth-User-Id` and `X-Auth-Email` are read only in `api_generate` and `api_mask_fill`, to sign the WebStorage save |
| `tests/test_imaging.py` | `fit_model_size` and `PhotoFrame` |
| `tests/test_generate_form.py` | The form shows `storage_error` only when `saved` is false |
| `tests/test_deploy_docs.py` | No published port, two networks, healthcheck, gunicorn |
| `tests/test_podman_network.py` | Podman network smoke, only with `RUN_PODMAN_TESTS=1` |

## Generator stub

Before `import app`, `conftest.py` puts a stub module in `sys.modules["app.generator"]` with the same `GenerationRequest`, `GenerationResult`, `PipelineError`, and `generator` object. `FakeGenerator.generate` records the request and returns a tiny RGB image without torch. `PRELOAD_MODEL=false` and a temporary `OUTPUT_DIR` are set before import, because `Settings` reads the environment once.

The `app` fixture builds the app with `TESTING` and, after each test, checks that no response was 401, 403, or a redirect to login. That keeps the "auth is outside" boundary from drifting.

`uv run pytest` therefore installs the web runtime and the dev group (pytest, Pillow) and does **not** install the `ml` group. The `app.generator` import in tests is the stub. `app/imaging.py` and the forms need only Pillow.

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

The file reads the README, `Dockerfile`, and `compose.yaml`. It does not start a container. Invariants it keeps:

- The service start has no `-p`, `--publish`, `-P`, or `8000:8000`. README commands do not publish 8000 and do not use `localhost:8000` from the host (`podman exec … localhost:8000` is allowed).
- The container is named `qwen-image` and sits on two networks: `qwen_image_gen_network` and `deploy_auth_network` (in `podman run` the second network may be `${AUTH_DOCKER_NETWORK:-deploy_auth_network}`). `compose.yaml` has no `ports` section.
- GPU `nvidia.com/gpu=all`, volumes `qwen-hf-cache:/data/huggingface` and `qwen-outputs:/data/outputs`, `shm_size` / `--shm-size`, and `mem_limit` / `--memory` stay.
- The README has the public URL, the network name, `Auth Gateway`, upstream `http://qwen-image:8000`, `X-Auth-User-Id`, and the sentence that WebStorage is not on `qwen_image_gen_network`.
- `Dockerfile`: exactly one `CMD`, gunicorn, `--bind 0.0.0.0:8000`, `--workers 1`, `--timeout 0`, argument `wsgi:app`. Install is `uv sync --frozen --no-dev --group ml --no-install-project`, with no `pip install`.
- `HEALTHCHECK` calls `curl` on `:8000/healthz`.
- `Containerfile` and `deploy/qwen-image.container` are absent. The README documents `uv sync` and `uv run pytest` and links to `docs/`.

`--threads 4` is not asserted on the gunicorn command. See [adr/0006-podman-compose-deploy.md](adr/0006-podman-compose-deploy.md).

`test_podman_network.py` starts a temporary Podman network and container with alias `qwen-image`, without a GPU and without a published port, and from another container on that network requests `http://qwen-image:8000/healthz`. It does not touch the real `qwen_image_gen_network` or the `qwen-image` container. `podman port` and `PortBindings` must be empty.

## Not covered

- Real inference, quantization, VRAM, and image quality. The stub does not call `QwenImageGenerator`.
- Unit tests for `app/offload.py` and the load paths in `app/generator.py` (attention, VAE tiling, parking the DiT).
- CI. The repository has no workflow that runs pytest.
- Image build and `podman compose config`. The network smoke is skipped until `RUN_PODMAN_TESTS=1` and a local image exist.
