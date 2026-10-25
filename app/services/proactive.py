"""Event-triggered proposals that the customer must confirm.

The chain is fixed: an event type matches one tenant rule, the rule's
template renders from an allowlisted context, and the result is a
notification that waits for the customer. Confirmation writes an outbox row
in the same transaction; the service cannot send anything the customer did
not approve.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from string import Formatter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.enums import (
    NotificationStatus,
    ProactiveEventType,
)
from app.models.memory import CustomerProfile, Notification, ProactiveRule
from app.observability.redaction import redact
from app.repositories.audit import AuditRepository
from app.services.memory import notification_view
from app.services.outbox import OutboxService

__all__ = ["EVENT_TITLES", "ProactiveService", "rule_view"]

EVENT_TITLES: dict[str, str] = {
    ProactiveEventType.REFUND_SETTLED.value: "退款到账提醒",
    ProactiveEventType.TICKET_UPDATED.value: "工单进展提醒",
    ProactiveEventType.DELIVERY_EXCEPTION.value: "物流异常提醒",
}

_CONTEXT_KEYS = frozenset(
    {"order_id", "ticket_no", "amount", "summary", "status", "product"}
)
# ``emit`` truncates the rendered body to 500 characters. Storing a template
# that long would let the tail of a sentence disappear on delivery, so the
# editable template stops short of the line that cuts it.
_TEMPLATE_LIMIT = 400
_COOLDOWN_MAX_MINUTES = 1440
_ACTIVE_STATUSES = frozenset(
    {
        NotificationStatus.PENDING_CONFIRMATION.value,
        NotificationStatus.CONFIRMED.value,
        NotificationStatus.SENT.value,
    }
)
_CONFIRMED_STATUSES = frozenset(
    {
        NotificationStatus.CONFIRMED.value,
        NotificationStatus.SENT.value,
    }
)


class _SafeContext(dict[str, str]):
    def __missing__(self, key: str) -> str:
        return ""


def _template_fields(template: str) -> list[str]:
    return sorted(
        {field for _, field, _, _ in Formatter().parse(template) if field}
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def rule_view(row: ProactiveRule) -> dict[str, Any]:
    return {
        "rule_id": row.id,
        "event_type": row.event_type,
        "enabled": row.enabled,
        "requires_confirmation": row.requires_confirmation,
        "cooldown_minutes": row.cooldown_minutes,
        "template": row.template,
        # Which context keys the emitter has to supply for this rule to fire.
        # Without it the only symptom is a suppression code days later.
        "required_context": _template_fields(row.template),
        "updated_at": _iso(row.updated_at),
    }


class ProactiveService:
    def __init__(self, session: Session, identity: Identity) -> None:
        self.session = session
        self.identity = identity
        self.audit = AuditRepository(session)

    def rules(self) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(ProactiveRule)
            .where(ProactiveRule.tenant_id == self.identity.tenant_id)
            .order_by(ProactiveRule.event_type)
        ).all()
        return [rule_view(row) for row in rows]

    def update_rule(
        self,
        event_type: str,
        changes: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Change what this tenant says when an event happens.

        The template is validated here rather than at delivery: a placeholder
        outside ``_CONTEXT_KEYS`` would make the rule suppress forever, and a
        positional one raises while formatting. Both are the author's mistake,
        so both are refused now, with the offending key named.
        """
        if not changes:
            raise ValidationAppError("No rule fields were supplied.")
        event = self._event(event_type)
        existing = self._rule_any(event)
        created = existing is None
        if created and "template" not in changes:
            raise ValidationAppError("A new rule needs a template to speak with.")
        row = existing if existing is not None else self._new_row(event)
        if created:
            self.session.add(row)

        if "template" in changes:
            row.template = self._template(changes["template"])
        if "enabled" in changes:
            enabled = changes["enabled"]
            if not isinstance(enabled, bool):
                raise ValidationAppError("enabled must be a boolean.")
            row.enabled = enabled
        if "cooldown_minutes" in changes:
            minutes = changes["cooldown_minutes"]
            if not isinstance(minutes, int) or isinstance(minutes, bool):
                raise ValidationAppError("cooldown_minutes must be an integer.")
            if not 0 <= minutes <= _COOLDOWN_MAX_MINUTES:
                raise ValidationAppError(
                    f"cooldown_minutes must be between 0 and {_COOLDOWN_MAX_MINUTES}."
                )
            row.cooldown_minutes = minutes

        row.updated_at = utcnow()
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ConflictError(
                "A rule for this event type already exists.",
                code="RULE_ALREADY_EXISTS",
            ) from None
        self._audit_rule(row, changes, created)
        self.session.commit()
        return rule_view(row)

    def _new_row(self, event: ProactiveEventType) -> ProactiveRule:
        return ProactiveRule(
            id=self._rule_id(event),
            tenant_id=self.identity.tenant_id,
            event_type=event.value,
            # A new rule always asks the customer first; the flag is not
            # writable through this surface.
            requires_confirmation=True,
            template="",
            enabled=True,
            cooldown_minutes=60,
        )

    def _event(self, event_type: str) -> ProactiveEventType:
        try:
            return ProactiveEventType(event_type)
        except ValueError as exc:
            raise ValidationAppError(
                "Unknown proactive event type.",
                code="UNKNOWN_PROACTIVE_EVENT",
                details={
                    "allowed": sorted(item.value for item in ProactiveEventType)
                },
            ) from exc

    def _template(self, value: Any) -> str:
        if not isinstance(value, str):
            raise ValidationAppError("template must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValidationAppError("template cannot be empty.")
        if len(cleaned) > _TEMPLATE_LIMIT:
            raise ValidationAppError(
                f"template must be at most {_TEMPLATE_LIMIT} characters."
            )
        try:
            parsed = list(Formatter().parse(cleaned))
        except ValueError as exc:
            raise ValidationAppError(
                "template has an unmatched brace or an invalid placeholder.",
                code="RULE_TEMPLATE_INVALID",
            ) from exc
        for _, field, _, _ in parsed:
            # ``parse`` reports the literal tail of the string as a None field,
            # so None means "no placeholder here" and empty means "{}".
            if field is None:
                continue
            if not field:
                raise ValidationAppError(
                    "template cannot use a positional placeholder like {}.",
                    code="RULE_TEMPLATE_INVALID",
                )
            if "." in field or "[" in field:
                raise ValidationAppError(
                    f"template placeholder '{field}' must be a plain context key.",
                    code="RULE_TEMPLATE_INVALID",
                )
            if field not in _CONTEXT_KEYS:
                raise ValidationAppError(
                    f"template uses an unknown context key '{field}'.",
                    code="RULE_TEMPLATE_UNKNOWN_KEY",
                    details={"allowed": sorted(_CONTEXT_KEYS)},
                )
        return cleaned

    def _rule_id(self, event: ProactiveEventType) -> str:
        """Fit tenant and event into the 40-character primary key.

        The column is short, so a long tenant name becomes a digest of itself
        instead of a truncation: two tenants sharing a prefix must not land on
        the same row. With the demo tenant this reproduces the seeded id.
        """
        tail = event.value.lower()
        stem = f"rule_{self.identity.tenant_id}"
        if len(stem) + 1 + len(tail) > 40:
            digest = hashlib.sha256(
                self.identity.tenant_id.encode("utf-8")
            ).hexdigest()[:8]
            stem = f"rule_{digest}"
        return f"{stem}_{tail}"

    def _rule_any(self, event: ProactiveEventType) -> ProactiveRule | None:
        return self.session.scalars(
            select(ProactiveRule).where(
                ProactiveRule.tenant_id == self.identity.tenant_id,
                ProactiveRule.event_type == event.value,
            )
        ).one_or_none()

    def emit(
        self,
        *,
        target_user_id: str,
        event_type: str,
        event_key: str,
        context: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        event = self._event(event_type)
        key = self._event_key(event_key)
        rule = self._rule(event)
        if rule is None:
            return self._suppressed(event, "NO_RULE")
        profile = self._profile(target_user_id)
        if profile is None or not profile.proactive_opt_in:
            return self._suppressed(event, "NO_CONSENT")
        if event.value in (profile.muted_event_types or []):
            return self._suppressed(event, "UNSUBSCRIBED")
        if self._duplicate(target_user_id, event, key) is not None:
            return self._suppressed(event, "DUPLICATE")
        if self._in_cooldown(target_user_id, event, rule):
            return self._suppressed(event, "COOLDOWN")
        if self._daily_count(target_user_id) >= profile.max_notifications_per_day:
            return self._suppressed(event, "FREQUENCY_CAP")

        clean = self._context(context)
        fields = {
            field
            for _, field, _, _ in Formatter().parse(rule.template)
            if field
        }
        if fields - clean.keys():
            return self._suppressed(event, "TEMPLATE_CONTEXT_MISSING")
        body = rule.template.format_map(_SafeContext(clean))[:500]
        status = (
            NotificationStatus.PENDING_CONFIRMATION.value
            if rule.requires_confirmation
            else NotificationStatus.CONFIRMED.value
        )
        row = Notification(
            id=f"ntf_{uuid.uuid4().hex[:16]}",
            tenant_id=self.identity.tenant_id,
            user_id=target_user_id,
            event_type=event.value,
            event_key=key,
            rule_id=rule.id,
            status=status,
            title=EVENT_TITLES[event.value],
            body=body,
            payload=dict(clean),
            confirmed_at=None if rule.requires_confirmation else utcnow(),
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            existing = self._duplicate(target_user_id, event, key)
            if existing is None:
                raise
            return self._suppressed(event, "DUPLICATE")
        if not rule.requires_confirmation:
            self._enqueue(row)
        self._audit(
            "proactive.emit",
            row.id,
            {"event_type": event.value, "status": status},
        )
        self.session.commit()
        return {
            "status": status,
            "reason": None,
            "notification": notification_view(row),
        }

    def list_notifications(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.session.scalars(
            select(Notification)
            .where(
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == self.identity.user_id,
            )
            .order_by(Notification.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).all()
        return [notification_view(row) for row in rows]

    def confirm(self, notification_id: str) -> dict[str, Any]:
        row = self._own(notification_id)
        if row.status == NotificationStatus.CANCELLED.value:
            raise ConflictError(
                "This notification was cancelled and cannot be confirmed.",
                code="NOTIFICATION_CANCELLED",
            )
        if row.status in _CONFIRMED_STATUSES:
            return notification_view(row)
        if row.status != NotificationStatus.PENDING_CONFIRMATION.value:
            raise ConflictError(
                "This notification cannot be confirmed.",
                code="NOTIFICATION_NOT_PENDING",
            )
        row.status = NotificationStatus.CONFIRMED.value
        row.confirmed_at = utcnow()
        self._enqueue(row)
        self._audit(
            "proactive.confirm",
            row.id,
            {"event_type": row.event_type},
        )
        self.session.commit()
        return notification_view(row)

    def cancel(self, notification_id: str) -> dict[str, Any]:
        row = self._own(notification_id)
        if row.status == NotificationStatus.CANCELLED.value:
            return notification_view(row)
        if row.status != NotificationStatus.PENDING_CONFIRMATION.value:
            raise ConflictError(
                "Only a notification awaiting confirmation can be cancelled.",
                code="NOTIFICATION_NOT_PENDING",
            )
        row.status = NotificationStatus.CANCELLED.value
        row.cancelled_at = utcnow()
        self._audit("proactive.cancel", row.id, {"event_type": row.event_type})
        self.session.commit()
        return notification_view(row)

    def _own(self, notification_id: str) -> Notification:
        row = self.session.scalars(
            select(Notification).where(
                Notification.id == notification_id,
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == self.identity.user_id,
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Notification not found.")
        return row

    def _rule(self, event: ProactiveEventType) -> ProactiveRule | None:
        return self.session.scalars(
            select(ProactiveRule).where(
                ProactiveRule.tenant_id == self.identity.tenant_id,
                ProactiveRule.event_type == event.value,
                ProactiveRule.enabled.is_(True),
            )
        ).one_or_none()

    def _profile(self, user_id: str) -> CustomerProfile | None:
        return self.session.scalars(
            select(CustomerProfile).where(
                CustomerProfile.tenant_id == self.identity.tenant_id,
                CustomerProfile.user_id == user_id,
            )
        ).one_or_none()

    def _duplicate(
        self,
        user_id: str,
        event: ProactiveEventType,
        key: str,
    ) -> Notification | None:
        return self.session.scalars(
            select(Notification).where(
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == user_id,
                Notification.event_type == event.value,
                Notification.event_key == key,
            )
        ).one_or_none()

    def _in_cooldown(
        self,
        user_id: str,
        event: ProactiveEventType,
        rule: ProactiveRule,
    ) -> bool:
        if rule.cooldown_minutes <= 0:
            return False
        since = utcnow() - timedelta(minutes=rule.cooldown_minutes)
        row = self.session.scalars(
            select(Notification)
            .where(
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == user_id,
                Notification.event_type == event.value,
                Notification.created_at >= since,
            )
            .limit(1)
        ).first()
        return row is not None

    def _daily_count(self, user_id: str) -> int:
        since = utcnow() - timedelta(hours=24)
        return int(
            self.session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.tenant_id == self.identity.tenant_id,
                    Notification.user_id == user_id,
                    Notification.created_at >= since,
                    Notification.status.in_(tuple(_ACTIVE_STATUSES)),
                )
            )
            or 0
        )

    def _event_key(self, value: str) -> str:
        if not isinstance(value, str):
            raise ValidationAppError("event_key must be a string.")
        key = value.strip()
        if not key or len(key) > 120:
            raise ValidationAppError("event_key must be 1 to 120 characters.")
        if redact(key) != key:
            raise ValidationAppError("event_key must not contain personal data.")
        return key

    def _context(self, raw: Mapping[str, Any] | None) -> dict[str, str]:
        if raw is None:
            return {}
        if not isinstance(raw, Mapping):
            raise ValidationAppError("context must be an object.")
        clean: dict[str, str] = {}
        for key, value in raw.items():
            if key not in _CONTEXT_KEYS:
                raise ValidationAppError(
                    f"Unsupported context key '{key}'."
                )
            if isinstance(value, bool) or value is None:
                clean[key] = str(value).lower()
            elif isinstance(value, (int, float)):
                clean[key] = str(value)
            elif isinstance(value, str):
                clean[key] = redact(value.strip()[:200])
            else:
                raise ValidationAppError(
                    f"context.{key} must be a scalar value."
                )
        return clean

    def _enqueue(self, row: Notification) -> None:
        target = Identity(
            tenant_id=row.tenant_id,
            user_id=row.user_id,
            roles=frozenset(),
        )
        OutboxService(self.session).enqueue(
            identity=target,
            aggregate_type="notification",
            aggregate_id=row.id,
            event_type="proactive.confirmed",
            dedupe_key=f"proactive:{row.id}",
            payload={
                "notification_id": row.id,
                "event_type": row.event_type,
                "title": row.title,
            },
        )

    def _suppressed(self, event: ProactiveEventType, reason: str) -> dict[str, Any]:
        self.session.rollback()
        self._audit(
            "proactive.suppressed",
            "none",
            {"event_type": event.value, "reason": reason},
        )
        self.session.commit()
        return {"status": "SUPPRESSED", "reason": reason, "notification": None}

    def _audit_rule(
        self,
        row: ProactiveRule,
        changes: Mapping[str, Any],
        created: bool,
    ) -> None:
        """Record which rule changed, and prove which template took effect.

        The words themselves stay out of the audit trail: a notification body
        can carry an order number or an amount, and duplicating it into
        ``audit_events`` would widen where that text can leak. The digest is
        enough to answer "was it this version that was live".
        """
        self._audit(
            "proactive.rule.update",
            row.id,
            {
                "event_type": row.event_type,
                "fields": sorted(changes),
                "enabled": row.enabled,
                "cooldown_minutes": row.cooldown_minutes,
                "template_sha256": hashlib.sha256(
                    row.template.encode("utf-8")
                ).hexdigest(),
                "created": created,
            },
            resource_type="proactive_rule",
        )

    def _audit(
        self,
        action: str,
        resource_id: str,
        detail: dict[str, Any],
        *,
        resource_type: str = "notification",
    ) -> None:
        self.audit.record(
            actor_id=self.identity.user_id,
            tenant_id=self.identity.tenant_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="SUCCESS",
            detail=detail,
        )
