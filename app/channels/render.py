from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.channels.base import ChannelCapabilities, RenderedMessage
from app.models.enums import Channel

__all__ = [
    "CAPABILITIES",
    "capabilities_for",
    "render_notice",
    "render_response",
    "speak",
    "truncate_sentences",
]

_WEB = ChannelCapabilities(
    supports_stream=True,
    supports_rich_card=True,
    supports_voice=False,
    max_length=None,
    max_cards=6,
)
_APP = ChannelCapabilities(
    supports_stream=True,
    supports_rich_card=True,
    supports_voice=False,
    max_length=None,
    max_cards=6,
)
_EMAIL = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=False,
    supports_voice=False,
    max_length=None,
    max_cards=0,
    formal=True,
)
_WECHAT = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=False,
    supports_voice=False,
    max_length=200,
    max_cards=0,
)
_WECOM = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=False,
    supports_voice=False,
    max_length=200,
    max_cards=0,
)
_WHATSAPP = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=True,
    supports_voice=False,
    max_length=1024,
    max_cards=3,
)
_VOICE = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=False,
    supports_voice=True,
    max_length=120,
    max_cards=0,
    sentence_char_limit=25,
    offers_human_handoff=True,
)
_SMS = ChannelCapabilities(
    supports_stream=False,
    supports_rich_card=False,
    supports_voice=False,
    max_length=70,
    max_cards=0,
)

CAPABILITIES: dict[Channel, ChannelCapabilities] = {
    Channel.WEB: _WEB,
    Channel.APP: _APP,
    Channel.EMAIL: _EMAIL,
    Channel.WECHAT: _WECHAT,
    Channel.WECOM: _WECOM,
    Channel.WHATSAPP: _WHATSAPP,
    Channel.VOICE: _VOICE,
    Channel.SMS: _SMS,
}

_SENTENCE_END = re.compile(r"(?<=[。！？!?；;])")
_URL = re.compile(r"https?://\S+")
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*•]|\d+[.)、])\s*")


def capabilities_for(channel: Channel) -> ChannelCapabilities:
    return CAPABILITIES[channel]


def truncate_sentences(text: str, limit: int) -> tuple[str, bool]:
    """Cut at a sentence boundary and mark the cut with an ellipsis.

    When no sentence fits at all, the hard cut still ends in an ellipsis, so
    a reader can always tell text was removed. The result never exceeds
    ``limit``.
    """
    if limit <= 0:
        return "", bool(text)
    if len(text) <= limit:
        return text, False
    if limit == 1:
        return "…", True

    kept: list[str] = []
    for part in _SENTENCE_END.split(text):
        piece = part.strip()
        if not piece:
            continue
        candidate = "".join(kept) + piece
        if len(candidate) > limit - 1:
            break
        kept.append(piece)

    body = "".join(kept)
    if not body:
        body = text[: limit - 1]
    return body + "…", True


