from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.core.money import format_cents
from app.core.policy import EvidenceScore, PolicyEngine
from app.core.security import Identity
from app.core.time import utcnow
from app.models.commerce import Order
from app.models.document import Document, DocumentChunk
from app.repositories.commerce import CommerceRepository
from app.retrieval.search import (
    PolicySearch,
    PolicySearchService,
    RetrievedChunk,
    classify_empty,
)
from app.services.catalog import CatalogProduct, CatalogService
from app.services.orders import OrderService
from app.services.refund_policy import RefundPolicy

CARD_KINDS = ("product", "policy", "order")

REFUND_REASON_LABELS = {
    "ORDER_CANCELLED": "订单已取消",
    "ORDER_STATUS_NOT_REFUNDABLE": "订单状态不满足退款条件",
    "NO_REFUNDABLE_BALANCE": "没有可退余额",
    "ALREADY_REFUNDED": "该订单已退款",
    "REFUND_STATUS_UNKNOWN": "退款状态待确认",
    "REFUND_ALREADY_REQUESTED": "已有在途退款申请",
    "AMOUNT_INVALID": "退款金额无效",
    "AMOUNT_EXCEEDS_REFUNDABLE": "退款金额超过可退余额",
    "AMOUNT_EXCEEDS_POLICY_CAP": "退款金额超过政策单笔上限",
}


def refund_reason_label(reason: str, engine: PolicyEngine) -> str:
    """User-facing reason; the window length comes from the policy, not the code."""
    if reason == "RETURN_WINDOW_EXPIRED":
        return f"已超过 {engine.refund.window_days} 天退货窗口"
    return REFUND_REASON_LABELS.get(reason, reason)

REFUND_STATUS_LABELS = {
    "REQUESTED": "已受理，等待退款工具执行",
    "SUCCEEDED": "退款已成功到账",
    "FAILED": "退款执行失败",
    "UNKNOWN": "状态待确认，需要先与渠道对账",
}

ORDER_STATUS_LABELS = {
    "PLACED": "已下单",
    "SHIPPED": "已发货",
    "DELIVERED": "已签收",
    "CANCELLED": "已取消",
}

# How many orders a clarification may offer as candidates.
CANDIDATE_LIMIT = 3


def _preview(text: str, limit: int = 96) -> str:
    compact = " ".join(text.split())
    return compact if len(compact) <= limit else compact[:limit].rstrip() + "…"


def _card_sections(
    sections: list[tuple[str | None, str, str]],
) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    total_chars = 0
    for index, (section_id, title, body) in enumerate(sections, start=1):
        total_chars += len(body)
        items.append(
            {
                "id": section_id or f"section-{index}",
                "title": title,
                "preview": _preview(body),
                "chars": len(body),
            }
        )
    return items, total_chars


def order_candidate(order: Order, product: CatalogProduct | None) -> dict[str, Any]:
    """One L1 candidate: what a customer who cannot remember an order number
    is shown instead of being asked the same question again.

    Four facts and nothing else — order number, product name, status, delivery
    time. No amount, no balance, no logistics: a candidate only has to be
    recognisable, and opening it is a separate, scoped read of the full card.
    """
    name = product.name if product is not None else "最近订单"
    status_label = ORDER_STATUS_LABELS.get(order.status.value, order.status.value)
    delivered = (
        f"{order.delivered_at.date().isoformat()} 签收" if order.delivered_at else "未签收"
    )
    return {
        "kind": "order_candidate",
        "card_id": order.id,
        "title": f"订单 {order.id}",
        "subtitle": name,
        "summary": f"{status_label} · {delivered}",
        "badges": [],
        "fields": {
            "order_id": order.id,
            "product_name": name,
            "status": order.status.value,
            "delivered_at": order.delivered_at.isoformat() if order.delivered_at else None,
        },
        "sections": [],
        "source": None,
        "total_chars": 0,
    }


@dataclass(frozen=True, slots=True)
class PolicyEvidence:
    """The policy cards, how strong the best hit was, and why it was empty.

    ``best`` is ``None`` when nothing matched at all; ``empty_reason`` then says
    which kind of emptiness it was. Keeping the two next to the cards means a
    caller never has to re-derive "was this empty?" on its own.
    """

    cards: list[dict[str, Any]]
    best: EvidenceScore | None
    empty_reason: str | None


