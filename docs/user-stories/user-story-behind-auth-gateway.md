# Change Request: Sit behind the Auth Gateway on `qwen_image_gen_network`

> **Status:** Implemented in README and run docs (see §6). Routes unchanged; one error-handler fix in `app/__init__.py`.
> **Repos:** qwen-image-service only (run topology and docs). No auth code in the Flask app.
> **Caller:** Auth Gateway, from Auth-Service [`docs/user-story-image-host.md`](../../../Auth-Service/docs/user-story-image-host.md).
> **Public URL (not this process):** `https://image.filenkov.store`.

**Actor:** The household member who already passed the gateway. This service does not decide that.

**Goal:** Run the existing container on network `qwen_image_gen_network` as `qwen-image`, unpublished, so the gateway can proxy `http://qwen-image:8000`. Leave generation, the UI, and `/healthz` as they are.

**Value:** The GPU service does not grow a login, a role table, or a copy of `JWT_SECRET`. Who may open it is the gateway's `image` role check (`FAMILY` or `ADMIN` in User-Service).

---

## 1. Background

The app is one Flask/gunicorn process. `GET /` is the form, `POST /api/generate` builds an image, `GET /healthz` reports whether the model is loaded, `GET /outputs/<name>` serves a result. Gunicorn is `--workers 1` because one GPU runs one job. A frame can take minutes. `gunicorn` already uses `--timeout 0`.

The README's `podman run` publishes `-p 8000:8000`. That would put the GPU UI on the host network, beside the gateway. This request removes that publication.

This service does not read `X-Auth-*`, cookies, or JWTs. A later change is the exception: `POST /api/generate` reads `X-Auth-User-Id` and `X-Auth-Email` only to save the run to WebStorage (`docs/user-stories/user-story-webstorage-generate.md`). The gateway strips client-supplied `X-Auth-*` and only proxies `FAMILY` and `ADMIN` on the `image` role. A request that arrives here is already allowed.

## 2. Requested run

1. Create the network once: `podman network create qwen_image_gen_network`.
2. Container name **`qwen-image`** (the gateway upstream is `http://qwen-image:8000`).
3. Attach `--network qwen_image_gen_network`.
4. Do not publish port 8000 on a host interface. No `-p 8000:8000`.
5. Keep the GPU device, the Hugging Face cache volume, the outputs volume, `--shm-size`, and `--memory` as in the README.

The Auth gateway joins `qwen_image_gen_network` from its own compose (`external: true`). This repo creates the network. The Auth repo does not.

`GET /healthz` stays anonymous on the app. The gateway exposes that path without a role so an operator can probe it, and requires the `image` role for everything else.

## 3. Out of scope

- No login page, no session cookie, no role check, no call to User-Service.
- No rename of routes. `/healthz` stays `/healthz` (the gateway matches that path).
- No change to the model, `MEMORY_MODE`, or the generate API.
- No Caddy site and no gateway YAML. Those live in Auth-Service.

## 4. Acceptance

- [x] README's run command uses `--name qwen-image`, `--network qwen_image_gen_network`, and does not publish `8000`.
- [x] README states that browsers use `https://image.filenkov.store`, that this container is only reachable on `qwen_image_gen_network`, and that allow/deny is the Auth Gateway.
- [x] From a container on `qwen_image_gen_network`, `curl -s http://qwen-image:8000/healthz` answers. From the host, nothing listens on public `:8000`.
- [x] App routes and tests are unchanged.

## 5. What to change

| Area | Change |
| --- | --- |
| `README.md` | Network, container name, unpublished port, public URL |
| `app/` | No change |

## 6. Implementation notes

| Area | Done |
| --- | --- |
| `README.md` | New "Network and access" section: public URL, network-only reachability, gateway decides allow/deny, `/healthz` anonymous, warning against `-p 8000:8000`. Step 4 adds `podman network create qwen_image_gen_network`. The run command has `--name qwen-image --network qwen_image_gen_network` and no `-p`; GPU, volumes, `--shm-size` and `--memory` are unchanged. Readiness is checked from inside the network (`curlimages/curl` → `http://qwen-image:8000/healthz`) or with `podman exec`. `podman port qwen-image` and `ss -ltn \| grep :8000` confirm nothing listens on the host. The API example runs from inside the network. Systemd note: the network must exist before the unit starts. New "Tests" section. |
| `.env.example` | Comment on `HOST`/`PORT`: in-container address only, the port is not published. |
| `.containerignore` | Excludes `tests/`, `requirements-dev.txt`, `pytest.ini`, `docs/` from the build context. |
| `tests/`, `requirements-dev.txt`, `pytest.ini` | New pytest suite with the generator stubbed, so it needs no GPU, torch or diffusers. |
| `app/__init__.py` | The catch-all `errorhandler(Exception)` returns werkzeug `HTTPException`s unchanged, so unknown paths get 404 and wrong methods get 405 instead of 500. Routes, auth behaviour and other error handling are unchanged. |
| other `app/` files, `Containerfile` | No change. |

How each acceptance item is checked:

- **Run command:** `tests/test_deploy_docs.py` parses the README `podman run` line. It checks `--name`, `--network`, no `-p`/`--publish`/`-P`, and that the GPU, volume and memory flags are kept.
- **Gateway wording:** `tests/test_deploy_docs.py` asserts the public URL, network name, `Auth Gateway` and the upstream URL appear in the README.
- **Reachability:** `tests/test_podman_network.py` (opt-in, `RUN_PODMAN_TESTS=1 .venv/bin/pytest -m podman`). It starts the image on a temporary network with alias `qwen-image` and no GPU, then curls `http://qwen-image:8000/healthz` from a second container on that network. It asserts `podman port` is empty and there are no host port bindings. It never touches the real `qwen_image_gen_network`. The Containerfile test checks that gunicorn binds `0.0.0.0:8000` inside the container.
- **Routes unchanged:** `tests/test_routes.py` pins the URL map and covers each route (validation 400, pipeline error 503, output 404, path traversal). There were no tests before, so none changed.
- **Trust boundary:** `tests/test_no_auth_in_app.py`. Responses are identical with and without spoofed `X-Auth-*`, `Authorization` and session cookies. No `Set-Cookie` is sent, there are no login routes, and a source scan finds no header, cookie or JWT handling in `app/` and no auth libraries in the requirements.

Deviations and open items:

- The trust boundary is the network itself. Only the gateway and `qwen-image` may join `qwen_image_gen_network`, and the README says so.
- **Deviation from «`app/` no change» (approved):** the new tests found an existing bug. `@app.errorhandler(Exception)` also caught Flask's `HTTPException`, so unknown paths and wrong methods (e.g. `/login`, `GET /api/generate`) answered 500 instead of 404/405. The handler now returns `HTTPException` unchanged. Routes and the no-auth guarantee are unaffected. `tests/test_routes.py` covers 404, 405, the JSON 413 and the JSON 500 for other exceptions, and `tests/test_no_auth_in_app.py` asserts auth-looking paths are 404.
