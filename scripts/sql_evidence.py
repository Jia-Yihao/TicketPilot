"""Print the evidence behind the README's controlled Text-to-SQL section.

Three parts, all through the production path (planner -> builder -> guard ->
read-only execution) on a throwaway SQLite database:

1. the worked examples, with the plan, the scoped SQL, the answer and the
   ledger row each question produced;
2. the refusal matrix — statements a caller *could* try to smuggle in, and the
   stable reason code that stopped each one;
3. what the same questions look like from the outside: the analytics endpoint's
   error envelope, the ledger row a refusal still writes, and the sentence the
   customer actually reads in an agent turn.

Nothing here writes to the demo database and nothing is mocked: the numbers are
whatever the seeded rows add up to, so the output changes if the fixtures do.

    python scripts/sql_evidence.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.config import Settings
from app.core.security import Identity
from app.main import create_app
from app.models.sql_query import SqlQuery
from app.seed import seed_all
from app.sql.errors import SqlRejected
from app.sql.guard import SqlGuard

try:
    from evals.fixtures import apply_fixtures
except ModuleNotFoundError:  # editable install maps ``app`` only, not ``evals``
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from evals.fixtures import apply_fixtures

QUESTIONS = [
    "我今年退了多少款",
    "我最近 30 天有几笔退款，一共多少钱",
    "我的退款按状态分布",
    "我买过多少订单",
    # Refused: it names no record set, so the planner returns no plan.
    "退货运费谁出",
    # Refused for a different reason: the count is about refunds, but
    # 已发货 is an order status, so the two record sets do not fit.
    "我今年退了几笔已发货的单",
    # Accepted, but on the fallback dimension: 商品 is not a groupable column,
    # so the "分布" cue answers it as a status breakdown. The README's
    # "heuristic boundary" paragraph quotes this line.
    "我的退款按商品分布",
]

HOSTILE: list[tuple[str, str]] = [
    ("no LIMIT", "SELECT COUNT(*) AS metric FROM refunds"),
    ("multi statement", "SELECT COUNT(*) AS metric FROM refunds LIMIT 5; SELECT 1"),
    ("drop table", "SELECT COUNT(*) AS metric FROM refunds LIMIT 5; DROP TABLE refunds"),
    ("star select", "SELECT * FROM refunds LIMIT 5"),
    ("join", "SELECT o.id FROM orders o JOIN refunds r ON r.order_id = o.id LIMIT 5"),
    (
        "subquery",
        "SELECT COUNT(*) AS metric FROM refunds "
        "WHERE order_id IN (SELECT id FROM orders) LIMIT 5",
    ),
    (
        "scope column written by hand",
        "SELECT COUNT(*) AS metric FROM refunds WHERE tenant_id = 'acme' LIMIT 5",
    ),
    ("unknown column", "SELECT COUNT(*) AS metric FROM refunds WHERE secret = 1 LIMIT 5"),
    (
        "dangerous function",
        "SELECT COUNT(*) AS metric FROM refunds WHERE load_extension('x') LIMIT 5",
    ),
    ("comment", "SELECT COUNT(*) AS metric FROM refunds -- trailing\n LIMIT 5"),
    (
        "having",
        "SELECT COUNT(*) AS metric FROM refunds GROUP BY status HAVING COUNT(*) > 1 LIMIT 5",
    ),
]

ALICE = Identity(tenant_id="demo", user_id="alice", roles=frozenset())
HEADERS = {"X-Tenant-Id": "demo", "X-User-Id": "alice"}


def _answer(app: Any, question: str) -> dict[str, Any]:
    """One question through the analytics service, committed like the agent does."""
    with app.state.session_factory() as session:
        try:
            answer = app.state.analytics.answer(question, ALICE, session)
        except SqlRejected as exc:
            # The turn commits either way; so does the agent's session.
            session.commit()
            return {"refused": f"{exc.reason}: {exc.detail}"}
        session.commit()
        return {
            "plan": asdict(answer.plan),
            "sql": answer.sql,
            "rows": list(answer.rows),
            "duration_ms": answer.duration_ms,
            "query_id": answer.query_id,
        }


def _ledger(session_factory: Any) -> list[str]:
    with session_factory() as session:
        rows = session.scalars(select(SqlQuery).order_by(SqlQuery.created_at)).all()
    return [
        f"{row.status:9} {row.rejected_reason or '-':18} dataset={row.dataset or '-':8} "
        f"rows={row.row_count} digest={row.params_digest[:12]} question={row.question[:20]}"
        for row in rows
    ]


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="ticketpilot-sql-evidence-"))
    database_url = f"sqlite+pysqlite:///{directory / 'evidence.db'}"
    try:
        return _run(database_url)
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _run(database_url: str) -> int:
    settings = Settings(
        environment="test",
        database_url=database_url,
        auto_create_tables=True,
    )
    app = create_app(settings)
    seed_all(app.state.session_factory)
    apply_fixtures(app.state.session_factory)

    print("== worked examples (planner -> builder -> guard -> read-only execution) ==")
    for question in QUESTIONS:
        result = _answer(app, question)
        print(f"\nQ: {question}")
        if "refused" in result:
            print(f"   NO QUERY RUN: {result['refused']}")
            continue
        print("   plan   ", json.dumps(result["plan"], ensure_ascii=False, default=str))
        print("   sql    ", result["sql"])
        print(
            f"   answer {json.dumps(result['rows'], ensure_ascii=False, default=str)}"
            f"  duration_ms={result['duration_ms']}"
        )

    print("\n== the ledger the same run wrote ==")
    for line in _ledger(app.state.session_factory):
        print(line)

    print("\n== refusal matrix (guard.validate on statements a caller could try) ==")
    guard = SqlGuard()
    accepted: list[str] = []
    for label, sql in HOSTILE:
        try:
            guard.validate(sql)
        except SqlRejected as exc:
            print(f"refused  {label:28} -> {exc.reason}: {exc.detail}")
        else:
            accepted.append(label)
            print(f"ACCEPTED {label:28} (unexpected)")

    print("\n== a refusal from the outside ==")
    client = TestClient(app)
    response = client.post(
        "/api/v1/analytics/query", headers=HEADERS, json={"question": "退货运费谁出"}
    )
    print(f"POST /api/v1/analytics/query -> {response.status_code}")
    print("   ", json.dumps(response.json(), ensure_ascii=False)[:220])
    print("\n== the same questions read by the customer (agent turn) ==")
    for question in (
        "我今年退了多少款",
        "我今年退了几笔款",
        "退货运费谁出",
        # The router sends this to analytics ("退款" + a count), and the
        # planner refuses it: 已发货 is an order status, so the question mixes
        # two record sets and no honest query describes it.
        "我今年退了几笔已发货的单",
        # The heuristic boundary: the question asks about products, the
        # planner only knows status/month/day, and the answer is a status
        # breakdown instead.
        "我的退款按商品分布",
    ):
        turn = client.post(
            "/api/v1/support/messages", headers=HEADERS, json={"message": question}
        )
        body = turn.json()
        print(f"Q: {question}")
        print(f"   -> {turn.status_code} {body.get('reply')}")
        print(
            f"    intent={body.get('intent')} "
            f"resolved={(body.get('slots') or {}).get('resolved')} "
            f"handoff={bool(body.get('handoff'))}"
        )
    print("\n== ledger after the HTTP turns ==")
    for line in _ledger(app.state.session_factory):
        print(line)

    app.state.engine.dispose()
    if accepted:
        print(f"\n{len(accepted)} statement(s) were wrongly accepted: {accepted}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
