"""事件触发的主动告知：提案要客户点头才可能发出去。

链路是定死的：一个事件类型只对应该租户现役的那一条规则，规则模板只从白名单
上下文取值渲染，渲染出来是一条等客户处理的 ``Notification``。客户确认时才在
同一事务里写外发队列行——服务发不出任何客户没点过的东西。

两条取舍写在这里，免得被后来人「优化」掉：规则的文案改动只能走提案，且必须
第二个人批准、提交人不能自批，因为文案就是客户收到的那些字；事件类型是代码的
所有物，``ProactiveEventType`` 里没有的类型在第一步就被拒，服务不能自己发明
消息、也不能自己发明触发器。

边界：本模块只管「说不说、说什么、要不要等确认」，怎么送到用户手上是
``app/channels/outbound.py`` 的事。它不碰任务与退款，也不读客户画像与规则表
之外的业务表；调用方是 ``app/api/v1/memory.py``，运行时只在对账队列里通过
``ProactiveSender`` 碰到已经确认过的那条通知。
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

from app.core.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationAppError,
)
from app.core.security import Identity, require_approver
from app.core.time import utcnow
from app.models.enums import (
    NotificationStatus,
    ProactiveEventType,
)
from app.models.memory import (
    CustomerProfile,
    Notification,
    ProactiveRule,
    ProactiveRuleChange,
    ProactiveRuleVersion,
)
from app.observability.redaction import redact
from app.repositories.audit import AuditRepository
from app.services.memory import notification_view
from app.services.outbox import OutboxService

__all__ = [
    "EVENT_TITLES",
    "ProactiveService",
    "change_view",
    "rule_view",
    "version_view",
]

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
_CHANGE_FIELDS = ("template", "enabled", "cooldown_minutes")
_CHANGE_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED"})
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


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


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
        "version": row.version,
        "updated_at": _iso(row.updated_at),
    }


def change_view(row: ProactiveRuleChange) -> dict[str, Any]:
    return {
        "change_id": row.id,
        "event_type": row.event_type,
        "kind": row.kind,
        "status": row.status,
        "base_version": row.base_version,
        "target_version": row.target_version,
        "template": row.template,
        "enabled": row.enabled,
        "requires_confirmation": row.requires_confirmation,
        "cooldown_minutes": row.cooldown_minutes,
        "required_context": _template_fields(row.template),
        "old_template": row.old_template,
        "old_enabled": row.old_enabled,
        "old_cooldown_minutes": row.old_cooldown_minutes,
        "reason": row.reason,
        "submitted_by": row.submitted_by,
        "submitted_at": _iso(row.submitted_at),
        "decided_by": row.decided_by,
        "decided_at": _iso(row.decided_at),
        "decision_note": row.decision_note,
        "applied_version": row.applied_version,
    }


def version_view(row: ProactiveRuleVersion) -> dict[str, Any]:
    return {
        "event_type": row.event_type,
        "version": row.version,
        "template": row.template,
        "enabled": row.enabled,
        "requires_confirmation": row.requires_confirmation,
        "cooldown_minutes": row.cooldown_minutes,
        "change_id": row.change_id,
        "submitted_by": row.submitted_by,
        "approved_by": row.approved_by,
        "reason": row.reason,
        "created_at": _iso(row.created_at),
    }


class ProactiveService:
    def __init__(self, session: Session, identity: Identity) -> None:
        self.session = session
        self.identity = identity
        self.audit = AuditRepository(session)

    def rules(self) -> list[dict[str, Any]]:
        """列本租户现役的主动规则，按事件类型排序；只读。"""
        rows = self.session.scalars(
            select(ProactiveRule)
            .where(ProactiveRule.tenant_id == self.identity.tenant_id)
            .order_by(ProactiveRule.event_type)
        ).all()
        return [rule_view(row) for row in rows]

    def submit_change(
        self,
        event_type: str,
        changes: Mapping[str, Any],
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Propose a new rule state; nothing takes effect here.

        The template is validated here rather than at delivery: a placeholder
        outside ``_CONTEXT_KEYS`` would make the rule suppress forever, and a
        positional one raises while formatting. Both are the author's mistake,
        so both are refused now, with the offending key named.
        """
        fields = dict(changes)
        if not fields:
            raise ValidationAppError("No rule fields were supplied.")
        event = self._event(event_type)
        existing = self._rule_any(event)
        if existing is None and "template" not in fields:
            raise ValidationAppError("A new rule needs a template to speak with.")
        target = self._target_state(existing, fields)
        self._require_no_pending(event)
        change = self._new_change(
            event,
            existing,
            target,
            kind="UPDATE",
            target_version=None,
            reason=reason,
        )
        self._audit(
            "proactive.rule.submit",
            existing.id if existing is not None else self._rule_id(event),
            {
                "event_type": event.value,
                "change_id": change.id,
                "kind": change.kind,
                "base_version": change.base_version,
                "fields": sorted(fields),
                "enabled": change.enabled,
                "cooldown_minutes": change.cooldown_minutes,
                "template_sha256": _digest(change.template),
                "reason_sha256": _digest(change.reason) if change.reason else None,
                "target_version": change.target_version,
            },
            resource_type="proactive_rule",
        )
        self.session.commit()
        return change_view(change)

    def approve_change(self, change_id: str, *, note: str | None = None) -> dict[str, Any]:
        """Apply a pending change, but only from a second pair of eyes."""
        require_approver(self.identity)
        change = self._own_change(change_id)
        self._require_pending(change)
        if self.identity.user_id == change.submitted_by:
            raise ForbiddenError(
                "Self-approval is not allowed.", code="RULE_SELF_APPROVAL"
            )
        event = self._event(change.event_type)
        existing = self._rule_any(event)
        current_version = existing.version if existing is not None else 0
        if current_version != change.base_version:
            raise ConflictError(
                "The rule changed after this proposal was submitted.",
                code="RULE_VERSION_CONFLICT",
                details={
                    "base_version": change.base_version,
                    "current_version": current_version,
                },
            )

        now = utcnow()
        previous_template = existing.template if existing is not None else None
        if existing is None:
            rule = self._new_row(event)
            self.session.add(rule)
        else:
            rule = existing
        rule.template = change.template
        rule.enabled = change.enabled
        rule.cooldown_minutes = change.cooldown_minutes
        rule.requires_confirmation = change.requires_confirmation
        rule.version = current_version + 1
        rule.updated_at = now
        version = ProactiveRuleVersion(
            id=f"prv_{uuid.uuid4().hex[:16]}",
            tenant_id=self.identity.tenant_id,
            event_type=event.value,
            version=rule.version,
            template=rule.template,
            enabled=rule.enabled,
            requires_confirmation=rule.requires_confirmation,
            cooldown_minutes=rule.cooldown_minutes,
            change_id=change.id,
            submitted_by=change.submitted_by,
            approved_by=self.identity.user_id,
            reason=change.reason,
            created_at=now,
        )
        self.session.add(version)
        change.status = "APPROVED"
        change.pending_key = None
        change.decided_by = self.identity.user_id
        change.decided_at = now
        change.decision_note = self._note(note)
        change.applied_version = rule.version
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ConflictError(
                "The rule changed while this approval was being written.",
                code="RULE_VERSION_CONFLICT",
            ) from None
        self._audit(
            "proactive.rule.approve",
            rule.id,
            {
                "event_type": event.value,
                "change_id": change.id,
                "kind": change.kind,
                "from_version": current_version,
                "to_version": rule.version,
                "target_version": change.target_version,
                "submitted_by": change.submitted_by,
                "approved_by": self.identity.user_id,
                "fields": sorted(_CHANGE_FIELDS),
                "enabled": rule.enabled,
                "cooldown_minutes": rule.cooldown_minutes,
                "template_sha256": _digest(rule.template),
                "previous_template_sha256": (
                    _digest(previous_template) if previous_template is not None else None
                ),
            },
            resource_type="proactive_rule",
        )
        self.session.commit()
        return change_view(change)

    def reject_change(self, change_id: str, *, note: str | None = None) -> dict[str, Any]:
        """驳回一条提案，规则保持原样；提交人不能审自己那条。"""
        require_approver(self.identity)
        change = self._own_change(change_id)
        self._require_pending(change)
        if self.identity.user_id == change.submitted_by:
            raise ForbiddenError(
                "Self-approval is not allowed.", code="RULE_SELF_APPROVAL"
            )
        change.status = "REJECTED"
        change.pending_key = None
        change.decided_by = self.identity.user_id
        change.decided_at = utcnow()
        change.decision_note = self._note(note)
        event = self._event(change.event_type)
        existing = self._rule_any(event)
        self._audit(
            "proactive.rule.reject",
            existing.id if existing is not None else self._rule_id(event),
            {
                "event_type": change.event_type,
                "change_id": change.id,
                "kind": change.kind,
                "from_version": change.base_version,
                "to_version": None,
                "target_version": change.target_version,
                "submitted_by": change.submitted_by,
                "rejected_by": self.identity.user_id,
                "reason_sha256": _digest(change.decision_note)
                if change.decision_note
                else None,
            },
            resource_type="proactive_rule",
        )
        self.session.commit()
        return change_view(change)

    def submit_rollback(
        self,
        event_type: str,
        version: int,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Propose restoring a version; the same approval path still applies."""
        event = self._event(event_type)
        existing = self._rule_any(event)
        if existing is None:
            raise NotFoundError("Rule not found.")
        snapshot = self.session.scalars(
            select(ProactiveRuleVersion).where(
                ProactiveRuleVersion.tenant_id == self.identity.tenant_id,
                ProactiveRuleVersion.event_type == event.value,
                ProactiveRuleVersion.version == version,
            )
        ).one_or_none()
        if snapshot is None:
            raise NotFoundError("Rule version not found.")
        if snapshot.version == existing.version:
            raise ConflictError(
                "The rule is already at that version.",
                code="RULE_ALREADY_AT_VERSION",
                details={"version": snapshot.version},
            )
        self._require_no_pending(event)
        target = {
            "template": snapshot.template,
            "enabled": snapshot.enabled,
            "cooldown_minutes": snapshot.cooldown_minutes,
            "requires_confirmation": snapshot.requires_confirmation,
        }
        change = self._new_change(
            event,
            existing,
            target,
            kind="ROLLBACK",
            target_version=snapshot.version,
            reason=reason,
        )
        self._audit(
            "proactive.rule.submit",
            existing.id,
            {
                "event_type": event.value,
                "change_id": change.id,
                "kind": change.kind,
                "base_version": change.base_version,
                "fields": sorted(_CHANGE_FIELDS),
                "enabled": change.enabled,
                "cooldown_minutes": change.cooldown_minutes,
                "template_sha256": _digest(change.template),
                "reason_sha256": _digest(change.reason) if change.reason else None,
                "target_version": change.target_version,
            },
            resource_type="proactive_rule",
        )
        self.session.commit()
        return change_view(change)

    def changes(self, *, status: str | None = None) -> list[dict[str, Any]]:
        """列规则提案，最新的在前；状态过滤只接受三种值。"""
        if status is not None and status not in _CHANGE_STATUSES:
            raise ValidationAppError(
                "Unknown change status.",
                details={"allowed": sorted(_CHANGE_STATUSES)},
            )
        query = select(ProactiveRuleChange).where(
            ProactiveRuleChange.tenant_id == self.identity.tenant_id
        )
        if status is not None:
            query = query.where(ProactiveRuleChange.status == status)
        rows = self.session.scalars(
            query.order_by(ProactiveRuleChange.submitted_at.desc())
        ).all()
        return [change_view(row) for row in rows]

    def change(self, change_id: str) -> dict[str, Any]:
        """读一条提案；属于别的租户与不存在都报 not found。"""
        return change_view(self._own_change(change_id))

    def versions(self, event_type: str) -> list[dict[str, Any]]:
        """列某个事件类型的规则历史版本，新的在前；只读。"""
        event = self._event(event_type)
        rows = self.session.scalars(
            select(ProactiveRuleVersion)
            .where(
                ProactiveRuleVersion.tenant_id == self.identity.tenant_id,
                ProactiveRuleVersion.event_type == event.value,
            )
            .order_by(ProactiveRuleVersion.version.desc())
        ).all()
        return [version_view(row) for row in rows]

    def _target_state(
        self,
        existing: ProactiveRule | None,
        fields: Mapping[str, Any],
    ) -> dict[str, Any]:
        if "template" in fields:
            template = self._template(fields["template"])
        elif existing is not None:
            template = existing.template
        else:
            raise ValidationAppError("A new rule needs a template to speak with.")
        enabled = (
            self._enabled(fields["enabled"])
            if "enabled" in fields
            else (existing.enabled if existing is not None else True)
        )
        cooldown = (
            self._cooldown(fields["cooldown_minutes"])
            if "cooldown_minutes" in fields
            else (existing.cooldown_minutes if existing is not None else 60)
        )
        return {
            "template": template,
            "enabled": enabled,
            "cooldown_minutes": cooldown,
            "requires_confirmation": (
                existing.requires_confirmation if existing is not None else True
            ),
        }

    def _new_change(
        self,
        event: ProactiveEventType,
        existing: ProactiveRule | None,
        target: Mapping[str, Any],
        *,
        kind: str,
        target_version: int | None,
        reason: str | None,
    ) -> ProactiveRuleChange:
        change = ProactiveRuleChange(
            id=f"chg_{uuid.uuid4().hex[:16]}",
            tenant_id=self.identity.tenant_id,
            event_type=event.value,
            kind=kind,
            status="PENDING",
            pending_key=event.value,
            base_version=existing.version if existing is not None else 0,
            target_version=target_version,
            template=target["template"],
            enabled=target["enabled"],
            requires_confirmation=target["requires_confirmation"],
            cooldown_minutes=target["cooldown_minutes"],
            old_template=existing.template if existing is not None else None,
            old_enabled=existing.enabled if existing is not None else None,
            old_cooldown_minutes=(
                existing.cooldown_minutes if existing is not None else None
            ),
            reason=self._text(reason, field="reason"),
            submitted_by=self.identity.user_id,
            submitted_at=utcnow(),
        )
        self.session.add(change)
        try:
            self.session.flush()
        except IntegrityError:
            self.session.rollback()
            raise ConflictError(
                "A change for this event type is already waiting for approval.",
                code="RULE_CHANGE_PENDING",
            ) from None
        return change

    def _require_no_pending(self, event: ProactiveEventType) -> None:
        pending = self.session.scalars(
            select(ProactiveRuleChange).where(
                ProactiveRuleChange.tenant_id == self.identity.tenant_id,
                ProactiveRuleChange.event_type == event.value,
                ProactiveRuleChange.status == "PENDING",
            )
        ).one_or_none()
        if pending is not None:
            raise ConflictError(
                "A change for this event type is already waiting for approval.",
                code="RULE_CHANGE_PENDING",
                details={"change_id": pending.id},
            )

    def _own_change(self, change_id: str) -> ProactiveRuleChange:
        row = self.session.scalars(
            select(ProactiveRuleChange).where(
                ProactiveRuleChange.id == change_id,
                ProactiveRuleChange.tenant_id == self.identity.tenant_id,
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Rule change not found.")
        return row

    def _require_pending(self, change: ProactiveRuleChange) -> None:
        if change.status != "PENDING":
            raise ConflictError(
                f"This change is already {change.status}.",
                code="RULE_CHANGE_NOT_PENDING",
                details={"status": change.status},
            )

    def _enabled(self, value: Any) -> bool:
        if not isinstance(value, bool):
            raise ValidationAppError("enabled must be a boolean.")
        return value

    def _cooldown(self, value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationAppError("cooldown_minutes must be an integer.")
        if not 0 <= value <= _COOLDOWN_MAX_MINUTES:
            raise ValidationAppError(
                f"cooldown_minutes must be between 0 and {_COOLDOWN_MAX_MINUTES}."
            )
        return value

    def _text(self, value: Any, *, field: str) -> str:
        if value is None:
            return ""
        if not isinstance(value, str):
            raise ValidationAppError(f"{field} must be a string.")
        cleaned = value.strip()
        if len(cleaned) > 200:
            raise ValidationAppError(f"{field} must be at most 200 characters.")
        return cleaned

    def _note(self, note: str | None) -> str:
        return self._text(note, field="note")

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
            version=1,
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
        """喂一个事实进去，由规则决定说不说；被压下的每一次都带理由回来。

        顺序即判据：没有规则、没有同意、已退订、重复、冷却中、超过每日上限、
        模板缺上下文——任何一条命中都只记一条抑制记录，不落通知。
        """
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
            rule_version=rule.version,
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
        """列调用者自己的通知，最新的在前；不返回别人那条。"""
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
        """客户确认一条待确认通知；确认与入队在同一事务里完成。"""
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
        """撤销一条还没发出的通知；已发出的撤不回来，只能让客户退订。"""
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
