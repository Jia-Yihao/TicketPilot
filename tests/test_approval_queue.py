from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import ALICE, BOB, REVIEWER

ACME_REVIEWER = {
    "X-Tenant-Id": "acme",
    "X-User-Id": "reviewer",
    "X-Roles": "approver",
}


def _refund_task(
    client: TestClient,
    headers: dict[str, str],
    *,
    order_id: str = "ORD-DEMO-1001",
    amount: int = 129,
) -> dict:
    response = client.post(
        "/api/v1/tasks",
        headers=headers,
        json={"request": f"我要申请退款，订单 {order_id}，金额 {amount} 元，商品有质量问题"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_approval_queue_requires_approver(client: TestClient) -> None:
    _refund_task(client, ALICE)

    response = client.get("/api/v1/approvals", headers=ALICE)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_approval_queue_lists_pending_case_with_order(client: TestClient) -> None:
    task = _refund_task(client, ALICE)

    response = client.get("/api/v1/approvals", headers=REVIEWER)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 1
    assert body["counts"]["PENDING"] == 1
    item = body["items"][0]
    assert item["task_id"] == task["task_id"]
    assert item["status"] == "PENDING"
    assert item["amount_cents"] == 12900
    assert item["order_id"] == "ORD-DEMO-1001"
    assert item["requested_by"] == "alice"
    assert item["task_status"] == "WAITING_APPROVAL"
    assert item["order"]["refundable_cents"] == 12900
    assert item["order"]["version"] == item["resource_version"]


def test_approval_queue_and_detail_are_tenant_isolated(client: TestClient) -> None:
    _refund_task(client, ALICE)
    demo_item = client.get("/api/v1/approvals", headers=REVIEWER).json()["items"][0]

    listing = client.get("/api/v1/approvals", headers=ACME_REVIEWER)

    assert listing.status_code == 200
    assert listing.json()["total"] == 0
    assert listing.json()["items"] == []

    detail = client.get(
        f"/api/v1/approvals/{demo_item['approval_id']}", headers=ACME_REVIEWER
    )
    assert detail.status_code == 404


def test_approval_detail_includes_digest_and_payload(client: TestClient) -> None:
    _refund_task(client, ALICE)
    item = client.get("/api/v1/approvals", headers=REVIEWER).json()["items"][0]

    response = client.get(
        f"/api/v1/approvals/{item['approval_id']}", headers=REVIEWER
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["action_digest"]) == 64
    assert body["action_payload"]["order_id"] == "ORD-DEMO-1001"
    assert body["action_payload"]["amount_cents"] == 12900
    assert body["order"]["user_id"] == "alice"


def test_approval_queue_filters_and_resolution(client: TestClient) -> None:
    task = _refund_task(client, ALICE)
    pending = client.get(
        "/api/v1/approvals", params={"status": "PENDING"}, headers=REVIEWER
    )
    assert pending.json()["total"] == 1

    approved = client.post(
        f"/api/v1/tasks/{task['task_id']}/approve",
        headers=REVIEWER,
        json={"decision": "APPROVE", "reason": "金额与订单一致"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["outcome"] == "VERIFIED_SUCCESS"

    resolved = client.get(
        "/api/v1/approvals", params={"status": "CONSUMED"}, headers=REVIEWER
    )
    assert resolved.status_code == 200
    assert resolved.json()["total"] == 1
    assert resolved.json()["items"][0]["status"] == "CONSUMED"
    assert resolved.json()["counts"]["PENDING"] == 0
    assert resolved.json()["counts"]["CONSUMED"] == 1

    no_pending = client.get(
        "/api/v1/approvals", params={"status": "PENDING"}, headers=REVIEWER
    )
    assert no_pending.json()["total"] == 0
    assert no_pending.json()["counts"]["CONSUMED"] == 1


def test_approval_queue_rejects_unknown_status(client: TestClient) -> None:
    response = client.get(
        "/api/v1/approvals", params={"status": "NOT_A_STATUS"}, headers=REVIEWER
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_task_list_only_returns_own_tasks(client: TestClient) -> None:
    alice_task = client.post(
        "/api/v1/tasks",
        headers=ALICE,
        json={"request": "帮我查询订单 ORD-DEMO-1001"},
    ).json()
    bob_task = client.post(
        "/api/v1/tasks",
        headers=BOB,
        json={"request": "帮我查询订单 ORD-DEMO-2001"},
    ).json()

    alice_list = client.get("/api/v1/tasks", headers=ALICE)
    bob_list = client.get("/api/v1/tasks", headers=BOB)

    assert alice_list.status_code == 200
    assert alice_list.json()["total"] == 1
    assert alice_list.json()["items"][0]["task_id"] == alice_task["task_id"]
    assert alice_list.json()["items"][0]["outcome"] == "VERIFIED_SUCCESS"

    assert bob_list.status_code == 200
    assert bob_list.json()["total"] == 1
    assert bob_list.json()["items"][0]["task_id"] == bob_task["task_id"]
