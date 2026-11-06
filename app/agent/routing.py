from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from app.agent.refund_reason_rules import match_refund_reason
from app.core.money import parse_yuan_to_cents
from app.models.enums import Intent, RefundReason, RefundType
from app.sql.planner import asks_for_aggregate, names_dataset

if TYPE_CHECKING:
    from app.agent.intent_catalog import IntentSpec


def ruleset_fingerprint() -> str:
    """A digest of the three code files below, and only those.

    A model decision is only reusable against the rule set that produced it.
    Hashing the source means a code change invalidates the record without
    anyone remembering to bump a version. The write-intent cues that now live
    in app/agent/intents.yaml are not covered here, so editing that data leaves
    cached decisions valid -- a known, registered gap.
    """
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parents[1]
    for path in (
        Path(__file__).resolve(),
        root / "sql" / "planner.py",
        root / "core" / "money.py",
    ):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


# Read once: the process serves one rule set, and a record from another
# process is rejected unless it was produced against the same bytes.
RULESET_FINGERPRINT = ruleset_fingerprint()

_ORDER_ID = re.compile(r"ORD-[A-Za-z0-9-]+", re.IGNORECASE)
_AMOUNT = re.compile(r"(?:¥\s*)?(\d+(?:\.\d{1,2})?)\s*(?:元|块|rmb|cny)", re.IGNORECASE)

_REFUND_ELIGIBILITY = (
    "是否符合退款条件",
    "退款条件",
    "退款规则",
    "退款政策",
    "能不能退款",
    "可以退吗",
    "能退吗",
    "为什么不能退款",
    "为什么无法退款",
)
_LOGISTICS = ("物流", "快递", "运单", "到哪", "配送", "延迟", "没收到", "发货")
_POLICY = ("政策", "规则", "条件", "期限", "售后", "退货", "保修", "流程")
_ORDER = ("订单", "order")

# 疑问标记：用户在问规则，不是在要动作。
_QUESTION_WORDS = (
    "什么",
    "怎么",
    "如何",
    "哪些",
    "哪个",
    "多少",
    "几个",
    "几笔",
    "几次",
    "多少次",
    "为什么",
    "为何",
    "谁",
    "是不是",
    "是否",
    "能否",
    "能不能",
    "可不可以",
    "多久",
    "几天",
    "几号",
    "哪天",
    "多长时间",
    "到哪",
    "什么时候",
    "怎样",
    "哪种",
    "哪一",
    "吗",
    "呢",
)
# 请求框架：用户明确要求"现在就做"。
_REQUEST_FRAMES = (
    "我要",
    "我想",
    "我需要",
    "我准备",
    "帮我",
    "帮忙",
    "请帮",
    "给我",
    "麻烦",
    "替我",
    "能不能帮",
    "可以帮",
)
# 泛化陈述：讲的是通则（"如果…会…"），不是这一单的具体诉求。
_GENERIC_MARKERS = (
    "如果",
    "要是",
    "一旦",
    "将会",
    "会不会",
    "一般情况下",
    "一般来说",
    "通常",
    "什么情况下",
    "哪种情况下",
    "每次",
    "多次",
    "所有",
    "任何",
)
_GENERATIVE = re.compile(r"会(?=[\u4e00-\u9fff])")

# 政策文本的"元名词"：出现即说明谈的是规则本身。
POLICY_TOPIC_TERMS = (
    "政策",
    "条款",
    "规则",
    "条件",
    "期限",
    "规定",
    "要求",
    "证据",
    "依据",
    "标准",
    "流程",
    "规范",
    "计算",
    "审批",
    "区别",
    "范围",
    "时效",
    "定义",
    "优先级",
    "赔偿",
    "售后",
    "退货",
    "超期",
    "过期",
    "逾期",
)
REFUND_TOPIC_TERMS = ("退款", "退货", "退钱", "退费")
TICKET_TOPIC_TERMS = ("工单", "转人工", "人工客服", "投诉")


@dataclass(frozen=True, slots=True)
class RoutePlan:
    intent: Intent
    order_id: str | None = None
    amount_cents: int | None = None
    amount_invalid: bool = False
    refund_reason: RefundReason | None = None
    refund_type: RefundType | None = None


def _refund_reason(value: object) -> RefundReason | None:
    """A refund reason that is present *and* legal; anything else is absent."""
    if isinstance(value, RefundReason):
        return value
    if isinstance(value, str):
        try:
            return RefundReason(value)
        except ValueError:
            return None
    return None


def _refund_type(value: object) -> RefundType | None:
    """A refund type that is present *and* legal; anything else is absent."""
    if isinstance(value, RefundType):
        return value
    if isinstance(value, str):
        try:
            return RefundType(value)
        except ValueError:
            return None
    return None


def extract_order_id(text: str) -> str | None:
    """Return the order id mentioned in the text, if any."""
    match = _ORDER_ID.search(text)
    return match.group(0).upper() if match else None


def extract_amount(text: str) -> tuple[int | None, bool]:
    """Return (amount_cents, invalid). ``invalid`` marks an unparsable amount."""
    match = _AMOUNT.search(text)
    if match is None:
        return None, False
    amount = parse_yuan_to_cents(match.group(1))
    return amount, amount is None


def strip_amount(text: str) -> str:
    """Remove amount phrases so a resumed request keeps only its intent wording."""
    return " ".join(_AMOUNT.sub(" ", text).split())


def is_interrogative(text: str) -> bool:
    """True when the message asks for information instead of requesting an action."""
    if "?" in text or "？" in text:
        return True
    return any(word in text for word in _QUESTION_WORDS)


