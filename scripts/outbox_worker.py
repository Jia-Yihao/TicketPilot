"""The outbox worker.

It polls the queue and delivers what is due. Each pass reconciles the
refunds whose outcome nobody knows; a refund that is still unconfirmed
goes back to waiting, and one that never confirms becomes a dead
letter an operator can see in the seat API.

Run it next to the app, not inside it:

    python scripts/outbox_worker.py            # poll every 30 seconds
    python scripts/outbox_worker.py --once     # one pass and exit
"""

from __future__ import annotations

import argparse
import logging
import time

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import build_engine, build_session_factory
from app.services.runtime import TaskRuntime

logger = logging.getLogger("outbox_worker")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deliver the outbox queue.")
    parser.add_argument("--once", action="store_true", help="Run one pass and exit.")
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Seconds between passes when polling.",
    )
    parser.add_argument(
        "--limit", type=int, default=50, help="Messages per pass."
    )
    args = parser.parse_args()

    configure_logging()
    settings = get_settings()
    engine = build_engine(settings.database_url)
    runtime = TaskRuntime(build_session_factory(engine), settings)

    while True:
        report = runtime.deliver_outbox(limit=args.limit)
        if report.claimed:
            logger.info(
                "outbox pass: claimed=%s sent=%s failed=%s dead=%s",
                report.claimed,
                report.sent,
                report.failed,
                report.dead_lettered,
            )
        if args.once:
            break
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
