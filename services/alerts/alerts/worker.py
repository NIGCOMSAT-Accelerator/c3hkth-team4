"""The alerts worker: two scheduled jobs, nothing else.

  04:30 daily   trigger the day's risk scoring
  every 30 min  evaluate every active subscription against current risk

Scoring lives in the processing layer, so this worker invokes it as a
subprocess rather than importing it — services/alerts must not depend on
services/processing.
"""

from __future__ import annotations

import subprocess
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from core.config import settings
from core.logging import configure_logging, get_logger

configure_logging("alerts")
log = get_logger("alerts.worker")

CITY = "abuja"


def run_daily_scoring() -> None:
    """Kick the processing layer's daily scorer."""
    log.info("daily_scoring_triggered", city=CITY)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "processing.scoring.daily", "--city", CITY],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        log.info("daily_scoring_finished", returncode=result.returncode)
        if result.returncode != 0:
            log.error("daily_scoring_failed", stderr=result.stderr[-600:])
    except FileNotFoundError:
        # Expected: the processing package is not installed in this container.
        # In compose the scheduler is informational and scoring is run in the
        # processing service; production wires this to a shared runner.
        log.warning(
            "processing_not_available_here",
            note="run `docker compose exec processing python -m processing.scoring.daily`",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("daily_scoring_error", error=str(exc)[:300])


def run_evaluation() -> None:
    from alerts.evaluate import run

    log.info("subscription_sweep_started")
    try:
        run(now=True, force=False)
    except Exception as exc:  # noqa: BLE001 — a bad sweep must not kill the worker
        log.error("subscription_sweep_failed", error=str(exc)[:300])


def main() -> int:
    settings.ensure_dirs()
    scheduler = BlockingScheduler(timezone="Africa/Lagos")
    scheduler.add_job(run_daily_scoring, "cron", hour=4, minute=30, id="daily_scoring")
    scheduler.add_job(run_evaluation, "interval", minutes=30, id="subscription_sweep")

    log.info(
        "worker_started",
        jobs=["daily_scoring 04:30 Africa/Lagos", "subscription_sweep every 30 min"],
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("worker_stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