def has_request_frame(text: str) -> bool:
    """True when the message explicitly asks for an action to be performed."""
    return any(frame in text for frame in _REQUEST_FRAMES)


def is_generic_statement(text: str) -> bool:
    """True for general-rule statements, which describe a rule instead of a request."""
    if has_request_frame(text):
        return False
    if any(marker in text for marker in _GENERIC_MARKERS):
        return True
    return bool(_GENERATIVE.search(text))


def blocks_write_action(text: str) -> bool:
    """True when the message must not trigger a write action.

    Questions and general-rule statements talk about refund or ticket rules;
    only an explicit request may create a refund or a ticket.
    """
    if has_request_frame(text):
        return False
    return is_interrogative(text) or is_generic_statement(text)


def is_policy_question(text: str) -> bool:
    """True when the message asks about rules instead of asking for a concrete action."""
    if any(term in text for term in POLICY_TOPIC_TERMS):
        return True
    if not (is_interrogative(text) or is_generic_statement(text)):
        return False
    return any(term in text for term in REFUND_TOPIC_TERMS + TICKET_TOPIC_TERMS)


def is_analytics_question(text: str) -> bool:
    """True when the message asks for a number about the caller's own records.

    Both halves are needed: an aggregate without a record set ("how much in
    total?") and a record set without an aggregate ("my refunds") are both
    answered somewhere else. A question about a rule is answered from the
    policy corpus, not from the caller's records.
    """
    if is_policy_question(text):
        return False
    return asks_for_aggregate(text) and names_dataset(text) is not None


# Only these Intent members can be claimed by classify(); a new write row must
# extend the enum (or be exempted below with a reason) before it can be read.
_WRITE_INTENT_IDS = frozenset({Intent.REFUND_REQUEST, Intent.TICKET_CREATE})
# Catalog rows that are write_path but have no Intent member, so classify()
# never judged them and still cannot: DELIVERY_ISSUE is decided by the catalog
# alone, in classify_support().
# This set is also a registration surface, not just a skip list: it must stay
# a subset of _WRITE_INTENT_IDS, so adding an id here on its own is not enough.
# That is why every entry must spell out its reason (DELIVERY_ISSUE: no Intent
# member, which is what the three lines above say).
_WRITE_INTENT_EXEMPT = frozenset({"DELIVERY_ISSUE"})


def _match_write_intent(text: str) -> IntentSpec | None:
    """The first catalog write row whose cues appear in the text.

    Imported inside the function on purpose: the catalog reads
    blocks_write_action from this module, so a module-level import would close
    a cycle. Rows that classify() cannot claim are skipped, never silently
    dropped -- the subset rule is asserted by a test, not by this loop.
    """
    from app.agent.intent_catalog import get_catalog

    for spec in get_catalog().ordered_specs():
        if not spec.write_path:
            continue
        if spec.intent_id in _WRITE_INTENT_EXEMPT:
            continue
        if spec.intent_id not in _WRITE_INTENT_IDS:
            continue
        if any(cue in text for cue in spec.cues):
            return spec
    return None


def classify(request: str, context: Mapping[str, object] | None = None) -> RoutePlan:
    context = context or {}
    text = request.lower()
    order_match = _ORDER_ID.search(request)
    context_order = context.get("order_id")
    order_id = (
        order_match.group(0).upper()
        if order_match
        else (str(context_order).upper() if context_order else None)
    )

    amount_cents: int | None = None
    amount_invalid = False
    amount_match = _AMOUNT.search(request)
    if amount_match:
        amount_cents = parse_yuan_to_cents(amount_match.group(1))
        amount_invalid = amount_cents is None
    else:
        context_amount = context.get("amount_cents")
        if isinstance(context_amount, int):
            amount_cents = context_amount
            amount_invalid = amount_cents <= 0

    # The reason has two paths and no third: what the caller supplied, or what
    # the keyword table reads out of the message. A value the context already
    # carries wins, so a turn that only adds information keeps the answer the
    # customer already gave. A miss stays missing -- it is a clarification,
    # not a guess. The rule table runs only on a turn this function reads as a
    # refund request (口径 §1「发起退款时」、§5.6.2): an "其他" or "坏了" in a
    # sentence that is not a refund is not a reason choice and must not latch.
    # Turns that reach REFUND_REQUEST later (resume / model adoption) run the
    # table at the call site instead.

    blocked = blocks_write_action(text)
    write_spec = None if blocked else _match_write_intent(text)
    if write_spec is not None:
        intent = Intent(write_spec.intent_id)
    elif any(word in text for word in _REFUND_ELIGIBILITY):
        intent = Intent.POLICY_QA
    elif is_policy_question(text):
        intent = Intent.POLICY_QA
    elif any(word in text for word in _LOGISTICS):
        intent = Intent.LOGISTICS_QUERY
    elif any(word in text for word in _POLICY):
        intent = Intent.POLICY_QA
    elif any(word in text for word in _ORDER):
        intent = Intent.ORDER_QUERY
    else:
        intent = Intent.UNKNOWN

    refund_reason = _refund_reason(context.get("refund_reason"))
    if refund_reason is None and intent is Intent.REFUND_REQUEST:
        refund_reason = match_refund_reason(request)
    refund_type = _refund_type(context.get("refund_type"))

    return RoutePlan(
        intent=intent,
        order_id=order_id,
        amount_cents=amount_cents,
        amount_invalid=amount_invalid,
        refund_reason=refund_reason,
        refund_type=refund_type,
    )
