from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utcnow
from app.models.commerce import Order, Refund
from app.models.enums import OrderStatus, RefundStatus

FIXTURE_FILE = Path(__file__).with_name("fixtures.json")


def load_fixtures(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or FIXTURE_FILE).read_text(encoding="utf-8"))


def apply_fixtures(
    session_factory: sessionmaker[Session], fixtures: dict[str, Any] | None = None
) -> dict[str, int]:
    """Insert the eval-only rows; safe to run on every dataset run.

    Dates are stored as "days ago" so a case that depends on the 30-day
    refund window keeps working no matter when the suite runs.
    """
    fixtures = fixtures if fixtures is not None else load_fixtures()
    now = utcnow()
    created = {"orders": 0, "refunds": 0}
    with session_factory() as session:
        for row in fixtures.get("orders", []):
            if session.get(Order, row["id"]) is not None:
                continue
            delivered_days = row.get("delivered_days_ago")
            session.add(
                Order(
                    id=row["id"],
                    tenant_id=row["tenant_id"],
                    user_id=row["user_id"],
                    status=OrderStatus(row["status"]),
                    total_cents=int(row["total_cents"]),
                    refundable_cents=int(row["refundable_cents"]),
                    paid_at=now - timedelta(days=int(row.get("paid_days_ago", 1))),
                    delivered_at=(
                        now - timedelta(days=int(delivered_days))
                        if delivered_days is not None
                        else None
                    ),
                )
            )
            created["orders"] += 1
        session.flush()

        for row in fixtures.get("refunds", []):
            exists = session.scalar(
                select(Refund.id).where(Refund.idempotency_key == row["idempotency_key"])
            )
            if exists is not None:
                continue
            session.add(
                Refund(
                    id=row["id"],
                    order_id=row["order_id"],
                    tenant_id=row["tenant_id"],
                    user_id=row["user_id"],
                    amount_cents=int(row["amount_cents"]),
                    status=RefundStatus(row["status"]),
                    external_refund_id=row.get("external_refund_id"),
                    idempotency_key=row["idempotency_key"],
                )
            )
            created["refunds"] += 1
        session.commit()
    return created
