"""Move a recorded task from the intake queue onto the gen queue."""

from __future__ import annotations

import logging

from .workers import handle_intake, serve

log = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    serve("intake", handle_intake)


if __name__ == "__main__":
    main()
