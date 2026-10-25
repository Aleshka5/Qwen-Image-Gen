"""Run gen-queue tasks against local Qwen or an external image provider."""

from __future__ import annotations

import logging

from .workers import fail_orphans, handle_gen, serve


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    serve("gen", handle_gen, recover=fail_orphans)


if __name__ == "__main__":
    main()
