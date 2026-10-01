# ADR 0007. uv owns dependencies

## Status

Accepted. Dependencies are described in `pyproject.toml` and `uv.lock`. `.python-version` is `3.10`. `requirements.txt`, `requirements-ml.txt`, and `requirements-dev.txt` are gone.

## Context

Dependencies used to live in three pip files, and the image installed torch with a separate command. Local tests must not pull torch, and the image must not take torch from PyPI: cu126 wheels and the default index are easy to mix up.

The image and a laptop must share one lock. Python in the CUDA 12.6 / Ubuntu 22.04 base image is 3.10. The torch 2.7.1+cu126 wheels target that line. The upper bound `<3.12` stops local uv from moving to an interpreter the `ml` group was not built for.

## Decision

One `pyproject.toml` and `uv.lock`. `.python-version` is `3.10`. Interpreter requirement: `>=3.10,<3.12`.

Groups:

| Group | Contents | Who installs it |
|---|---|---|
| project (runtime) | web: Flask, gunicorn, and what HTTP needs | local `uv sync` and the image |
| `dev` | pytest, Pillow | local `uv sync` only |
| `ml` | torch `2.7.1`, torchvision `0.22.1` from `https://download.pytorch.org/whl/cu126`, diffusers per ADR 0004, transformers, accelerate, optimum-quanto, and the rest of the former `requirements-ml.txt` | the image only |

`uv sync` installs runtime and `dev` and does not install `ml`.

The image runs:

```bash
uv sync --frozen --no-dev --group ml --no-install-project
```

`--frozen` does not rewrite the lock during the build. `--no-dev` does not pull pytest. `--group ml` adds the GPU stack. `--no-install-project` does not install the directory as a package: gunicorn runs the code from `WORKDIR`, as before (`wsgi:app`).

Pillow in the dev group is what `app/imaging.py` needs under the generator stub. In the image, Pillow comes with the `ml` group.

## Consequences

- A local `uv run pytest` does not download torch and does not need a GPU.
- Changing the diffusers commit or the torch version without updating `uv.lock` fails the image build (`--frozen`).
- `tests/test_no_auth_in_app.py` looks for JWT libraries in `[project].dependencies` and in the `ml` group of `pyproject.toml`. The ban (`pyjwt`, `flask-login`, and neighboring names) stays.
- The build cache is a mount at `/root/.cache/uv`. `podman build --no-cache` does not drop that mount, so torch wheels are not downloaded again without a reason.
