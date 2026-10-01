# ADR 0006. Deploy with Podman Compose

## Status

Accepted. Deploy is described in `Dockerfile`, `compose.yaml`, and `.dockerignore`. Application behavior does not change.

## Context

The service is a container with no published port, on two networks, with a GPU, two volumes, and memory limits (ADR 0001, ADR 0002, ADR 0003). Historically that was `podman build -f Containerfile`, `podman run`, and the Quadlet unit `deploy/qwen-image.container`.

Quadlet and a long `podman run` diverge from the Auth stack, which already lives in Compose. One `compose.yaml` holds both networks, the GPU, and the limits. The runtime is Podman: `podman build -f Dockerfile` and `podman compose`. The GPU device is NVIDIA CDI `nvidia.com/gpu=all`. The image file stays `Dockerfile`: Podman builds it directly.

`.dockerignore` does not copy `.env`, `tests/`, `docs/`, `README.md`, or `.venv/` into the build context. Secrets must not enter the image.

## Decision

| Was | Is |
|---|---|
| `Containerfile` | `Dockerfile` |
| `deploy/qwen-image.container` | `compose.yaml` |
| `.containerignore` | `.dockerignore` |

Invariants compose must keep:

- Image `qwen-image-service:latest`, container name `qwen-image`.
- The host port is not published. The process listens on 8000.
- Networks `qwen_image_gen_network` and `deploy_auth_network`. This project creates the first. The second is external: the Auth stack brings it up, and this repository does not create it.
- Device `nvidia.com/gpu=all`.
- Volumes for the Hugging Face cache at `/data/huggingface` and results at `/data/outputs`.
- shm 8g, memory 64g.
- Gunicorn command: `--bind 0.0.0.0:8000`, `--workers 1`, `--threads 4`, `--timeout 0`, `--graceful-timeout 60`, `--access-logfile -`, `wsgi:app`.
- Healthcheck: `curl` against `http://localhost:8000/healthz`.
- The user in the image is not root (`uid` 1000). `/data/huggingface` and `/data/outputs` belong to that user.
- `WEBSTORAGE_URL` comes from the env file. It is not hardcoded a second way in compose.

The image installs dependencies as ADR 0007 describes: `uv sync --frozen --no-dev --group ml --no-install-project`.

Quadlet is no longer how the service starts. Operator commands are `podman` and `podman compose`. Documents in `docs/user-stories/` stay as history and are not rewritten to match Compose.

## Consequences

- `tests/test_deploy_docs.py` reads the README, `Dockerfile`, and `compose.yaml`, and requires that `Containerfile` and the Quadlet unit are absent. Invariants: no port, two networks, healthcheck, gunicorn, install via `uv sync --frozen`.
- `tests/test_podman_network.py` calls the `podman` binary. The test is skipped by default. Explicit smoke: `RUN_PODMAN_TESTS=1 uv run pytest -m podman`. The default pytest needs neither a GPU nor a daemon.
- The operator no longer copies a unit into `~/.config/containers/systemd`.
