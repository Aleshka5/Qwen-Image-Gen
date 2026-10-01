# ADR 0001. Trust is the network; the host port is not published

## Status

Accepted

## Context

The service is one Flask/gunicorn process with a form, `POST /api/generate`, and `GET /healthz`. A frame on the V100 takes minutes. Browsers do not open this process. They open the Auth Gateway at `https://image.filenkov.store`. The gateway proxies to `http://qwen-image:8000` and allows the `image` role (`FAMILY` or `ADMIN` in User-Service). The gateway serves `GET /healthz` without a role so an operator can probe it; the application also leaves that path unchecked.

`app/` has no login, session, JWT check, or call to User-Service. `tests/test_no_auth_in_app.py` compares responses with spoofed `Authorization`, cookie, and `X-Auth-*` headers: aside from archiving a generation, they change nothing. The exception is reading `X-Auth-User-Id` and `X-Auth-Email` on `POST /api/generate` (ADR 0005). The gateway strips client-supplied `X-Auth-*` before proxying. A request that reached the container is already allowed.

Publishing `-p 8000:8000` would put the GPU form on a host interface next to the gateway, and the role check would be bypassed.

## Decision

The `qwen-image` container is reachable only on `qwen_image_gen_network`. Port 8000 is not published on the host: no `-p`, and no `ports:` in compose. Inside the container, gunicorn listens on `0.0.0.0:8000`.

Only the gateway and `qwen-image` are allowed on `qwen_image_gen_network`. This repository creates the network; the Auth stack attaches to it as external. WebStorage is not on `qwen_image_gen_network`.

`/healthz` stays anonymous in the application and does not load the model.

## Consequences

- Any container admitted to `qwen_image_gen_network` can bypass the role check. That is an operational rule, not a check in Flask.
- A probe from the host to public `:8000` finds nothing listening. Readiness is checked from the network (`http://qwen-image:8000/healthz`) or from inside the container.
- `tests/test_deploy_docs.py` forbids publishing a port in README commands and in `compose.yaml`. `tests/test_podman_network.py` (flag `RUN_PODMAN_TESTS=1`, marker `podman`) checks that `podman port` is empty.
- The historical write-up of the move off a published port is `docs/user-stories/user-story-behind-auth-gateway.md`.
