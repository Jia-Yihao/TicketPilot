"""Customer profile memory with a narrow, audited write surface.

The agent never writes here. A profile is created only by the customer's own
PUT, and only allowlisted preference keys plus a masked history summary are
stored. Reads, exports and deletes leave an audit row that records which
fields were touched, never their values. ``contact_channel`` is the one
preference the delivery path reads, and it is a channel name rather than an
address: where a message actually goes is resolved from server-side
configuration, never from this row or the queued payload.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.core.security import Identity
from app.core.time import utcnow
from app.models.enums import Channel, OutboxStatus, ProactiveEventType
from app.models.memory import CustomerProfile, Notification
from app.models.outbox import OutboxMessage
from app.observability.redaction import redact
from app.repositories.audit import AuditRepository

__all__ = ["MemoryService", "notification_view", "profile_view"]

_PREFERENCE_KEYS = frozenset(
    {"contact_channel", "contact_window", "product_interests", "tone"}
)
_TONES = frozenset({"concise", "detailed"})
_LOCALE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?$")
_HISTORY_LIMIT = 500


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def profile_view(profile: CustomerProfile | None) -> dict[str, Any]:
    if profile is None:
        return {
            "preferred_name": None,
            "locale": "zh-CN",
            "preferences": {},
            "history_summary": "",
            "proactive_opt_in": False,
            "muted_event_types": [],
            "max_notifications_per_day": 3,
            "updated_at": None,
        }
    return {
        "preferred_name": profile.preferred_name,
        "locale": profile.locale,
        "preferences": dict(profile.preferences or {}),
        "history_summary": redact(profile.history_summary or ""),
        "proactive_opt_in": profile.proactive_opt_in,
        "muted_event_types": list(profile.muted_event_types or []),
        "max_notifications_per_day": profile.max_notifications_per_day,
        "updated_at": _iso(profile.updated_at),
    }


class MemoryService:
    def __init__(self, session: Session, identity: Identity) -> None:
        self.session = session
        self.identity = identity
        self.audit = AuditRepository(session)

    def get(self) -> dict[str, Any]:
        profile = self._find()
        self._audit(
            "memory.read",
            profile,
            {"fields": ["profile"]},
        )
        self.session.commit()
        return profile_view(profile)

    def update(self, changes: Mapping[str, Any]) -> dict[str, Any]:
        if not changes:
            raise ValidationAppError("No memory fields were supplied.")
        profile = self._find()
        if profile is None:
            profile = CustomerProfile(
                id=f"mem_{uuid.uuid4().hex[:16]}",
                tenant_id=self.identity.tenant_id,
                user_id=self.identity.user_id,
                preferences={},
                history_summary="",
                proactive_opt_in=False,
                muted_event_types=[],
                max_notifications_per_day=3,
            )
            self.session.add(profile)
            self.session.flush()

        if "preferred_name" in changes:
            value = changes["preferred_name"]
            profile.preferred_name = (
                None
                if value is None
                else self._clean_text(
                    value, "preferred_name", max_length=40, reject_pii=True
                )
            )
        if "locale" in changes:
            locale = self._clean_text(
                changes["locale"], "locale", max_length=16, reject_pii=False
            )
            if not _LOCALE.match(locale):
                raise ValidationAppError("locale must look like zh-CN or en.")
            profile.locale = locale
        if "preferences" in changes:
            profile.preferences = self._merge_preferences(
                profile.preferences or {}, changes["preferences"]
            )
        if "history_summary" in changes:
            summary = changes["history_summary"]
            if summary is None:
                summary = ""
            if not isinstance(summary, str):
                raise ValidationAppError("history_summary must be a string.")
            if len(summary) > _HISTORY_LIMIT:
                raise ValidationAppError(
                    f"history_summary must be at most {_HISTORY_LIMIT} characters."
                )
            # Mask rather than reject: the customer may legitimately mention
            # a number, but the stored copy must never carry it.
            profile.history_summary = redact(summary)
        if "proactive_opt_in" in changes:
            value = changes["proactive_opt_in"]
            if not isinstance(value, bool):
                raise ValidationAppError("proactive_opt_in must be a boolean.")
            profile.proactive_opt_in = value
        if "muted_event_types" in changes:
            profile.muted_event_types = self._muted(changes["muted_event_types"])
        if "max_notifications_per_day" in changes:
            value = changes["max_notifications_per_day"]
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValidationAppError(
                    "max_notifications_per_day must be an integer."
                )
            if not 1 <= value <= 20:
                raise ValidationAppError(
                    "max_notifications_per_day must be between 1 and 20."
                )
            profile.max_notifications_per_day = value

        profile.updated_at = utcnow()
        self._audit("memory.update", profile, {"fields": sorted(changes)})
        self.session.commit()
        return profile_view(profile)

    def delete(self) -> None:
        profile = self._find()
        self.session.execute(
            sa_update(OutboxMessage)
            .where(
                OutboxMessage.tenant_id == self.identity.tenant_id,
                OutboxMessage.user_id == self.identity.user_id,
                OutboxMessage.event_type == "proactive.confirmed",
                OutboxMessage.status.in_(
                    (OutboxStatus.PENDING.value, OutboxStatus.PROCESSING.value)
                ),
            )
            .values(status=OutboxStatus.CANCELLED.value, updated_at=utcnow())
        )
        self.session.execute(
            sa_delete(Notification).where(
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == self.identity.user_id,
            )
        )
        if profile is not None:
            self._audit("memory.delete", profile, {"fields": ["profile"]})
            self.session.delete(profile)
        self.session.commit()

    def export(self) -> dict[str, Any]:
        profile = self._find()
        rows = self.session.execute(
            select(Notification.status, func.count(Notification.id))
            .where(
                Notification.tenant_id == self.identity.tenant_id,
                Notification.user_id == self.identity.user_id,
            )
            .group_by(Notification.status)
        ).all()
        counts = {status: int(count) for status, count in rows}
        self._audit("memory.export", profile, {"counts": True})
        self.session.commit()
        return {
            "profile": profile_view(profile),
            "notification_counts": counts,
            "redacted": True,
        }

    def _find(self) -> CustomerProfile | None:
        return self.session.scalars(
            select(CustomerProfile).where(
                CustomerProfile.tenant_id == self.identity.tenant_id,
                CustomerProfile.user_id == self.identity.user_id,
            )
        ).one_or_none()

    def _audit(
        self,
        action: str,
        profile: CustomerProfile | None,
        detail: dict[str, Any],
    ) -> None:
        self.audit.record(
            actor_id=self.identity.user_id,
            tenant_id=self.identity.tenant_id,
            action=action,
            resource_type="customer_profile",
            resource_id=profile.id if profile is not None else "none",
            outcome="SUCCESS",
            detail=detail,
        )

    def _clean_text(
        self,
        value: Any,
        field: str,
        *,
        max_length: int,
        reject_pii: bool,
    ) -> str:
        if not isinstance(value, str):
            raise ValidationAppError(f"{field} must be a string.")
        cleaned = value.strip()
        if not cleaned:
            raise ValidationAppError(f"{field} cannot be empty.")
        if len(cleaned) > max_length:
            raise ValidationAppError(
                f"{field} must be at most {max_length} characters."
            )
        if reject_pii and redact(cleaned) != cleaned:
            raise ValidationAppError(
                f"{field} looks like personal contact or payment data and is not stored."
            )
        return cleaned

    def _merge_preferences(
        self,
        current: Mapping[str, Any],
        incoming: Any,
    ) -> dict[str, Any]:
        if not isinstance(incoming, Mapping):
            raise ValidationAppError("preferences must be an object.")
        merged = dict(current)
        for key, value in incoming.items():
            if key not in _PREFERENCE_KEYS:
                raise ValidationAppError(
                    f"Unsupported preference '{key}'. Allowed: "
                    + ", ".join(sorted(_PREFERENCE_KEYS))
                    + "."
                )
            if value is None:
                # Clearing a preference removes it; an empty string is not a
                # value any of these fields accept.
                merged.pop(key, None)
                continue
            if key == "product_interests":
                merged[key] = self._interests(value)
            elif key == "tone":
                if not isinstance(value, str) or value not in _TONES:
                    raise ValidationAppError(
                        "tone must be one of: " + ", ".join(sorted(_TONES)) + "."
                    )
                merged[key] = value
            elif key == "contact_channel":
                merged[key] = self._contact_channel(value)
            else:
                merged[key] = self._clean_text(
                    value, key, max_length=40, reject_pii=True
                )
        return merged

    def _contact_channel(self, value: Any) -> str:
        """A channel name only. An address looks like PII and is rejected here."""
        allowed = {item.value for item in Channel}
        if not isinstance(value, str) or value.strip().upper() not in allowed:
            raise ValidationAppError(
                "contact_channel must be one of: " + ", ".join(sorted(allowed)) + "."
            )
        return value.strip().upper()

    def _interests(self, value: Any) -> list[str]:
        if not isinstance(value, list) or len(value) > 10:
            raise ValidationAppError(
                "product_interests must be a list of at most 10 items."
            )
        interests: list[str] = []
        for item in value:
            interests.append(
                self._clean_text(
                    item, "product_interests", max_length=40, reject_pii=True
                )
            )
        return interests

    def _muted(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            raise ValidationAppError("muted_event_types must be a list.")
        allowed = {item.value for item in ProactiveEventType}
        muted: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in allowed:
                raise ValidationAppError(
                    "muted_event_types may only contain: "
                    + ", ".join(sorted(allowed))
                    + "."
                )
            if item not in muted:
                muted.append(item)
        return sorted(muted)


def notification_view(row: Notification) -> dict[str, Any]:
    return {
        "notification_id": row.id,
        "event_type": row.event_type,
        "status": row.status,
        "title": row.title,
        "body": redact(row.body),
        "created_at": _iso(row.created_at),
        "confirmed_at": _iso(row.confirmed_at),
        "cancelled_at": _iso(row.cancelled_at),
    }