class SupportContextService:
    """Builds compact cards first and serves full sections on demand."""

    def __init__(
        self,
        session: Session,
        catalog: CatalogService,
        *,
        engine: PolicyEngine,
        policy_max_results: int = 6,
        policy_min_score: float = 0.01,
        policy_search: PolicySearch | None = None,
    ) -> None:
        self.session = session
        self.catalog = catalog
        self.orders = OrderService(session)
        self.commerce = CommerceRepository(session)
        self.engine = engine
        self.policy_search = policy_search or PolicySearchService(
            session,
            max_results=policy_max_results,
            min_score=policy_min_score,
        )

    # ------------------------------------------------------------- products
    def product_card(self, product: CatalogProduct) -> dict[str, Any]:
        sections, total_chars = _card_sections(
            [(section.id, section.title, section.body) for section in product.sections]
        )
        return {
            "kind": "product",
            "card_id": product.product_id,
            "title": product.name,
            "subtitle": f"{product.sku} · {product.category}",
            "summary": product.summary,
            "badges": list(product.badges),
            "fields": dict(product.fields),
            "sections": sections,
            "source": "catalog://products",
            "total_chars": total_chars,
        }

    def product_section(
        self, card_id: str, section_id: str
    ) -> dict[str, Any]:
        product = self._require_product(card_id)
        section = next(
            (item for item in product.sections if item.id == section_id), None
        )
        if section is None:
            raise NotFoundError("Context section not found.")
        return {
            "kind": "product",
            "card_id": product.product_id,
            "title": product.name,
            "section": {
                "id": section.id,
                "title": section.title,
                "content": section.body,
                "chars": len(section.body),
            },
            "source": "catalog://products",
            "citation": None,
        }

    # -------------------------------------------------------------- policy
    def policy_cards(
        self, identity: Identity, query: str, *, limit: int = 2
    ) -> list[dict[str, Any]]:
        return self.policy_evidence(identity, query, limit=limit).cards

    def policy_evidence(
        self, identity: Identity, query: str, *, limit: int = 2
    ) -> PolicyEvidence:
        """The policy cards plus the strength of the best hit, on its own scale.

        ``best`` is what the escalation table compares against: no score means
        the retrieval found nothing at all, and ``empty_reason`` says whether the
        library had nothing or this phrasing simply missed.
        """
        matches = self.policy_search.search(identity, query)
        if not matches:
            # A blank query never touches the library; only a real miss pays for
            # the visibility count. The count comes from the BM25 service itself:
            # the injected searcher may be a rewriter or a hybrid channel, and
            # neither answers "could this tenant read anything at all?".
            visible = 0
            if query.strip():
                visible = len(
                    PolicySearchService(self.session).visible_chunks(identity)
                )
            return PolicyEvidence([], None, classify_empty(query, visible))
        grouped: dict[str, RetrievedChunk] = {}
        for match in matches:
            grouped.setdefault(match.document_id, match)
        cards: list[dict[str, Any]] = []
        for document_id, top in grouped.items():
            document = self._require_document(identity, document_id)
            if document is None:
                continue
            cards.append(self._policy_card(document, top=top))
            if len(cards) >= limit:
                break
        best = max(grouped.values(), key=lambda hit: hit.score)
        score, kind = best.evidence()
        return PolicyEvidence(cards, EvidenceScore(score=score, kind=kind), None)

    def policy_card(
        self, identity: Identity, document_id: str
    ) -> dict[str, Any]:
        document = self._require_document(identity, document_id)
        if document is None:
            raise NotFoundError("Context card not found.")
        return self._policy_card(document)

    def policy_section(
        self, identity: Identity, card_id: str, section_id: str
    ) -> dict[str, Any]:
        document = self._require_document(identity, card_id)
        if document is None:
            raise NotFoundError("Context card not found.")
        sections = self._document_sections(document)
        for index, (title, body) in enumerate(sections, start=1):
            if f"section-{index}" == section_id:
                return {
                    "kind": "policy",
                    "card_id": document.id,
                    "title": document.title,
                    "section": {
                        "id": f"section-{index}",
                        "title": title,
                        "content": body,
                        "chars": len(body),
                    },
                    "source": document.source_uri,
                    "citation": {
                        "type": "policy",
                        "document_id": document.id,
                        "version": document.version,
                        "title": document.title,
                        "section": title,
                        "source_uri": document.source_uri,
                        "effective_from": document.effective_from.isoformat(),
                        "effective_to": (
                            document.effective_to.isoformat()
                            if document.effective_to
                            else None
                        ),
                    },
                }
        raise NotFoundError("Context section not found.")

    # --------------------------------------------------------------- order
    def order_card(self, identity: Identity, order_id: str) -> dict[str, Any]:
        return self._order_payload(identity, order_id)[0]

    def order_candidates(
        self, identity: Identity, *, limit: int = CANDIDATE_LIMIT
    ) -> list[dict[str, Any]]:
        """The caller's own recent orders, as the L1 facts a chip can carry.

        A customer who cannot remember an order number does not need a second
        asking — they need their own list. Both the tenant and the user are in
        the predicate and neither is relaxed, so a seat asking on someone's
        behalf never gets that someone's order numbers back as chips.
        ``sections`` stays empty on purpose: opening a candidate is a request
        for the full ``kind=order`` card, which is scoped and audited separately.
        """
        orders = self.commerce.list_recent_orders(
            identity.tenant_id, identity.user_id, limit=limit
        )
        return [order_candidate(order, self.catalog.for_order(order.id)) for order in orders]

    def order_section(
        self, identity: Identity, card_id: str, section_id: str
    ) -> dict[str, Any]:
        card, bodies = self._order_payload(identity, card_id)
        for section in card["sections"]:
            if section["id"] != section_id:
                continue
            body = bodies[section_id]
            return {
                "kind": "order",
                "card_id": card_id,
                "title": card["title"],
                "section": {
                    "id": section_id,
                    "title": section["title"],
                    "content": body,
                    "chars": len(body),
                },
                "source": card["source"],
                "citation": None,
            }
        raise NotFoundError("Context section not found.")

    def refund_records(
        self, identity: Identity, order_id: str
    ) -> list[dict[str, Any]]:
        """Refund attempts recorded for an order the caller may read."""
        self.orders.require_order(identity, order_id)
        rows = self.commerce.list_refunds(order_id)
        return [
            {
                "refund_id": row.id,
                "amount_cents": row.amount_cents,
                "amount": format_cents(row.amount_cents),
                "status": row.status.value,
                "status_label": REFUND_STATUS_LABELS.get(row.status.value, row.status.value),
                "provider": row.provider,
                "external_refund_id": row.external_refund_id,
                "created_at": row.created_at.isoformat(),
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]

    def _order_payload(
        self, identity: Identity, order_id: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        order = self.orders.require_order(identity, order_id)
        logistics = self.orders.get_logistics(identity, order.id)
        product = self.catalog.for_order(order.id)

        order_lines = [
            f"订单号：{order.id}",
            f"状态：{order.status.value}",
            f"订单金额：{format_cents(order.total_cents)} {order.currency}",
            f"可退余额：{format_cents(order.refundable_cents)} {order.currency}",
            f"支付时间：{order.paid_at.isoformat()}",
            f"签收时间：{order.delivered_at.isoformat() if order.delivered_at else '未签收'}",
            f"数据版本：v{order.version}",
        ]
        if product is not None:
            order_lines.insert(1, f"商品：{product.name}（{product.sku}）")

        if logistics is None:
            logistics_body = "该订单暂无物流记录。"
        else:
            logistics_body = "\n".join(
                [
                    f"承运商：{logistics.carrier}",
                    f"运单号：{logistics.tracking_no}",
                    f"物流状态：{logistics.status.value}",
                    f"最新节点：{logistics.last_event}",
                    f"更新时间：{logistics.updated_at.isoformat()}",
                    "预计送达："
                    + (
                        logistics.estimated_delivery.isoformat()
                        if logistics.estimated_delivery
                        else "待更新"
                    ),
                ]
            )

        eligibility = RefundPolicy(self.session, self.engine).check(identity, order.id)
        if eligibility.eligible:
            refund_headline = (
                f"按当前政策 {eligibility.policy_version}，可退金额 "
                f"{format_cents(eligibility.amount_cents)} {order.currency}，"
                "提交后仍需人工审批。"
            )
        else:
            reasons = "、".join(
                refund_reason_label(reason, self.engine)
                for reason in eligibility.reasons
            )
            refund_headline = f"当前不符合退款条件：{reasons}。"
        refund_body = "\n".join(
            [
                refund_headline,
                f"可退上限：{format_cents(eligibility.max_refundable_cents)} "
                f"{order.currency}",
                f"政策版本：{eligibility.policy_version}",
                "资格校验是只读结论；实际退款必须由确定性工作流创建任务并通过审批。",
            ]
        )

        bodies = {
            "section-1": "\n".join(order_lines),
            "section-2": logistics_body,
            "section-3": refund_body,
        }
        sections, total_chars = _card_sections(
            [
                (None, "订单字段", bodies["section-1"]),
                (None, "物流记录", bodies["section-2"]),
                (None, "退款资格", bodies["section-3"]),
            ]
        )
        badges = [order.status.value, f"可退 {format_cents(order.refundable_cents)}"]
        return (
            {
                "kind": "order",
                "card_id": order.id,
                "title": f"订单 {order.id}",
                "subtitle": product.name if product is not None else "订单与履约信息",
                "summary": self.orders.order_summary(order),
                "badges": badges,
                "fields": {
                    "order_id": order.id,
                    "status": order.status.value,
                    "total_cents": order.total_cents,
                    "refundable_cents": order.refundable_cents,
                    "currency": order.currency,
                    "version": order.version,
                    "paid_at": order.paid_at.isoformat(),
                    "delivered_at": (
                        order.delivered_at.isoformat() if order.delivered_at else None
                    ),
                },
                "sections": sections,
                "source": f"orders/{order.id}",
                "total_chars": total_chars,
            },
            bodies,
        )

    # --------------------------------------------------------------- cards
    def get_card(
        self, identity: Identity, kind: str, card_id: str
    ) -> dict[str, Any]:
        if kind == "product":
            return self.product_card(self._require_product(card_id))
        if kind == "order":
            return self.order_card(identity, card_id)
        if kind == "policy":
            return self.policy_card(identity, card_id)
        raise ValidationAppError(
            "Unknown context card kind.",
            details={"allowed": list(CARD_KINDS)},
        )

    def get_section(
        self, identity: Identity, kind: str, card_id: str, section_id: str
    ) -> dict[str, Any]:
        if kind == "product":
            return self.product_section(card_id, section_id)
        if kind == "order":
            return self.order_section(identity, card_id, section_id)
        if kind == "policy":
            return self.policy_section(identity, card_id, section_id)
        raise ValidationAppError(
            "Unknown context card kind.",
            details={"allowed": list(CARD_KINDS)},
        )

    # ------------------------------------------------------------- private
    def _require_product(self, card_id: str) -> CatalogProduct:
        product = self.catalog.get(card_id)
        if product is None:
            raise NotFoundError("Context card not found.")
        return product

    def _require_document(
        self, identity: Identity, document_id: str
    ) -> Document | None:
        today = utcnow().date()
        document = self.session.get(Document, document_id)
        if (
            document is None
            or document.tenant_id != identity.tenant_id
            or document.status != "ACTIVE"
            or document.effective_from > today
            or (document.effective_to is not None and document.effective_to < today)
        ):
            return None
        return document

    def _document_sections(self, document: Document) -> list[tuple[str, str]]:
        chunks = self.session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document.id)
            .order_by(DocumentChunk.chunk_index)
        ).all()
        grouped: list[tuple[str, list[str]]] = []
        index: dict[str, int] = {}
        for chunk in chunks:
            title = chunk.section or "正文"
            if title not in index:
                index[title] = len(grouped)
                grouped.append((title, []))
            grouped[index[title]][1].append(chunk.content)
        return [
            (title, "\n\n".join(parts))
            for title, parts in grouped
        ]

    def _policy_card(
        self, document: Document, *, top: RetrievedChunk | None = None
    ) -> dict[str, Any]:
        sections, total_chars = _card_sections(
            [(None, title, body) for title, body in self._document_sections(document)]
        )
        summary_source = top.content if top is not None else sections[0]["preview"]
        return {
            "kind": "policy",
            "card_id": document.id,
            "title": document.title,
            "subtitle": f"{document.version} · 生效 {document.effective_from.isoformat()}",
            "summary": _preview(summary_source, 140),
            "badges": [f"版本 {document.version}", "现行有效"],
            "fields": {
                "document_id": document.id,
                "version": document.version,
                "status": document.status,
                "effective_from": document.effective_from.isoformat(),
                "effective_to": (
                    document.effective_to.isoformat()
                    if document.effective_to
                    else None
                ),
                "source_uri": document.source_uri,
                "matched_section": top.section if top is not None else None,
            },
            "sections": sections,
            "source": document.source_uri,
            "total_chars": total_chars,
        }
