# Bootstrap for the qimg workspace

The shared services are already running. This repository does not start Mongo, RabbitMQ, MinIO, or Caddy, and it does not create Podman networks. Compose joins networks that already exist.

Browser traffic stays on `https://image.filenkov.store` → Caddy → the Auth Gateway → `http://qwen-image:8000`. A separate vhost such as `qimg.filenkov.store` needs a DNS A record, a Caddy site block, and a gateway host block. That is not done here. There is no wildcard certificate: an unknown host fails TLS.

## Networks

| Process | Networks |
|---|---|
| `qwen-image` | `deploy_auth_network`, `models_network`, `common_network` |
| `qimg-processor` | `common_network` |
| `qimg-orchestrator` | `common_network`, `models_network` |

`mongo`, `minio`, and `rabbit` (alias of `rabbitmq`) resolve on `common_network` (`10.89.2.0/24` on the measured host). They do not resolve from `deploy_auth_network`. The name `redis` on the auth network is `rejected_cache`. Do not use it.

`qwen-inference` resolves on `models_network` only. It is started by Switch-Service (`state image gpu`) or from `Common/models/services/inference`, not by this compose file.

## Mongo

Database name: `qimg`. The client creates it on first write. This service does not use Postgres and does not create `db_qimg`.

```
MONGO_URL=mongodb://mongo:27017/qimg
MONGO_DB=qimg
```

If the shared Mongo requires a user, put that user in `MONGO_URL`. Take the password from `Common/.env.common`. Do not commit it.

## RabbitMQ

The client declares a durable topic exchange `qimg` and durable queues `qimg.intake` and `qimg.gen` on connect. A separate vhost is optional. The Rabbit user needs permission to declare those names.

```
RABBIT_URL=amqp://qimg:change-me@rabbit:5672/
```

## MinIO

One bucket: `qimg-images`. Keys look like `{user_id}/projects/...`, `{user_id}/batches/...`, `{user_id}/tasks/...`, and `{user_id}/renders/...`.

Create the bucket and the IAM user by hand. Compose will not do it, and a MinIO volume recreated from scratch loses both. Use the `mc` alias `myminio` → `127.0.0.1:9000`. Do not run the app as `minioadmin`.

An IAM user's access key may be longer than 20 characters. A service-account access key may not. Create an IAM user.

```bash
mc alias set myminio http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"
mc mb --ignore-existing myminio/qimg-images
mc admin user add myminio qimg 'replace-with-a-long-secret'
mc admin policy create myminio qimg-images /dev/stdin <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:*"],"Resource":["arn:aws:s3:::qimg-images","arn:aws:s3:::qimg-images/*"]}]}
JSON
mc admin policy attach myminio qimg-images --user qimg
```

Then set `MINIO_ACCESS_KEY=qimg`, `MINIO_SECRET_KEY` to that secret, `MINIO_ENDPOINT=minio:9000`, `MINIO_BUCKET=qimg-images`, `MINIO_SECURE=false`.

## Secrets for provider keys

`SETTINGS_SECRET` seals Wan and Higgsfield keys before they are written. Generate one and keep it stable: changing it makes stored keys unreadable.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Empty `SETTINGS_SECRET` refuses to store an external key. Local Qwen has no key.

## What this document does not do

It does not publish port 8000 or 8010. It does not add a login. It does not point the gateway at a new upstream. The role on the existing host remains `image`.
