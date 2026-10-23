# 0013 — Workspace, two workers, and common_network

Status: accepted (2026-10-23)

Extends [0012](0012-model-container-is-a-separate-project.md). Supersedes the WebStorage archive in [0005](0005-webstorage-best-effort.md) for this flow.

## Context

The web face was one Flask process: a form in, a PNG out, and a best-effort copy to WebStorage. The product is now a project workspace. A generation is a task that outlives the HTTP request, may be one of several copies, and may call Local Qwen, Wan, or Higgsfield. The browser polls status. The model process must stay free of Mongo, RabbitMQ, and MinIO.

On the live host, `qwen-image` could resolve `qwen-inference`, `app`, and `redis`. That `redis` name is `rejected_cache` on `deploy_auth_network`, not a broker. `mongo`, `minio`, and `rabbit` resolve only on `common_network`. A container that does not join `common_network` cannot see them.

The public name `image` is already a vhost, a gateway role, an upstream, a container, and a User-Service role. New resources use the prefix `qimg`.

## Decision

* Mongo database `qimg` is the source of truth. This service does not add a Postgres database.
* One MinIO bucket, `qimg-images`, holds every blob. Keys start with `{user_id}/`. An IAM user `qimg` is limited to that bucket. The root `minioadmin` user is not a runtime credential. Compose does not create the bucket or the user.
* RabbitMQ exchange `qimg` (topic, durable) and queues `qimg.intake` and `qimg.gen`. The API inserts the task, stores ordered artifact keys, then publishes `intake`. The processor re-reads Mongo and publishes `gen`. The orchestrator claims `queued` → `running` and is the only process that calls a provider.
* Three processes, one image, no published port, no `depends_on`, no GPU: `qwen-image` (gunicorn), `qimg-processor`, `qimg-orchestrator`.
* Networks: the web process joins `deploy_auth_network`, `models_network`, and `common_network`. The processor joins `common_network` only. The orchestrator joins `common_network` and `models_network`. All three networks are external.
* `qwen-inference` stays a separate project. It does not read or write Mongo, RabbitMQ, or MinIO. Progress on the browser is a poll of Mongo. A change request asks inference to emit `event: progress`; until that ships, denoise log lines are mapped into the same 0..10 scale.
* Provider secrets are sealed with Fernet before insert and are never returned. An empty `SETTINGS_SECRET` refuses to store an external key.
* The browser stays on `https://image.filenkov.store`. A `qimg` vhost is possible later and is not part of this change.
* When `MONGO_URL`, `RABBIT_URL`, and `MINIO_ENDPOINT` are all empty, the process uses an in-memory store so tests and a local page need no broker. Any partial set is a startup error.

## Consequences

The web container can now reach shared infrastructure, so its trust boundary is no longer "auth network only". It still does not authenticate. The gateway injects `X-Auth-User-Id`. A missing id is 400.

Recreating MinIO from scratch drops the bucket and the IAM user. An operator recreates them with `mc`. Recreating RabbitMQ does not require a hand-built queue: the client declares `qimg.intake` and `qimg.gen` on connect, which needs a user allowed to declare them.

WebStorage is unused by this flow. The module remains, and nothing in `app/` calls it.
