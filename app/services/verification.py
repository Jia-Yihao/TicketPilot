from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.commerce import Order, Refund
from app.models.enums import RefundStatus, VerificationStatus


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    checks: tuple[dict[str, Any], ...]

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.VERIFIED_SUCCESS


class VerificationService:
    def verify_refund(
        self, *, refund: Refund | None, order: Order, expected_amount_cents: int
    ) -> VerificationResult:
        checks: list[dict[str, Any]] = []

        checks.append(
            {
                "name": "refund_record_exists",
                "passed": refund is not None,
                "detail": refund.id if refund else None,
            }
        )
        if refund is None:
            return VerificationResult(VerificationStatus.PENDING_CONFIRMATION, tuple(checks))

        checks.append(
            {
                "name": "refund_belongs_to_order",
                "passed": refund.order_id == order.id,
            }
        )
        checks.append(
            {
                "name": "refund_amount_matches",
                "passed": refund.amount_cents == expected_amount_cents,
                "expected": expected_amount_cents,
                "actual": refund.amount_cents,
            }
        )
        checks.append(
            {
                "name": "refund_status",
                "passed": refund.status is RefundStatus.SUCCEEDED,
                "actual": refund.status.value,
            }
        )
        checks.append(
            {
                "name": "refundable_balance_non_negative",
                "passed": order.refundable_cents >= 0,
                "actual": order.refundable_cents,
            }
        )

        if refund.status is RefundStatus.FAILED:
            status = VerificationStatus.FAILED
        elif refund.status is RefundStatus.SUCCEEDED and all(c["passed"] for c in checks):
            status = VerificationStatus.VERIFIED_SUCCESS
        elif refund.status is RefundStatus.SUCCEEDED:
            status = VerificationStatus.NEEDS_HUMAN_REVIEW
        else:
            status = VerificationStatus.PENDING_CONFIRMATION
        return VerificationResult(status, tuple(checks))
