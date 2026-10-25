"""The datasets a customer may ask a question about.

Each dataset is one physical table, the columns that may be read from it, and
the two columns that scope a row to its owner. The scope columns are never
readable from a question — the executor injects them — so a statement cannot
even mention them.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "DATASETS",
    "STATUS_LABELS",
    "Dataset",
    "dataset_for",
    "status_label",
]


@dataclass(frozen=True, slots=True)
class Dataset:
    name: str
    table: str
    label: str
    unit: str
    columns: tuple[str, ...]
    scope_columns: tuple[str, ...]
    time_column: str
    amount_column: str | None
    status_column: str | None


DATASETS: dict[str, Dataset] = {
    "refunds": Dataset(
        name="refunds",
        table="refunds",
        label="退款",
        unit="笔",
        columns=("id", "order_id", "amount_cents", "status", "created_at"),
        scope_columns=("tenant_id", "user_id"),
        time_column="created_at",
        amount_column="amount_cents",
        status_column="status",
    ),
    "orders": Dataset(
        name="orders",
        table="orders",
        label="订单",
        unit="单",
        columns=("id", "status", "total_cents", "currency", "paid_at", "created_at"),
        scope_columns=("tenant_id", "user_id"),
        time_column="created_at",
        amount_column="total_cents",
        status_column="status",
    ),
    "tickets": Dataset(
        name="tickets",
        table="tickets",
        label="工单",
        unit="个",
        columns=("id", "ticket_no", "category", "priority", "status", "created_at"),
        scope_columns=("tenant_id", "user_id"),
        time_column="created_at",
        amount_column=None,
        status_column="status",
    ),
}


def dataset_for(name: str) -> Dataset | None:
    return DATASETS.get(name)


# The status values a grouped answer may show, in the customer's language.
STATUS_LABELS: dict[str, dict[str, str]] = {
    "refunds": {
        "REQUESTED": "已受理",
        "SUCCEEDED": "已到账",
        "FAILED": "退款失败",
        "UNKNOWN": "状态待确认",
    },
    "orders": {
        "PLACED": "已下单",
        "SHIPPED": "已发货",
        "DELIVERED": "已签收",
        "CANCELLED": "已取消",
    },
    "tickets": {
        "OPEN": "待处理",
        "IN_PROGRESS": "处理中",
        "RESOLVED": "已解决",
        "CLOSED": "已关闭",
    },
}


def status_label(dataset: str, value: str) -> str:
    """The readable name of a status value in one dataset."""
    return STATUS_LABELS.get(dataset, {}).get(value, value)
