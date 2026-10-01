# ADR 0008. One worker and a lock on the GPU

## Status

Accepted

## Context

The model takes most of the V100 VRAM (ADR 0003). A second gunicorn process with the same pipeline would duplicate the weights and OOM immediately. A prefork after `from_pretrained` would copy an already loaded CUDA context. That mode does not work on this card.

One thread for everything is also a poor fit. Generation holds the call for tens of seconds or minutes, and during that time the form polls `GET /api/logs`. `GET /healthz` must answer while a frame is running. The log buffer (`app/logbuf.py`) is built for several threads of one process: generation writes the log, another thread serves `since(after)`.

Gunicorn's default timeout is 30 s. A 2048 frame and 40 steps on a V100 without FlashAttention exceeds that. The worker would be killed mid-pass.

## Decision

`QwenImageGenerator` has two locks: `_load_lock` around weight loading and `_gpu_lock` around the pipeline call. One worker, one queue for the card. Concurrent HTTP requests wait on the lock instead of sharing VRAM.

Image command:

```text
gunicorn --bind 0.0.0.0:8000 --workers 1 --threads 4 --timeout 0 wsgi:app
```

`--timeout 0` disables killing the worker for request duration. Four threads leave room for the healthcheck, logs, and the next request, which waits on `_gpu_lock`. The `Dockerfile` also sets `--graceful-timeout 60` and `--access-logfile -`.

`PRELOAD_MODEL` loads weights on a background thread of the same process, not in a second worker.

## Consequences

- Throughput is one frame at a time. This service cannot go faster by adding workers.
- `tests/test_deploy_docs.py` checks `--workers 1` and `--timeout 0`, but not `--threads 4`. The test will not catch a lost thread count.
- Restarting the container aborts the current frame and drops the log ring. Weights on the `/data/huggingface` volume remain.
- A VRAM OOM is not fixed by a second worker. See ADR 0003.
