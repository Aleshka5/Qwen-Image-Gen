Rules:
1) Use Podman and compose to deploy: `podman build -f Dockerfile` and `podman compose`. Never publish port 8000 (`-p 8000:8000`, `ports: 8000:8000`). The only published port is `127.0.0.1:18000`, loopback only, for health probes.
2) Code style: Clean Architecture, Do not repeat yourself, Keep it simple
3) Dependencies live in `pyproject.toml` and `uv.lock`. Do not add pip requirements files. Default `uv sync` stays without the `ml` group.
4) This service does not authenticate. Trust the Auth Gateway. Do not add JWT, sessions, or login.
5) Default tests must pass without a GPU. The Podman network smoke stays opt-in (`RUN_PODMAN_TESTS=1`).
6) All documentation is in English. User stories live in `docs/user-stories/`.
7) The models are not in this repository. Three inference services (embedding, DiT, VAE) live in `../Common/models` and this process only calls them (ADR 0009, `docs/models_inference_api.md`). Do not add torch, diffusers, or model loading here; a change to a stage's maths belongs in that repository.
8) The image built from this Dockerfile is also the base the inference services are built FROM (`app` stage). Keep it buildable with a plain `podman build -f Dockerfile .`, with no extra build arguments.
9) `app/geometry.py` is a twin of `shared/qwensplit/geometry.py` in Common/models. Change both together; the contract tests in both repositories and `parity.sh` catch drift.
