from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class DecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"


class ReviewAction(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class StageStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    INFO = "INFO"


class Evidence(BaseModel):
    field: str
    page: int | None = None
    quote: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class LineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceExtraction(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    po_number: str | None = None
    currency: str | None = "INR"
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    extraction_confidence: float = Field(default=0.0, ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    raw_text: str | None = None

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper().strip() if value else value


class PurchaseOrder(BaseModel):
    po_number: str
    vendor_name: str
    currency: str = "INR"
    total_amount: Decimal
    consumed_amount: Decimal = Decimal("0")
    status: str = "OPEN"

    @property
    def remaining_amount(self) -> Decimal:
        return self.total_amount - self.consumed_amount


class MatchResult(BaseModel):
    po: PurchaseOrder | None = None
    score: float = 0.0
    candidates: list[str] = Field(default_factory=list)
    reason: str | None = None


class InvoiceDecision(BaseModel):
    status: DecisionStatus
    reasons: list[str] = Field(default_factory=list)
    matched_po: PurchaseOrder | None = None
    match_confidence: float = Field(default=0.0, ge=0, le=1)
    rule_checks: dict[str, Any] = Field(default_factory=dict)
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)
    policy_hash: str | None = None


class InvoiceResult(BaseModel):
    document_id: str
    job_id: str
    decision: InvoiceDecision
    extraction: InvoiceExtraction
    model_name: str | None = None
    model_latency_ms: float | None = None
    created_at: datetime | None = None


class ExtractionCorrections(BaseModel):
    """Reviewer overrides. Stored on the review action only; the model extraction is preserved."""

    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None


class ReviewResolveRequest(BaseModel):
    action: ReviewAction
    reviewer_name: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)
    selected_po_number: str | None = None
    corrections: ExtractionCorrections | None = None


class RetryRequest(BaseModel):
    requested_by: str = Field(default="Operator", max_length=120)
    note: str | None = Field(default=None, max_length=2000)


class JobListItem(BaseModel):
    job_id: str
    document_id: str
    file_name: str
    status: JobStatus
    decision_status: DecisionStatus | None = None
    attempts: int
    created_at: datetime
    updated_at: datetime

