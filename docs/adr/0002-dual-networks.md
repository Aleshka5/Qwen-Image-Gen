# ADR 0002. Two networks: gateway and WebStorage

## Status

Accepted

## Context

After a successful generation the process writes the run to WebStorage: `POST {WEBSTORAGE_URL}/api/generated`. In the Auth stack, WebStorage is the DNS name `app` and listens on 8000 on its own network. With only `qwen_image_gen_network`, this container cannot resolve `app`.

WebStorage must not join `qwen_image_gen_network`: from there it could call generation without the `image` role. This container must not be renamed to `app`: on the Auth network that name is already WebStorage, and the gateway looks up the upstream `qwen-image`.

This repository does not create the Auth network. When Auth is started from `Auth-Service/deploy` (compose project `deploy`, network `auth_network`), that network is named `deploy_auth_network`.

## Decision

The `qwen-image` container attaches to two networks:

- `qwen_image_gen_network` — the gateway reaches it here;
- `deploy_auth_network` — from here the process calls `http://app:8000`.

`WEBSTORAGE_URL` defaults to `http://app:8000`. The host port is still not published (ADR 0001). The container name on both networks is `qwen-image`.

## Consequences

- The Auth stack must be up first. Otherwise `deploy_auth_network` does not exist and `app` does not resolve. Generation still works; the archive answers `saved: false` (ADR 0005).
- `qwen_image_gen_network` is still only the gateway and `qwen-image`.
- `tests/test_deploy_docs.py` requires both networks in the run command and in `compose.yaml`, and checks the sentence that WebStorage is not on the generation network.
- The historical write-up is `docs/user-stories/user-story-webstorage-generate.md`.
