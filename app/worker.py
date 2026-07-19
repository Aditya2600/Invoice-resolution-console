from __future__ import annotations

import logging
import time

from app.core.auth import validate_auth_configuration
from app.core.config import get_settings
from app.db.pg import init_schema
from app.db.repository import claim_one_job
from app.pipeline.orchestrator import process_job


# Bare message: the pipeline emits one JSON object per line, which a prefix would make unparseable.
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def run() -> None:
    settings = get_settings()
    validate_auth_configuration()
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
        except Exception as exc:
            # Detailed failures stay in the protected job audit record.
            logger.error("Job %s failed (%s)", job["job_id"], type(exc).__name__)


if __name__ == "__main__":
    run()
