"""Manual retry of failed runs, and the policy snapshot frozen onto every result."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.core.config import get_settings
from app.db import repository
from app.pipeline.decision import build_policy_snapshot, policy_hash
from tests.test_finalization import approve, extraction, matched


def test_only_failed_jobs_can_retry(make_po, make_job, fail_terminally) -> None:
    po_number = make_po(total="500")
    approved_job, approved_document = make_job()
    approve(approved_job, approved_document, po_number, "100")

    with pytest.raises(repository.RetryConflict):
        repository.retry_job(approved_job, requested_by="Priya")

    failed_job, _ = make_job()
    fail_terminally(failed_job)
    outcome = repository.retry_job(failed_job, requested_by="Priya", note="Model was down.")

    job = repository.get_job(failed_job)
    assert job["status"] == "PENDING"
    assert job["attempts"] == 0
    assert job["last_error"] is None
    assert job["lease_until"] is None
    assert (job["retry_generation"], job["manual_retry_count"]) == (1, 1)
    assert outcome["retry_generation"] == 1
    events = [e["stage"] for e in repository.get_job_detail(failed_job)["events"]]
    assert "stage_retry_requested" in events


def test_review_pending_job_cannot_retry(make_job) -> None:
    job_id, document_id = make_job()
    repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="NEEDS_REVIEW",
        extraction=extraction("100"),
        matched_po=None,
        reasons=["A reviewer must decide."],
        rule_checks={},
    )
    with pytest.raises(repository.RetryConflict):
        repository.retry_job(job_id, requested_by="Priya")


def test_retry_cannot_double_consume_a_po_allocation(make_po, make_job, po_row, allocations) -> None:
    """A re-run of an already-approved document reuses its allocation instead of making a second."""
    po_number = make_po(total="500")
    job_id, document_id = make_job()
    approve(job_id, document_id, po_number, "100")

    approve(job_id, document_id, po_number, "100")

    assert len(allocations(po_number)) == 1
    assert po_row(po_number)["consumed_amount"] == Decimal("100")


def test_policy_snapshot_survives_a_rule_config_change(make_po, make_job, tmp_path) -> None:
    po_number = make_po(total="500")
    job_id, document_id = make_job()
    snapshot = build_policy_snapshot("Globex Ltd")
    stored_hash = policy_hash(snapshot)

    repository.finalize_invoice_decision(
        job_id=job_id,
        document_id=document_id,
        decision_status="APPROVED",
        extraction=extraction("100"),
        matched_po=matched(po_number),
        reasons=["All checks passed."],
        rule_checks={},
        allocation_amount=Decimal("100"),
        policy_snapshot=snapshot,
        policy_hash=stored_hash,
    )

    result = repository.get_job_detail(job_id)["result"]
    assert result["policy_hash"] == stored_hash
    assert result["policy_snapshot"]["policy_version"] == get_settings().policy_version

    # Rewrite vendor_rules.json with a different tolerance; the stored decision is unchanged.
    rules_path = get_settings().vendor_rules_path
    original = rules_path.read_text()
    try:
        changed = json.loads(original)
        changed["__default__"]["amount_tolerance"] = "999.00"
        rules_path.write_text(json.dumps(changed))
        assert policy_hash(build_policy_snapshot("Globex Ltd")) != stored_hash
    finally:
        rules_path.write_text(original)

    reread = repository.get_job_detail(job_id)["result"]
    assert reread["policy_hash"] == stored_hash
    assert reread["policy_snapshot"] == result["policy_snapshot"]


def test_policy_hash_is_stable_for_identical_values() -> None:
    snapshot = build_policy_snapshot("Globex Ltd")
    assert policy_hash(snapshot) == policy_hash(dict(reversed(list(snapshot.items()))))
