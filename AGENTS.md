Rules:
1) Use Podman and compose to deploy: `podman build -f Dockerfile` and `podman compose`. Do not publish the service port (no `-p`, no `ports:` in `compose.yaml`).
2) Code style: Clean Architecture, Do not repeat yourself, Keep it simple
3) Dependencies live in `pyproject.toml` and `uv.lock`. Do not add pip requirements files. Default `uv sync` stays without the `ml` group.
4) This service does not authenticate. Trust the Auth Gateway. Do not add JWT, sessions, or login.
5) Default tests must pass without a GPU. The Podman network smoke stays opt-in (`RUN_PODMAN_TESTS=1`).
6) All documentation is in English. User stories live in `docs/user-stories/`.
