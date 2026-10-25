"""Input-side detection for prompt injection, policy bypass and PII.

The guard is deliberately observational in this stage: a hit is recorded as a
security event, but it never changes routing, eligibility or any other
backend rule. The deterministic workflow already ignores instructions that
arrive inside a customer message; the guard exists so those attempts become
visible instead of silently disappearing into the conversation log.

Excerpts are masked with the same rules that protect logs and traces. The raw
message is never copied into a security event.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.observability.redaction import digest, redact

__all__ = [
    "GuardFinding",
    "SecurityAction",
    "SecurityCategory",
    "SecuritySeverity",
    "safe_excerpt",
    "scan_input",
]


class SecurityCategory(StrEnum):
    PROMPT_INJECTION = "PROMPT_INJECTION"
    POLICY_BYPASS = "POLICY_BYPASS"
    SENSITIVE_CONTENT = "SENSITIVE_CONTENT"
    OUTPUT_GROUNDING = "OUTPUT_GROUNDING"
    RATE_LIMIT = "RATE_LIMIT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"


class SecuritySeverity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class SecurityAction(StrEnum):
    LOGGED = "LOGGED"
    REDACTED = "REDACTED"
    BLOCKED = "BLOCKED"


_SEVERITY_RANK = {
    SecuritySeverity.LOW: 0,
    SecuritySeverity.MEDIUM: 1,
    SecuritySeverity.HIGH: 2,
}


@dataclass(frozen=True, slots=True)
class GuardFinding:
    rule_id: str
    category: SecurityCategory
    severity: SecuritySeverity
    action: SecurityAction
    excerpt: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class _Rule:
    id: str
    category: SecurityCategory
    severity: SecuritySeverity
    pattern: re.Pattern[str]


_RULES: tuple[_Rule, ...] = (
    _Rule(
        "injection.ignore_instructions",
        SecurityCategory.PROMPT_INJECTION,
        SecuritySeverity.HIGH,
        re.compile(
            r"(?:忽略|无视|忘记|跳过|不要遵守|别管|不用管).{0,16}"
            r"(?:之前|以上|上面|前面|所有|系统|原有).{0,12}"
            r"(?:指令|规则|提示|要求|约束|设定)|"
            r"(?:ignore|disregard|forget|override).{0,20}"
            r"(?:previous|above|prior|system).{0,20}"
            r"(?:instructions|prompts|rules)",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        "injection.system_prompt",
        SecurityCategory.PROMPT_INJECTION,
        SecuritySeverity.HIGH,
        re.compile(
            r"(?:系统提示词|系统指令|系统消息|初始指令|你的指令|"
            r"system\s*prompt|developer\s*message)",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        "injection.role_override",
        SecurityCategory.PROMPT_INJECTION,
        SecuritySeverity.HIGH,
        re.compile(
            r"(?:你现在是|从现在开始你是|扮演|假装你是|进入.{0,6}模式|"
            r"开发者模式|越狱|jailbreak|\bDAN\b)",
            re.IGNORECASE,
        ),
    ),
    _Rule(
        "bypass.policy_claim",
        SecurityCategory.POLICY_BYPASS,
        SecuritySeverity.MEDIUM,
        re.compile(
            r"(?:你们|平台|公司|客服|官方)?.{0,8}"
            r"(?:政策|规则|规定|条款).{0,10}"
            r"(?:允许|支持|同意|承诺|已经).{0,10}"
            r"(?:全额退款|全额退|退款|赔付)"
        ),
    ),
    _Rule(
        "bypass.approval",
        SecurityCategory.POLICY_BYPASS,
        SecuritySeverity.HIGH,
        re.compile(r"(?:绕过|跳过|不用|无需|免去|取消).{0,8}(?:审批|审核|人工|复核)"),
    ),
    _Rule(
        "bypass.authority",
        SecurityCategory.POLICY_BYPASS,
        SecuritySeverity.HIGH,
        re.compile(
            r"(?:我是|作为).{0,8}(?:管理员|老板|上级|主管|内部人员|官方人员)|"
            r"(?:给我|帮我).{0,6}(?:特批|直接退|马上退)"
        ),
    ),
    _Rule(
        "bypass.prior_promise",
        SecurityCategory.POLICY_BYPASS,
        SecuritySeverity.MEDIUM,
        re.compile(
            r"(?:你|你们|客服).{0,10}(?:答应|承诺|说过|同意过).{0,12}"
            r"(?:退款|全额|赔偿|赔付)"
        ),
    ),
)

_SENSITIVE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("phone", re.compile(r"(?<!\d)(?:\+?86[-\s]?)?1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    (
        "id_card",
        re.compile(
            r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
            r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"
        ),
    ),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)")),
    (
        "credential",
        re.compile(
            r"(?:密码|口令|验证码|短信码|api[_\s-]?key|access[_\s-]?token)"
            r"\s*(?:是|为|[:：=])?\s*\S+",
            re.IGNORECASE,
        ),
    ),
)


def safe_excerpt(text: str, *, max_chars: int = 160) -> str:
    """Mask PII, collapse whitespace and cap the length of a stored excerpt."""
    masked = " ".join(redact(text).split())
    if len(masked) <= max_chars:
        return masked
    return f"{masked[:max_chars]}…"


def scan_input(message: str) -> list[GuardFinding]:
    """Classify one customer message; at most one finding per category."""
    if not message:
        return []
    best: dict[SecurityCategory, GuardFinding] = {}

    matched_rules: dict[SecurityCategory, list[str]] = {}
    for rule in _RULES:
        match = rule.pattern.search(message)
        if match is None:
            continue
        matched_rules.setdefault(rule.category, []).append(rule.id)
        current = best.get(rule.category)
        if current is None or (
            _SEVERITY_RANK[rule.severity] > _SEVERITY_RANK[current.severity]
        ):
            best[rule.category] = GuardFinding(
                rule_id=rule.id,
                category=rule.category,
                severity=rule.severity,
                action=SecurityAction.LOGGED,
                excerpt=safe_excerpt(match.group(0), max_chars=120),
                details={},
            )

    for category, rule_ids in matched_rules.items():
        finding = best[category]
        best[category] = GuardFinding(
            rule_id=finding.rule_id,
            category=finding.category,
            severity=finding.severity,
            action=finding.action,
            excerpt=finding.excerpt,
            details={"rules": rule_ids},
        )

    sensitive_kinds = [
        name for name, pattern in _SENSITIVE if pattern.search(message)
    ]
    if sensitive_kinds:
        best[SecurityCategory.SENSITIVE_CONTENT] = GuardFinding(
            rule_id=f"pii.{sensitive_kinds[0]}",
            category=SecurityCategory.SENSITIVE_CONTENT,
            severity=SecuritySeverity.MEDIUM,
            action=SecurityAction.LOGGED,
            excerpt=safe_excerpt(message),
            details={"kinds": sensitive_kinds, "digest": digest(message)},
        )

    return list(best.values())
