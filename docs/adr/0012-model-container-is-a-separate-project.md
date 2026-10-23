# 0012 — The model container leaves this compose: two projects, two networks

Status: accepted (2026-10-23)

Supersedes part of [0006](0006-podman-compose-deploy.md) (one compose file for both containers) and of
[0001](0001-network-trust-unpublished-port.md) / [0002](0002-dual-networks.md) (the network names).

## Context

ADR 0011 split the process in two and kept both containers in one `compose.yaml` on one network,
`qwen_image_gen_network`. That made them look like one deployment, and it produced two real problems:

1. **One file owned two very different things.** The 6.2 GB torch image with the card and 33 GiB of weights,
   and the 212 MB Flask image, were started, stopped and rebuilt together. Restarting the UI reloaded nothing,
   but `podman compose up -d` on the stack recreated the model too, losing the warm weights.
2. **The network was neither of theirs.** `qwen_image_gen_network` had to hold the gateway (unpublished port,
   role check) *and* the pipeline. On master-node the pipeline is a model that the switch-service admission-controls
   in `models_network`, so it ended up outside that network while the web face sat inside it — the web face could
   not resolve `qwen-inference` at all (`/healthz` → `name resolution`, every generation `503`).

## Decision

Two projects, two networks, one HTTP call between them.

* **`qwen-inference`** — the model: torch 2.7.1+cu126, the pipeline, `qwen-hf-cache`, `nvidia.com/gpu=all`.
  Launch code: `../Common/models/services/inference/` (own `compose.yaml`, own `env.example`, own `run.sh`),
  build context still this repository's `Dockerfile`. Network: **`models_network` only**.
  On master-node it is started by Switch-Service as service `image` (`switch-service state image gpu`).
* **`qwen-image`** — the web face: Flask, pages, JSON API, WebStorage client, SSE client. `compose.yaml` here.
  Networks: **`deploy_auth_network`** (home: the gateway proxies `http://qwen-image:8000`; WebStorage answers as
  `app`) plus **`models_network`** as a client, only to resolve `qwen-inference:8010`.

No `depends_on` between them — they are different projects. The web face tolerates a missing pipeline:
`/healthz` reports it, `/api/generate` answers `503` with the reason, and the pages keep working.

## Consequences

**Good**

* Each half has one owner and one lifecycle: `podman compose up -d` here can never reload the weights, and
  restarting the model never takes the UI down.
* The model never sees the auth network and WebStorage never sees the model's network: the role-check boundary of
  ADR 0001/0002 is now a property of the topology, not of a rule someone has to remember.
* The model's VRAM claim, restart policy and env live in exactly the place that owns the card (the switch catalog on
  master-node, `Common/models/services/inference` elsewhere).

**Costs**

* Two commands instead of one: `state image gpu` (or `run.sh`) and `podman compose up -d`.
* Two env files: model settings moved to `Common/models/services/inference/env.example`; the keys this repository's
  `.env` still carries for the model are ignored.
* `qwen_image_gen_network` is no longer used by this project. It is left on the host for the stacks that were
  attached to it; nothing here publishes or reads it.
