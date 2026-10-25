from __future__ import annotations

import argparse
import sys

import httpx

TENANT = "demo"
USER = "alice"


def call(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    json: dict | None = None,
) -> dict:
    response = client.request(method, path, json=json)
    if response.status_code >= 400:
        raise SystemExit(
            f"{method} {path} -> {response.status_code}\n{response.text}"
        )
    return response.json()


def show_cards(cards: list[dict]) -> None:
    for card in cards:
        print(f"  [{card['kind']}] {card['title']} · {card['card_id']}")
        print(f"    摘要: {card['summary']}")
        print(
            f"    章节: {len(card['sections'])} 个 / 全文 {card['total_chars']} 字"
            "（响应只带预览）"
        )
        for section in card["sections"]:
            print(f"      - {section['id']}  {section['title']}  ({section['chars']} 字)")


def show_section(client: httpx.Client, card: dict, section_id: str) -> None:
    path = f"/api/v1/support/cards/{card['kind']}/{card['card_id']}/sections/{section_id}"
    detail = call(client, "GET", path)
    section = detail["section"]
    print(f"  按需加载 {card['card_id']}/{section['id']}：{section['title']}")
    print(f"    {section['content'].splitlines()[0]}")
    if detail.get("citation"):
        citation = detail["citation"]
        print(
            "    引用: "
            f"{citation['document_id']} {citation['version']} "
            f"§ {citation['section']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only TicketPilot support demo.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--tenant", default=TENANT)
    parser.add_argument("--user", default=USER)
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    headers = {"X-Tenant-Id": args.tenant, "X-User-Id": args.user}
    with httpx.Client(base_url=args.base_url, headers=headers, timeout=30) as client:
        try:
            call(client, "GET", "/health/ready")
        except httpx.HTTPError as exc:
            raise SystemExit(
                f"无法连接 {args.base_url}；先启动 uvicorn 并运行 scripts/seed.py。\n{exc}"
            ) from exc

        questions = [
            "订单 ORD-DEMO-1001 买的是什么商品？桌板材质和承重是多少？",
            "退款条件是什么？已签收多久内可以申请？",
            "ORD-DEMO-1002 的物流到哪了？",
        ]
        cards: list[dict] = []
        for question in questions:
            payload = call(
                client,
                "POST",
                "/api/v1/support/messages",
                json={"message": question},
            )
            print(f"\n> {question}")
            print(f"  意图: {payload['intent']}  模式: {payload['agent_mode']}")
            print(f"  客服: {payload['reply']}")
            show_cards(payload["context_cards"])
            cards.extend(payload["context_cards"])

        product = next(card for card in cards if card["kind"] == "product")
        policy = next(card for card in cards if card["kind"] == "policy")
        print("\n渐进式披露：卡片只返回章节预览，正文按需请求")
        show_section(client, product, product["sections"][1]["id"])
        show_section(client, policy, policy["sections"][1]["id"])

        print("\n只读问答到此结束；退款与工单请走确定性工作流（见控制台 审批工作台）。")


if __name__ == "__main__":
    main()
