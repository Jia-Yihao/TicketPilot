from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import Identity
from app.models.conversation import Conversation, ConversationMessage

MAX_STORED_TURNS = 40


@dataclass(frozen=True, slots=True)
class ConversationSlots:
    """Business slots the support agent may carry across turns."""

    order_id: str | None = None
    product_id: str | None = None
    amount_cents: int | None = None
    pending_intent: str | None = None
    pending_request: str | None = None
    # Escalation counters. The trigger table reads them; they are how a
    # conversation remembers that it already failed twice.
    failed_intents: dict[str, int] = field(default_factory=dict)
    clarification_count: int = 0
    negative_rounds: int = 0
    last_intent: str | None = None
    resolved: bool = False
    last_message_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "product_id": self.product_id,
            "amount_cents": self.amount_cents,
            "pending_intent": self.pending_intent,
            "pending_request": self.pending_request,
            "failed_intents": dict(self.failed_intents),
            "clarification_count": self.clarification_count,
            "negative_rounds": self.negative_rounds,
            "last_intent": self.last_intent,
            "resolved": self.resolved,
            "last_message_at": self.last_message_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> ConversationSlots:
        data = raw or {}
        amount = data.get("amount_cents")
        failed = data.get("failed_intents")
        return cls(
            order_id=_as_str(data.get("order_id")),
            product_id=_as_str(data.get("product_id")),
            amount_cents=amount if isinstance(amount, int) else None,
            pending_intent=_as_str(data.get("pending_intent")),
            pending_request=_as_str(data.get("pending_request")),
            failed_intents={
                str(key): int(value)
                for key, value in (failed or {}).items()
                if isinstance(value, int)
            },
            clarification_count=int(data.get("clarification_count") or 0),
            negative_rounds=int(data.get("negative_rounds") or 0),
            last_intent=_as_str(data.get("last_intent")),
            resolved=bool(data.get("resolved", False)),
            last_message_at=_as_str(data.get("last_message_at")),
        )


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    conversation: Conversation
    slots: ConversationSlots
    inherited: tuple[str, ...] = field(default_factory=tuple)


class ConversationService:
    """Persists conversation slots and turns for the read-only support path.

    Conversations are tenant and user scoped: an id belonging to somebody else
    is treated as unknown, so no cross-tenant state can be inherited.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def resolve(
        self, identity: Identity, conversation_id: str | None
    ) -> ConversationTurn:
        if conversation_id:
            conversation = self._owned(identity, conversation_id)
            if conversation is not None:
                return ConversationTurn(
                    conversation=conversation,
                    slots=ConversationSlots.from_dict(conversation.slots),
                )
        return ConversationTurn(conversation=self._create(identity), slots=ConversationSlots())

    def _owned(
        self, identity: Identity, conversation_id: str
    ) -> Conversation | None:
        conversation = self.session.get(Conversation, conversation_id)
        if (
            conversation is None
            or conversation.tenant_id != identity.tenant_id
            or conversation.user_id != identity.user_id
        ):
            return None
        return conversation

    def _create(self, identity: Identity) -> Conversation:
        conversation = Conversation(
            id=f"conv_{uuid.uuid4().hex[:12]}",
            tenant_id=identity.tenant_id,
            user_id=identity.user_id,
            slots={},
            turn_count=0,
        )
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def save_slots(
        self, conversation: Conversation, slots: ConversationSlots
    ) -> None:
        conversation.slots = slots.to_dict()
        self.session.flush()

    def record(
        self,
        conversation: Conversation,
        *,
        role: str,
        content: str,
        intent: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ConversationMessage:
        message = ConversationMessage(
            id=f"cm_{uuid.uuid4().hex[:16]}",
            conversation_id=conversation.id,
            role=role,
            intent=intent,
            content=content,
            payload=payload or {},
        )
        self.session.add(message)
        conversation.turn_count += 1
        self.session.flush()
        return message

    def history(self, conversation: Conversation, limit: int = 10) -> list[ConversationMessage]:
        rows = self.session.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(limit)
        )
        return list(reversed(rows.scalars().all()))

    def trim(self, conversation: Conversation, keep: int = MAX_STORED_TURNS) -> None:
        stale = self.session.execute(
            select(ConversationMessage.id)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at.desc())
            .offset(keep)
        )
        ids = list(stale.scalars().all())
        if not ids:
            return
        self.session.execute(
            delete(ConversationMessage).where(ConversationMessage.id.in_(ids))
        )