def _card_titles(cards: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    titles: list[str] = []
    for card in cards:
        title = card.get("title")
        if isinstance(title, str) and title.strip():
            titles.append(title.strip())
    return tuple(titles)


def _ticket_number(response: Mapping[str, Any]) -> str | None:
    task = response.get("task")
    if not isinstance(task, Mapping):
        return None
    result = task.get("result")
    if not isinstance(result, Mapping):
        return None
    data = result.get("data")
    if isinstance(data, Mapping):
        value = data.get("ticket_no")
        if isinstance(value, str) and value:
            return value
    evidence = result.get("evidence")
    if isinstance(evidence, list):
        for item in evidence:
            if isinstance(item, Mapping):
                value = item.get("reference")
                if isinstance(value, str) and value.startswith("TCK-"):
                    return value
    return None


def _reference_block(titles: Sequence[str]) -> str:
    if not titles:
        return ""
    lines = ["", "参考资料："]
    lines.extend(f"{index}. {title}" for index, title in enumerate(titles, 1))
    return "\n".join(lines)


def _render_email(
    response: Mapping[str, Any],
    *,
    sla_hours: int,
) -> RenderedMessage:
    reply = str(response.get("reply") or "").strip()
    titles = _card_titles(_cards(response))
    ticket = _ticket_number(response)
    conversation_id = response.get("conversation_id")
    reference = ticket or (
        conversation_id if isinstance(conversation_id, str) else "未生成"
    )

    lines = ["您好：", "", reply or "您的诉求已收到。"]
    references = _reference_block(titles)
    if references:
        lines.append(references)
    lines.extend(
        [
            "",
            f"受理编号：{reference}",
            f"处理时限：{sla_hours} 小时内首次回复",
            "",
            "—— TicketPilot 客服团队",
            "此邮件由系统自动发送，请勿直接回复。如需人工协助，请回复「转人工」。",
        ]
    )
    text = "\n".join(lines)
    conversation = response.get("conversation_id")
    subject_reference = ticket or (
        conversation if isinstance(conversation, str) else "受理中"
    )
    return RenderedMessage(
        text=text,
        payload=response,
        subject=f"TicketPilot 售后回复 · {subject_reference}",
        context_references=titles,
    )


def _render_im(
    response: Mapping[str, Any],
    *,
    limit: int,
) -> RenderedMessage:
    reply = str(response.get("reply") or "").strip()
    titles = _card_titles(_cards(response))
    index = ""
    if titles:
        entries = " ".join(
            f"{number}.{title}" for number, title in enumerate(titles[:3], 1)
        )
        index = f"详情：{entries}"
        if len(index) > limit // 2:
            index = "详情：回复「详情」查看"
    budget = limit - len(index) - (1 if index else 0)
    body, truncated = truncate_sentences(reply, max(1, budget))
    text = f"{body}\n{index}" if index else body
    return RenderedMessage(
        text=text[:limit],
        payload=response,
        truncated=truncated,
        context_references=titles,
    )


def _render_whatsapp(response: Mapping[str, Any]) -> RenderedMessage:
    rendered = _render_im(response, limit=1024)
    return RenderedMessage(
        text=rendered.text,
        payload=response,
        truncated=rendered.truncated,
        context_references=rendered.context_references,
    )


def _render_voice(response: Mapping[str, Any]) -> RenderedMessage:
    reply = str(response.get("reply") or "")
    text, truncated = speak(reply, limit=100, sentence_limit=25)
    handoff = "需要人工帮助请说「转人工」。"
    if handoff not in text:
        text = f"{text}{handoff}"
    return RenderedMessage(
        text=text,
        payload=response,
        truncated=truncated,
        context_references=(),
    )


def speak(text: str, *, limit: int, sentence_limit: int) -> tuple[str, bool]:
    """Strip what a speaker cannot say, then cut whole sentences to ``limit``."""
    cleaned = _URL.sub("", text)
    cleaned = _LIST_MARKER.sub("", cleaned)
    pieces: list[str] = []
    for part in _SENTENCE_END.split(cleaned):
        piece = part.strip()
        if not piece:
            continue
        if len(piece) > sentence_limit:
            piece = piece[: sentence_limit - 1] + "…"
        pieces.append(piece)
    joined = "".join(pieces) or "您的诉求已收到。"
    return truncate_sentences(joined, limit)


def _render_sms(response: Mapping[str, Any]) -> RenderedMessage:
    reply = str(response.get("reply") or "").strip()
    suffix = " 回复TD退订"
    budget = 70 - len(suffix) - 1
    body, truncated = truncate_sentences(reply, max(1, budget))
    return RenderedMessage(
        text=f"{body}{suffix}",
        payload=response,
        truncated=truncated,
        context_references=(),
    )


def _cards(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = response.get("context_cards")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def render_response(
    channel: Channel,
    response: Mapping[str, Any],
    *,
    sla_hours: int = 24,
    deduplicated: bool = False,
) -> RenderedMessage:
    """One canonical response, many shapes. No channel touches the agent."""
    capabilities = capabilities_for(channel)
    if capabilities.formal:
        rendered = _render_email(response, sla_hours=sla_hours)
    elif channel in (Channel.WECHAT, Channel.WECOM):
        rendered = _render_im(response, limit=capabilities.max_length or 200)
    elif channel is Channel.WHATSAPP:
        rendered = _render_whatsapp(response)
    elif channel is Channel.VOICE:
        rendered = _render_voice(response)
    elif channel is Channel.SMS:
        rendered = _render_sms(response)
    else:
        reply = str(response.get("reply") or "")
        limit = capabilities.max_length
        text, truncated = (
            truncate_sentences(reply, limit)
            if limit is not None
            else (reply, False)
        )
        rendered = RenderedMessage(
            text=text,
            payload=response,
            truncated=truncated,
            context_references=_card_titles(_cards(response)),
        )
    if not deduplicated:
        return rendered
    return RenderedMessage(
        text=rendered.text,
        payload=rendered.payload,
        truncated=rendered.truncated,
        deduplicated=True,
        subject=rendered.subject,
        context_references=rendered.context_references,
        channel_message_id=rendered.channel_message_id,
    )


def render_notice(
    channel: Channel,
    *,
    title: str,
    body: str,
    sla_hours: int = 24,
) -> RenderedMessage:
    """One proactive reminder, shaped by what the channel can carry.

    A notice is not an agent answer, so it does not pretend to be one: there
    is no ticket reference and no card index. The same capability table still
    decides the length, the signature block and the sentence cut.
    """
    capabilities = capabilities_for(channel)
    headline = title.strip()[:120]
    detail = body.strip()
    payload: dict[str, Any] = {"title": headline, "body": detail}

    if capabilities.formal:
        lines = ["您好：", "", headline, detail or "详情请登录控制台查看。"]
        lines.extend(
            [
                "",
                f"处理时限：{sla_hours} 小时内首次回复",
                "",
                "—— TicketPilot 客服团队",
                "此为系统提醒，无需回复。如需人工协助，请在控制台回复「转人工」。",
            ]
        )
        return RenderedMessage(
            text="\n".join(lines),
            payload=payload,
            subject=f"TicketPilot 提醒 · {headline}"[:120],
        )

    if channel is Channel.VOICE:
        handoff = "需要人工帮助请说「转人工」。"
        budget = max(1, (capabilities.max_length or 100) - len(handoff))
        text, truncated = speak(
            f"{headline}。{detail}",
            limit=budget,
            sentence_limit=capabilities.sentence_char_limit or 25,
        )
        if capabilities.offers_human_handoff:
            text = f"{text}{handoff}"
        return RenderedMessage(text=text, payload=payload, truncated=truncated)

    separator = " " if channel is Channel.SMS else "\n"
    message = f"{headline}{separator}{detail}".strip()
    suffix = " 回复TD退订" if channel is Channel.SMS else ""
    limit = capabilities.max_length
    if limit is None:
        return RenderedMessage(text=message, payload=payload)
    budget = max(1, limit - len(suffix))
    body_text, truncated = truncate_sentences(message, budget)
    return RenderedMessage(
        text=f"{body_text}{suffix}"[:limit],
        payload=payload,
        truncated=truncated,
    )
