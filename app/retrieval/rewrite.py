"""检索侧的确定性查询改写：只改送去检索的那段文字。

改写只发生在**检索文本**上。用户的原话、回复、意图与槽位值都还用它原来的字，
所以一次改写不会悄悄改掉一个判断，评测里的数字也能离线复现。

三件事，都是加法、都不是替换（替换会毁掉原文自带的词）：

1. 中文数字与时长补一份阿拉伯写法（``三十天`` → ``30天``），原文保留——
   「七天无理由退货」这样的章节标题还得靠它自己的写法才匹配得上；
2. 口语命中政策词汇时追加规范说法（``退钱`` → ``退款``），只加政策侧的词，
   用户的原话留在原位；
3. 域与 5W1H 都指得准时，追加一两条当前生效政策的章节标题（``怎么处理`` →
   ``处理方式``）。标题是 BM25 最强的词法信号，因此条数有上限，且绝不凭空
   造一个标题出来。

边界：本模块不认识数据库，也不认识权限，只吃字符串吐字符串。``QueryRewriter``
是纯函数式的，``RewritingPolicySearch`` 只是在检索器外面套了一层；评测脚本与
``app/retrieval/build.py`` 用同一份实现，所以评测测的就是线上那条改写。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import cast

from app.core.security import Identity
from app.retrieval.search import PolicySearch, RetrievedChunk

FACET_ORDER: tuple[str, ...] = ("WHO", "WHAT", "WHEN", "WHERE", "WHY", "HOW")

# Documented 口径上限. Configuration may lower these, never raise them: a
# deployment that sets 9 must not turn one query into nine title hints.
MAX_SECTION_HINTS = 2
MAX_EXPANSIONS = 8

# The 15 titles of the currently active policy documents. A drift test checks
# this table against the seeded chunks, so a renamed heading cannot silently
# turn into a hint that never matches.
SECTION_TITLES: dict[str, tuple[str, ...]] = {
    "POLICY-REFUND": ("适用范围", "退款条件", "退款金额", "审批要求", "结果确认"),
    "POLICY-DELAY": ("延迟判定", "处理方式", "证据要求"),
    "POLICY-RETURN": ("七天无理由退货", "退货与退款的区别", "不予退货的情形", "操作要求"),
    "POLICY-TICKET": ("优先级定义", "创建规则", "转人工"),
}


@dataclass(frozen=True, slots=True)
class RewriteResult:
    original: str
    search_text: str
    added_terms: tuple[str, ...]
    facets: tuple[str, ...]
    section_hints: tuple[str, ...]
    numbers: tuple[str, ...]
    synonyms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SectionRule:
    policy_id: str
    section: str
    cues: tuple[str, ...]
    facets: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _SynonymGroup:
    policy_terms: tuple[str, ...]
    user_terms: tuple[str, ...]


_CN_DIGITS: dict[str, int] = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS: dict[str, int] = {
    "十": 10,
    "百": 100,
    "千": 1000,
    "万": 10_000,
    "亿": 100_000_000,
}

# Numerals must be followed by a unit. This is what keeps ``一并寄回``,
# ``一般`` and ``一致`` out of the converter.
_NUMBER_RE = re.compile(
    r"(?P<num>[零〇一二两三四五六七八九十百千万亿]+)\s*"
    r"(?P<unit>个工作日|个月|星期|小时|分钟|周|天|日|元|块|分)"
)
_FULLWIDTH_NUMBER_RE = re.compile(
    r"(?P<num>[\uff10-\uff19]+)\s*"
    r"(?P<unit>个工作日|个月|星期|小时|分钟|周|天|日|元|块|分)"
)
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_HALF_MONTH_RE = re.compile(r"半\s*个月")

_FACET_CUES: dict[str, tuple[str, ...]] = {
    "WHO": ("谁", "哪位", "哪个部门", "哪个角色", "人工", "审批人", "负责人", "客服"),
    "WHAT": (
        "哪些",
        "什么",
        "哪种",
        "条件",
        "范围",
        "金额",
        "多少",
        "区别",
        "定义",
        "等级",
        "级别",
        "优先级",
    ),
    "WHEN": (
        "多久",
        "几天",
        "什么时候",
        "何时",
        "期限",
        "时效",
        "之前",
        "之后",
        "以内",
        "超过",
        "小时",
        "当天",
        "几天内",
    ),
    "WHERE": ("哪里", "哪个渠道", "入口", "页面", "系统", "线上", "线下"),
    "WHY": ("为什么", "为何", "原因", "凭什么", "依据", "怎么判定", "怎么算"),
    "HOW": (
        "怎么",
        "如何",
        "怎样",
        "流程",
        "处理",
        "操作",
        "申请",
        "提交",
        "确认",
        "才能",
    ),
}

_DOMAIN_CUES: dict[str, tuple[str, ...]] = {
    "POLICY-REFUND": (
        "退款",
        "退钱",
        "退费",
        "返款",
        "到账",
        "可退",
        "退款单",
        "退款政策",
    ),
    "POLICY-DELAY": (
        "延迟",
        "晚到",
        "超时",
        "没到",
        "物流",
        "快递",
        "承运商",
        "丢件",
        "轨迹",
        "配送",
        "包裹",
    ),
    "POLICY-RETURN": (
        "退货",
        "退换",
        "寄回",
        "退回",
        "无理由",
        "拆封",
        "包装",
        "赠品",
    ),
    "POLICY-TICKET": (
        "工单",
        "优先级",
        "紧急",
        "转人工",
        "人工介入",
        "升级",
        "投诉",
        "派单",
    ),
}

# Keyword rules win over the facet fallback. Within one domain the first two
# matching rules are used, so a cross-topic question still gets at most two
# hints.
_SECTION_RULES: tuple[_SectionRule, ...] = (
    _SectionRule(
        "POLICY-REFUND",
        "结果确认",
        ("到账", "成功", "确认", "超时", "重复提交", "接口返回", "受理", "没到账", "还没到"),
    ),
    _SectionRule(
        "POLICY-REFUND",
        "审批要求",
        ("审批", "审核", "谁批", "人工审批", "审批人", "版本变化", "订单版本"),
    ),
    _SectionRule(
        "POLICY-REFUND",
        "退款金额",
        ("金额", "多少钱", "退多少", "余额", "运费", "快递费", "税费", "优惠券", "手续费", "分"),
    ),
    _SectionRule(
        "POLICY-REFUND",
        "退款条件",
        (
            "条件",
            "多久",
            "几天",
            "期限",
            "时效",
            "签收后",
            "超过",
            "以内",
            "申请退款",
            "提交退款申请",
            "退款申请",
            "什么时候",
            "多久能退",
        ),
    ),
    _SectionRule(
        "POLICY-REFUND",
        "适用范围",
        (
            "哪些订单",
            "哪些情况",
            "适用范围",
            "适用",
            "未发货",
            "没发货",
            "没出库",
            "仓库",
            "已支付",
            "已取消",
            # "退款政策" is a frame, not a topic: 退款政策对延迟发货怎么规定
            # asks about delivery, so the policy name must not pull in 适用范围.
            "能不能退",
            "可以退吗",
            "范围",
        ),
    ),
    _SectionRule(
        "POLICY-DELAY",
        "延迟判定",
        (
            "判定",
            "算不算",
            "超过几天",
            "7天",
            "七天",
            "48小时",
            "轨迹",
            "丢件",
            "多久算",
            "严重延迟",
            "物流更新",
            "没有更新",
            "没有物流更新",
        ),
    ),
    _SectionRule(
        "POLICY-DELAY",
        "处理方式",
        ("怎么处理", "怎么办", "如何处理", "处理方式", "赔偿", "补偿", "联系承运商", "高优先级"),
    ),
    _SectionRule(
        "POLICY-DELAY",
        "证据要求",
        (
            "证据",
            "凭证",
            "物流记录",
            "证明",
            "核实",
            "核对",
            "只凭",
            "用户描述",
            "判断丢件",
        ),
    ),
    _SectionRule(
        "POLICY-RETURN",
        "七天无理由退货",
        ("七天", "7天", "无理由", "期限", "多久", "拆封", "包装", "赠品", "签收后"),
    ),
    _SectionRule(
        "POLICY-RETURN",
        "退货与退款的区别",
        (
            "区别",
            "退货退款",
            "先退",
            "关系",
            "等于退款",
            "是不是退款",
            "验收",
            "多久退款",
            "寄回后",
        ),
    ),
    _SectionRule(
        "POLICY-RETURN",
        "不予退货的情形",
        (
            "哪些不能",
            "不能退",
            "不可以退",
            "不予",
            "不支持",
            "贴身",
            "食品",
            "定制",
            "超期",
            "缺少配件",
            "二次销售",
            "赠品缺失",
            "贴身用品",
            "拆封的",
        ),
    ),
    _SectionRule(
        "POLICY-RETURN",
        "操作要求",
        (
            "客服",
            "怎么回答",
            "引用",
            "提醒",
            "表述",
            "话术",
            "怎么说",
            "告诉用户",
            "已经退款",
            "误解",
            "纠正",
        ),
    ),
    _SectionRule(
        "POLICY-TICKET",
        "优先级定义",
        ("优先级", "紧急", "URGENT", "HIGH", "NORMAL", "LOW", "等级", "级别", "最高优先级"),
    ),
    _SectionRule(
        "POLICY-TICKET",
        "创建规则",
        ("创建", "绑定", "别人的订单", "重复提交", "幂等", "几张", "工单号", "禁止"),
    ),
    _SectionRule(
        "POLICY-TICKET",
        "转人工",
        ("转人工", "人工介入", "找人工", "证据不足", "政策冲突", "重试上限", "升级", "什么时候转"),
    ),
)

_FACET_FALLBACK: dict[str, dict[str, str]] = {
    "POLICY-REFUND": {"WHEN": "退款条件", "WHO": "审批要求"},
    "POLICY-DELAY": {"WHY": "延迟判定", "HOW": "处理方式"},
    "POLICY-RETURN": {"WHEN": "七天无理由退货"},
    "POLICY-TICKET": {"WHAT": "优先级定义", "WHO": "转人工"},
}

# Only user wording -> policy wording. If the policy term is already present,
# nothing is appended, so a query is not inflated by its own vocabulary.
_SYNONYM_GROUPS: tuple[_SynonymGroup, ...] = (
    _SynonymGroup(("退款",), ("退钱", "退费", "返款", "退还", "退回来")),
    _SynonymGroup(("退款条件", "退款规则"), ("什么情况能退", "能不能退", "退款要求")),
    _SynonymGroup(
        ("退款金额", "可退余额"),
        ("退多少钱", "退多少", "能退多少", "可退金额"),
    ),
    _SynonymGroup(("审批", "人工审批"), ("审核", "谁批", "审批人")),
    _SynonymGroup(("结果确认", "到账"), ("退款成功", "到没到", "钱到了吗", "确认成功")),
    _SynonymGroup(("签收", "收货"), ("收到货", "到货", "签收时间")),
    _SynonymGroup(("发货",), ("寄出", "出库", "发出")),
    _SynonymGroup(("物流", "承运商"), ("快递", "配送", "运输")),
    _SynonymGroup(("延迟",), ("晚到", "超时", "迟迟不到", "延误", "没到")),
    _SynonymGroup(("丢件",), ("丢失", "丢了", "遗失")),
    _SynonymGroup(("赔偿", "补偿"), ("赔付", "赔钱", "补偿金")),
    _SynonymGroup(("运费",), ("邮费", "快递费", "配送费")),
    _SynonymGroup(("退货",), ("退换", "退回", "寄回", "退掉")),
    _SynonymGroup(("七天无理由退货",), ("7天无理由", "无理由退货", "七天无理由")),
    _SynonymGroup(("拆封",), ("打开", "开封", "用过")),
    _SynonymGroup(("不予退货",), ("不能退", "不支持退货", "不可以退")),
    _SynonymGroup(("优先级",), ("紧急程度", "优先等级")),
    _SynonymGroup(("工单",), ("报修单", "服务单", "工单号")),
    _SynonymGroup(("转人工",), ("人工客服", "人工介入", "找人工", "转客服", "升级处理")),
    _SynonymGroup(("重复提交",), ("重复创建", "多次提交", "重复申请")),
    _SynonymGroup(("余额",), ("剩余金额", "可退额度", "还能退多少")),
    _SynonymGroup(("优惠券",), ("券", "折扣")),
    _SynonymGroup(("税费",), ("税", "手续费")),
    _SynonymGroup(("证据",), ("凭证", "证明", "证明材料", "物流记录")),
    _SynonymGroup(("适用范围",), ("适用情况", "哪些订单", "哪些情况", "适用对象")),
    _SynonymGroup(("创建规则",), ("怎么创建", "建单规则", "开单规则")),
    _SynonymGroup(("绑定",), ("关联", "挂靠")),
    _SynonymGroup(("幂等键",), ("幂等", "去重键")),
    _SynonymGroup(("重试上限",), ("重试次数", "重试限制")),
    _SynonymGroup(("版本",), ("版次", "新旧版本")),
    _SynonymGroup(("失效",), ("过期", "作废", "无效")),
)


def _cn_to_int(text: str) -> int | None:
    """Convert a Chinese numeral without a unit (``一百零五`` -> 105)."""
    total = 0
    section = 0
    number = 0
    seen = False
    for char in text:
        if char in _CN_DIGITS:
            number = _CN_DIGITS[char]
            seen = True
            continue
        unit = _CN_UNITS.get(char)
        if unit is None:
            return None
        seen = True
        if unit < 10_000:
            if number == 0:
                number = 1
            section += number * unit
            number = 0
        else:
            section = (section + number) * unit
            total += section
            section = 0
            number = 0
    return total + section + number if seen else None


def parse_chinese_number(text: str) -> int | None:
    """The value of a Chinese numeral without a unit (``一百零五`` -> 105)."""
    return _cn_to_int(text)


def _normalise_number(value: int, unit: str) -> str:
    if unit in {"个月", "月"}:
        return f"{value * 30}天"
    if unit in {"周", "星期"}:
        return f"{value * 7}天"
    if unit in {"元", "块"}:
        return f"{value}元"
    return f"{value}{unit}"


def _number_terms(query: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in _NUMBER_RE.finditer(query):
        value = _cn_to_int(match.group("num"))
        if value is None:
            continue
        term = _normalise_number(value, match.group("unit"))
        if term not in query:
            found.append(term)
    for match in _FULLWIDTH_NUMBER_RE.finditer(query):
        digits = match.group("num").translate(_FULLWIDTH_DIGITS)
        term = _normalise_number(int(digits), match.group("unit"))
        if term not in query:
            found.append(term)
    if _HALF_MONTH_RE.search(query) and "15天" not in query:
        found.append("15天")
    return tuple(dict.fromkeys(found))


def _facets(query: str) -> tuple[str, ...]:
    return tuple(
        facet
        for facet in FACET_ORDER
        if any(cue in query for cue in _FACET_CUES[facet])
    )


def detect_facets(query: str) -> tuple[str, ...]:
    """The 5W1H facets whose cues appear in the query, in fixed order."""
    return _facets(query)


def _domains(query: str) -> list[tuple[str, int]]:
    scored: list[tuple[str, int]] = []
    for policy_id, cues in _DOMAIN_CUES.items():
        score = sum(1 for cue in cues if cue in query)
        if score:
            scored.append((policy_id, score))
    scored.sort(key=lambda item: (-item[1], item[0]))
    return scored


def detect_domain(query: str) -> str | None:
    """The policy domain with the strongest lexical cue, or ``None``."""
    scored = _domains(query)
    return scored[0][0] if scored else None


def _section_hints(
    query: str,
    facets: tuple[str, ...],
    *,
    limit: int,
) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    domains = _domains(query)
    if not domains:
        return ()
    best = domains[0][1]
    candidates = [policy_id for policy_id, score in domains if score == best]

    # A query that already names an active section has told BM25 where to go.
    # Adding another title (or falling back to a facet) only moves a competing
    # chunk up, so leave it alone.
    if any(
        title in query
        for policy_id in candidates
        for title in SECTION_TITLES.get(policy_id, ())
    ):
        return ()

    ranked: list[tuple[int, int, int, str]] = []
    facet_preferred = {
        section
        for policy_id in candidates
        for facet in facets
        for section in (_FACET_FALLBACK.get(policy_id, {}).get(facet),)
        if section
    }
    for index, rule in enumerate(_SECTION_RULES):
        if rule.policy_id not in candidates:
            continue
        matched = sum(1 for cue in rule.cues if cue in query)
        if not matched:
            continue
        if rule.section in query:
            continue
        ranked.append((matched, rule.section in facet_preferred, -index, rule.section))
    ranked.sort(reverse=True)

    hints: list[str] = []
    for _, _, _, section in ranked:
        if section not in hints:
            hints.append(section)
        if len(hints) >= limit:
            return tuple(hints)

    if not hints:
        for policy_id in candidates:
            for facet in facets:
                fallback = _FACET_FALLBACK.get(policy_id, {}).get(facet)
                if fallback and fallback not in query and fallback not in hints:
                    hints.append(fallback)
                    if len(hints) >= limit:
                        return tuple(hints)
    return tuple(hints)


def _synonym_terms(query: str, limit: int) -> tuple[str, ...]:
    if limit <= 0:
        return ()
    found: list[str] = []
    for group in _SYNONYM_GROUPS:
        if not any(term in query for term in group.user_terms):
            continue
        if any(term in query for term in group.policy_terms):
            continue
        for term in group.policy_terms:
            if term not in query and term not in found:
                found.append(term)
            if len(found) >= limit:
                return tuple(found)
    return tuple(found)


class QueryRewriter:
    """Append-only, deterministic query expansion for policy retrieval."""

    def __init__(
        self,
        *,
        numbers: bool = True,
        synonyms: bool = True,
        sections: bool = True,
        max_section_hints: int = 1,
        max_expansions: int = 8,
    ) -> None:
        self.numbers = numbers
        self.synonyms = synonyms
        self.sections = sections
        self.max_section_hints = min(MAX_SECTION_HINTS, max(0, max_section_hints))
        self.max_expansions = min(MAX_EXPANSIONS, max(0, max_expansions))

    def rewrite(self, query: str) -> RewriteResult:
        facets = _facets(query)
        number_terms = _number_terms(query) if self.numbers else ()
        section_hints = (
            _section_hints(query, facets, limit=self.max_section_hints)
            if self.sections
            else ()
        )

        added: list[str] = []
        for term in number_terms:
            if term not in query and term not in added:
                added.append(term)
        for term in section_hints:
            if term not in query and term not in added:
                added.append(term)

        remaining = max(0, self.max_expansions - len(added))
        synonym_terms = _synonym_terms(query, remaining) if self.synonyms else ()
        for term in synonym_terms:
            if term not in query and term not in added:
                added.append(term)

        added = added[: self.max_expansions]
        search_text = query if not added else f"{query} {' '.join(added)}"
        return RewriteResult(
            original=query,
            search_text=search_text,
            added_terms=tuple(added),
            facets=facets,
            section_hints=section_hints,
            numbers=number_terms,
            synonyms=synonym_terms,
        )


class RewritingPolicySearch:
    """Wrap any ``PolicySearch`` and hand it the rewritten retrieval text."""

    def __init__(self, inner: PolicySearch, rewriter: QueryRewriter) -> None:
        self.inner = inner
        self.rewriter = rewriter
        self.last_rewrite: RewriteResult | None = None

    def search(
        self,
        identity: Identity,
        query: str,
        *,
        today: date | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]:
        if not query.strip():
            return []
        result = self.rewriter.rewrite(query)
        self.last_rewrite = result
        return self.inner.search(
            identity, result.search_text, today=today, limit=limit
        )

    def __getattr__(self, name: str) -> object:
        # Diagnostics such as ``last_dense_decision`` live on the wrapped
        # search. A missing attribute must still raise, otherwise an eval
        # would mistake "sparse only" for "dense ran".
        return cast(object, getattr(self.inner, name))
