from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.core.time import utcnow
from app.models.commerce import Logistics, Order
from app.models.enums import LogisticsStatus, OrderStatus, ProactiveEventType
from app.models.memory import ProactiveRule, ProactiveRuleVersion
from app.retrieval.ingestion import ingest_document

POLICY_DIR = Path(__file__).resolve().parent / "resources" / "policies"


def seed_orders(session: Session) -> int:
    now = utcnow()
    rows: list[dict[str, Any]] = [
        {
            "id": "ORD-DEMO-1001",
            "tenant_id": "demo",
            "user_id": "alice",
            "status": OrderStatus.DELIVERED,
            "total_cents": 12900,
            "refundable_cents": 12900,
            "paid_at": now - timedelta(days=10),
            "delivered_at": now - timedelta(days=5),
            "logistics": {
                "carrier": "DemoExpress",
                "tracking_no": "DE10010001",
                "status": LogisticsStatus.DELIVERED,
                "last_event": "已签收，签收人：本人",
            },
        },
        {
            "id": "ORD-DEMO-1002",
            "tenant_id": "demo",
            "user_id": "alice",
            "status": OrderStatus.SHIPPED,
            "total_cents": 8000,
            "refundable_cents": 8000,
            "paid_at": now - timedelta(days=3),
            "delivered_at": None,
            "logistics": {
                "carrier": "DemoExpress",
                "tracking_no": "DE10020002",
                "status": LogisticsStatus.IN_TRANSIT,
                "last_event": "包裹在分拨中心停留超过 48 小时",
            },
        },
        {
            "id": "ORD-DEMO-2001",
            "tenant_id": "demo",
            "user_id": "bob",
            "status": OrderStatus.DELIVERED,
            "total_cents": 5000,
            "refundable_cents": 5000,
            "paid_at": now - timedelta(days=8),
            "delivered_at": now - timedelta(days=3),
            "logistics": {
                "carrier": "DemoExpress",
                "tracking_no": "DE20010003",
                "status": LogisticsStatus.DELIVERED,
                "last_event": "已签收",
            },
        },
        {
            "id": "ORD-OTHER-3001",
            "tenant_id": "acme",
            "user_id": "carol",
            "status": OrderStatus.DELIVERED,
            "total_cents": 3000,
            "refundable_cents": 3000,
            "paid_at": now - timedelta(days=6),
            "delivered_at": now - timedelta(days=2),
            "logistics": None,
        },
    ]
    for row in rows:
        if session.get(Order, row["id"]) is not None:
            continue
        logistics_row = row.pop("logistics")
        session.add(Order(**row))
        session.flush()
        if logistics_row:
            session.add(
                Logistics(
                    id=f"log_{row['id'].lower()}",
                    order_id=row["id"],
                    estimated_delivery=now + timedelta(days=2),
                    **logistics_row,
                )
            )
    session.flush()
    return len(rows)


def seed_policies(session: Session) -> int:
    manifest = json.loads((POLICY_DIR / "manifest.json").read_text(encoding="utf-8"))
    count = 0
    for item in manifest["documents"]:
        content = (POLICY_DIR / item["file"]).read_text(encoding="utf-8")
        result = ingest_document(
            session,
            tenant_id=item["tenant_id"],
            source_uri=item["source_uri"],
            title=item["title"],
            version=item["version"],
            content=content,
            effective_from=date.fromisoformat(item["effective_from"]),
            effective_to=(
                date.fromisoformat(item["effective_to"]) if item.get("effective_to") else None
            ),
            status=item.get("status", "ACTIVE"),
            meta={"policy_id": item["policy_id"], "demo": True},
        )
        count += int(result.created)
    session.flush()
    return count


def seed_proactive_rules(session: Session) -> int:
    rules = [
        {
            "id": "rule_demo_refund_settled",
            "tenant_id": "demo",
            "event_type": ProactiveEventType.REFUND_SETTLED.value,
            "template": "您的订单 {order_id} 退款 {amount} 元已到账，请注意查收。",
            "requires_confirmation": True,
            "cooldown_minutes": 60,
        },
        {
            "id": "rule_demo_ticket_updated",
            "tenant_id": "demo",
            "event_type": ProactiveEventType.TICKET_UPDATED.value,
            "template": "工单 {ticket_no} 有新进展：{summary}。需要我把详情发给您吗？",
            "requires_confirmation": True,
            "cooldown_minutes": 30,
        },
        {
            "id": "rule_demo_delivery_exception",
            "tenant_id": "demo",
            "event_type": ProactiveEventType.DELIVERY_EXCEPTION.value,
            "template": "订单 {order_id} 物流异常：{summary}。需要我帮您跟进吗？",
            "requires_confirmation": True,
            "cooldown_minutes": 120,
        },
    ]
    for row in rules:
        if session.get(ProactiveRule, row["id"]) is not None:
            continue
        session.add(ProactiveRule(**row))
    session.flush()
    # The live rule is version 1, so the history starts with a snapshot rather
    # than a gap: a notification produced before the first edit can still name
    # the words it used.
    for row in rules:
        version_id = f"prv_{str(row['id'])[5:][:30]}"
        if session.get(ProactiveRuleVersion, version_id) is not None:
            continue
        session.add(
            ProactiveRuleVersion(
                id=version_id,
                tenant_id=row["tenant_id"],
                event_type=row["event_type"],
                version=1,
                template=row["template"],
                enabled=True,
                requires_confirmation=row["requires_confirmation"],
                cooldown_minutes=row["cooldown_minutes"],
                change_id="seed",
                submitted_by="system",
                approved_by="system",
                reason="seed",
            )
        )
    session.flush()
    return len(rules)


def seed_all(session_factory: sessionmaker[Session]) -> dict[str, int]:
    with session_factory() as session:
        orders = seed_orders(session)
        policies = seed_policies(session)
        rules = seed_proactive_rules(session)
        session.commit()
        return {"orders": orders, "policies_created": policies, "rules": rules}
