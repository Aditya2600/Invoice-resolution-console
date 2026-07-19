from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.db.pg import init_schema
from app.db.repository import claim_one_job
from app.pipeline.orchestrator import process_job


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run() -> None:
    settings = get_settings()
    init_schema()
    logger.info("Invoice worker started")
    while True:
        job = claim_one_job()
        if not job:
            time.sleep(settings.worker_poll_seconds)
            continue
        try:
            logger.info("Processing job %s", job["job_id"])
            process_job(job)
        except Exception:
            logger.exception("Job %s failed", job["job_id"])


if __name__ == "__main__":
    run()

