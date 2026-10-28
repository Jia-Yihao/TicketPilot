"""Pins the intent gap loop: dedup, human gate, corpus merge and report honesty.

The tests do not judge the classifier. They assert that a missed sentence
cannot quietly become gold: the queue dedups by normalized text, tenants only
survive as hashes, a model proposal never changes status, closing requires an
explicit human confirmation and a note, closures enter the same held-out
layer, and the cycle report keeps the layers apart.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

import evals.intent_gaps as gaps
import scripts.manage_intent_gaps as cli
from scripts.build_intent_set import LABELS, build

ROOT = Path(__file__).resolve().parent.parent
CYCLE = "2026-10-26"
NEXT_CYCLE = "2026-10-27"


def _record(text: str, **overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "text": text,
        "predicted": "UNKNOWN",
        "tenant_id": "demo",
        "signals": ["false_unknown"],
        "provenance": {"dataset": "fixture", "file": "fixture.jsonl", "line": 1},
    }
    record.update(overrides)
    return record


def _one(text: str = "我的退款到哪了", **overrides: Any) -> dict[str, Any]:
    queue, _ = gaps.ingest(gaps.new_queue(), [_record(text, **overrides)], cycle=CYCLE)
    return queue


def _case(queue: dict[str, Any], gap_id: str) -> dict[str, Any]:
    return next(case for case in queue["cases"] if case["id"] == gap_id)


def _metrics(
    layers: dict[str, dict[str, Any]],
    *,
    ok: bool = True,
    failures: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "layers": layers,
        "invariants": {"ok": ok, "failures": failures or []},
    }


# ------------------------------------------------------------------ normalise
def test_normalisation_ignores_case_space_and_punctuation() -> None:
    assert gaps.normalize("Where  is my ORDER??") == "where is my order"
    assert gaps.normalize("　我刚拍的，刀纸 地址选错了！ ") == "我刚拍的刀纸 地址选错了"
    assert gaps.dedup_key("A b") == gaps.dedup_key("a  B!")
    # Collapsing rather than deleting spaces keeps "a b" and "ab" apart.
    assert gaps.dedup_key("a b") != gaps.dedup_key("ab")


# --------------------------------------------------------------------- ingest
def test_ingest_dedups_the_same_text_and_counts_observations() -> None:
    queue, first = gaps.ingest(
        gaps.new_queue(), [_record("我的退款到哪了")], cycle=CYCLE
    )
    queue, second = gaps.ingest(
        queue, [_record("我的退款到哪了！")], cycle=NEXT_CYCLE
    )

    assert first == {"added": 1, "merged": 0, "skipped": 0}
    assert second == {"added": 0, "merged": 1, "skipped": 0}
    assert len(queue["cases"]) == 1
    case = queue["cases"][0]
    assert case["status"] == "OPEN"
    assert case["observations"] == 2
    assert case["first_cycle"] == CYCLE
    assert case["last_cycle"] == NEXT_CYCLE
    assert case["label"] is None
    assert case["dedup_key"] == gaps.dedup_key("我的退款到哪了")


def test_cross_tenant_duplicates_merge_without_writing_raw_tenants() -> None:
    queue, stats = gaps.ingest(
        gaps.new_queue(),
        [
            _record(
                "同一句",
                tenant_id="tenant-a",
                provenance={
                    "dataset": "fixture",
                    "file": "fixture.jsonl",
                    "line": 2,
                    "tenant_id": "tenant-a",
                },
            ),
            _record("同一句", tenant_id="tenant-b"),
        ],
        cycle=CYCLE,
    )

    assert stats == {"added": 1, "merged": 1, "skipped": 0}
    case = queue["cases"][0]
    assert len(case["tenant_hashes"]) == 2
    assert all(len(value) == 64 for value in case["tenant_hashes"])
    blob = gaps.render_queue(queue)
    assert "tenant-a" not in blob
    assert "tenant-b" not in blob


def test_ingest_reads_the_last_user_message_and_skips_non_gaps() -> None:
    queue, stats = gaps.ingest(
        gaps.new_queue(),
        [
            {
                "predicted": "UNKNOWN",
                "messages": [
                    {"role": "system", "content": "ignored"},
                    {"role": "user", "content": "你好"},
                    {"role": "assistant", "content": "在的"},
                    {"role": "user", "content": "我的退款到哪了"},
                ],
            },
            _record("我要退款", intent="REFUND_REQUEST"),
            {"messages": [{"role": "assistant", "content": "没有用户话"}]},
        ],
        cycle=CYCLE,
    )

    assert stats == {"added": 1, "merged": 0, "skipped": 2}
    assert [case["text"] for case in queue["cases"]] == ["我的退款到哪了"]


def test_ingest_skips_records_the_classifier_already_labelled() -> None:
    queue, stats = gaps.ingest(
        gaps.new_queue(),
        [_record("我要退款", intent="REFUND_REQUEST")],
        cycle=CYCLE,
    )

    assert stats == {"added": 0, "merged": 0, "skipped": 1}
    assert queue["cases"] == []


def test_a_terminal_gap_is_not_reopened_by_a_later_observation() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    queue = gaps.close(
        queue,
        gap_id,
        label="REFUND_STATUS",
        note="问退款进度，属于退款状态",
        by="reviewer",
        cycle=CYCLE,
        confirm_human=True,
    )

    queue, stats = gaps.ingest(
        queue,
        [_record("我的退款到哪了", tenant_id="tenant-b")],
        cycle=NEXT_CYCLE,
    )

    case = _case(queue, gap_id)
    assert stats["merged"] == 1
    assert case["status"] == "CLOSED"
    assert case["label"] == "REFUND_STATUS"
    assert case["observations"] == 2
    assert case["last_cycle"] == NEXT_CYCLE
    assert len(case["tenant_hashes"]) == 2


# ------------------------------------------------------- proposals vs humans
def test_a_model_proposal_never_sets_gold_or_closes_the_gap() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    before = gaps.render_queue(queue)

    proposed = gaps.propose(
        queue,
        gap_id,
        label="LOGISTICS_QUERY",
        note="模型认为是物流问题",
        by="model:qwen3-8b",
        cycle=CYCLE,
    )

    case = _case(proposed, gap_id)
    assert case["status"] == "OPEN"
    assert case["label"] is None
    assert case["confirmed_by"] is None
    assert case["proposals"] == [
        {
            "source": "model",
            "label": "LOGISTICS_QUERY",
            "note": "模型认为是物流问题",
            "by": "model:qwen3-8b",
            "cycle": CYCLE,
        }
    ]
    assert gaps.closure_rows(proposed) == []
    assert gaps.render_queue(queue) == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirm_human": False},
        {"note": "   "},
        {"by": "  "},
    ],
)
def test_close_requires_human_confirmation_note_and_author(
    overrides: dict[str, Any],
) -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    before = gaps.render_queue(queue)
    kwargs: dict[str, Any] = {
        "label": "REFUND_STATUS",
        "note": "问退款进度",
        "by": "reviewer",
        "cycle": CYCLE,
        "confirm_human": True,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError):
        gaps.close(queue, gap_id, **kwargs)

    assert gaps.render_queue(queue) == before
    assert gaps.closure_rows(queue) == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"confirm_human": False},
        {"note": "   "},
        {"by": "  "},
    ],
)
def test_ignore_requires_human_confirmation_note_and_author(
    overrides: dict[str, Any],
) -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    before = gaps.render_queue(queue)
    kwargs: dict[str, Any] = {
        "note": "本来就该转人工",
        "by": "reviewer",
        "cycle": CYCLE,
        "confirm_human": True,
    }
    kwargs.update(overrides)

    with pytest.raises(ValueError):
        gaps.ignore(queue, gap_id, **kwargs)

    assert gaps.render_queue(queue) == before
    assert _case(queue, gap_id)["status"] == "OPEN"


def test_close_rejects_an_unknown_label() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]

    with pytest.raises(ValueError):
        gaps.close(
            queue,
            gap_id,
            label="NOT_A_LABEL",
            note="随便写",
            by="reviewer",
            cycle=CYCLE,
            confirm_human=True,
        )

    assert _case(queue, gap_id)["status"] == "OPEN"


def test_close_produces_a_closure_row_from_the_same_corpus() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    queue = gaps.close(
        queue,
        gap_id,
        label="REFUND_STATUS",
        note="问退款进度，属于退款状态",
        by="reviewer",
        cycle=CYCLE,
        confirm_human=True,
    )

    rows = gaps.closure_rows(queue)

    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == gap_id
    assert row["gold"] == "REFUND_STATUS"
    assert row["layer"] == "authored_gap"
    assert row["label_source"] == "human"
    assert row["note"] == "问退款进度，属于退款状态"
    assert row["provenance"] == {
        "gap_id": gap_id,
        "source": "intent-gap-loop",
        "cycle": CYCLE,
    }
    assert _case(queue, gap_id)["confirmation"] == "human"


def test_ignore_is_terminal_and_never_enters_the_corpus() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    queue = gaps.ignore(
        queue,
        gap_id,
        note="这句话本来就该转人工，不是缺口",
        by="reviewer",
        cycle=CYCLE,
        confirm_human=True,
    )

    case = _case(queue, gap_id)
    assert case["status"] == "IGNORED"
    assert case["label"] is None
    assert case["note"] == "这句话本来就该转人工，不是缺口"
    assert gaps.closure_rows(queue) == []


# --------------------------------------------------------------------- report
def test_report_keeps_layers_apart_and_shows_the_cycle_delta() -> None:
    queue = _one("我的退款到哪了")
    gap_id = queue["cases"][0]["id"]
    queue = gaps.close(
        queue,
        gap_id,
        label="REFUND_STATUS",
        note="问退款进度",
        by="reviewer",
        cycle=CYCLE,
        confirm_human=True,
    )
    queue, _ = gaps.ingest(
        queue, [_record("什么时候发货")], cycle=CYCLE
    )
    before = _metrics(
        {
            "external": {"cases": 201, "coverage": 0.5473, "false_unknown": 91},
            "authored_gap": {"cases": 144, "coverage": 0.5, "false_unknown": 60},
        }
    )
    after = _metrics(
        {
            "external": {"cases": 201, "coverage": 0.5473, "false_unknown": 91},
            "authored_gap": {"cases": 145, "coverage": 0.51, "false_unknown": 60},
        }
    )

    report = gaps.build_report(queue, before=before, after=after, cycle=CYCLE)

    assert report["queue"] == {
        "total": 2,
        "OPEN": 1,
        "CLOSED": 1,
        "IGNORED": 0,
    }
    assert report["new_cases"] == 1
    assert report["closed_this_cycle"] == 1
    assert [item["id"] for item in report["open_gaps"]] == [
        item["id"] for item in queue["cases"] if item["status"] == "OPEN"
    ]
    assert report["layers"]["external"]["delta"] == {
        "cases": 0,
        "coverage": 0.0,
        "false_unknown": 0,
    }
    assert report["layers"]["authored_gap"]["before"]["cases"] == 144
    assert report["layers"]["authored_gap"]["after"]["cases"] == 145
    assert report["layers"]["authored_gap"]["delta"] == {
        "cases": 1,
        "coverage": 0.01,
        "false_unknown": 0,
    }
    assert "accuracy" not in report
    assert report["gate_regression"] is False


def test_report_flags_a_broken_gate_after_the_change() -> None:
    queue = _one()
    before = _metrics(
        {"external": {"cases": 201, "coverage": 0.5, "false_unknown": 91}}
    )
    after = _metrics(
        {"external": {"cases": 201, "coverage": 0.49, "false_unknown": 92}},
        ok=False,
        failures=["external: cost buckets sum to 199, expected 201"],
    )

    report = gaps.build_report(queue, before=before, after=after, cycle=CYCLE)

    assert report["gate_regression"] is True
    assert report["gate_failures"] == [
        "external: cost buckets sum to 199, expected 201"
    ]


def test_queue_report_and_closures_render_deterministically() -> None:
    queue = _one()
    gap_id = queue["cases"][0]["id"]
    queue = gaps.close(
        queue,
        gap_id,
        label="REFUND_STATUS",
        note="问退款进度",
        by="reviewer",
        cycle=CYCLE,
        confirm_human=True,
    )
    report = gaps.build_report(queue, cycle=CYCLE)
    rows = gaps.closure_rows(queue)

    assert gaps.render_queue(queue) == gaps.render_queue(copy.deepcopy(queue))
    assert gaps.render_report(report) == gaps.render_report(copy.deepcopy(report))
    assert gaps.render_closures(rows) == gaps.render_closures(
        [dict(row) for row in rows]
    )
    assert gaps.render_queue(queue).endswith("\n")
    assert gaps.render_report(report).endswith("\n")
    assert gaps.render_closures(rows).endswith("\n")


# ------------------------------------------------------------ corpus integration
def test_queue_render_round_trips_through_the_loader(tmp_path: Path) -> None:
    queue = _one()
    path = tmp_path / "queue.json"
    path.write_text(gaps.render_queue(queue), encoding="utf-8", newline="\n")

    assert gaps.render_queue(gaps.load_queue(path)) == gaps.render_queue(queue)


def test_report_and_closures_do_not_depend_on_ingest_order() -> None:
    records = [_record("甲句"), _record("乙句")]

    def closed(order: list[dict[str, Any]]) -> dict[str, Any]:
        queue, _ = gaps.ingest(gaps.new_queue(), order, cycle=CYCLE)
        return gaps.close(
            queue,
            gaps.gap_id("甲句"),
            label="POLICY_QA",
            note="问政策",
            by="reviewer",
            cycle=CYCLE,
            confirm_human=True,
        )

    first = closed(records)
    second = closed(list(reversed(records)))

    assert gaps.render_queue(first) == gaps.render_queue(second)
    assert gaps.render_closures(gaps.closure_rows(first)) == gaps.render_closures(
        gaps.closure_rows(second)
    )
    assert gaps.render_report(gaps.build_report(first, cycle=CYCLE)) == (
        gaps.render_report(gaps.build_report(second, cycle=CYCLE))
    )


def test_committed_queue_and_closures_are_valid_and_reproducible() -> None:
    queue = gaps.load_queue(gaps.QUEUE_PATH)
    rows = gaps.closure_rows(queue)
    committed = gaps.CLOSURES_PATH.read_text(encoding="utf-8")

    assert gaps.render_queue(queue) == gaps.QUEUE_PATH.read_text(encoding="utf-8")
    assert gaps.render_closures(rows) == committed
    assert queue["statuses"] == ["OPEN", "CLOSED", "IGNORED"]
    assert {case["status"] for case in queue["cases"]} <= set(gaps.STATUSES)


def test_committed_before_snapshot_reproduces_the_report_before_side() -> None:
    report = json.loads(gaps.REPORT_PATH.read_text(encoding="utf-8"))
    before = json.loads(gaps.BEFORE_METRICS_PATH.read_text(encoding="utf-8"))

    assert before["invariants"] == {"ok": True, "failures": []}
    for layer, values in report["layers"].items():
        assert values["before"] == {
            key: before["layers"][layer][key]
            for key in ("cases", "coverage", "false_unknown")
        }


def test_builder_merges_closures_into_the_authored_gap_layer() -> None:
    dataset = build()
    closures = [
        case
        for case in dataset["cases"]
        if case["layer"] == "authored_gap" and "gap_id" in case["provenance"]
    ]

    assert len(closures) == 1, closures
    row = closures[0]
    assert row["provenance"]["gap_id"] == row["id"]
    assert row["provenance"]["source"] == "intent-gap-loop"
    assert row["label_source"] == "human"
    assert row["gold"] in LABELS
    assert dataset["layers"]["authored_gap"]["cases"] == 145
    assert len(dataset["cases"]) == 346
    assert dataset["sources"]["gap_closures"] == "gap_closures.jsonl"
    assert dataset["sources"]["gap_closures_sha256"] == hashlib.sha256(
        gaps.CLOSURES_PATH.read_bytes()
    ).hexdigest()


def test_builder_rejects_duplicate_text_across_layers() -> None:
    from scripts import build_intent_set

    rows = [
        {"id": "a", "text": "同一句", "layer": "external"},
        {"id": "b", "text": "同一句", "layer": "authored_gap"},
    ]

    with pytest.raises(SystemExit, match="duplicate text"):
        build_intent_set.assert_unique_texts(rows)


def test_builder_rejects_a_normalized_duplicate_across_layers() -> None:
    from scripts import build_intent_set

    rows = [
        {"id": "a", "text": "我的退款到哪了！", "layer": "external"},
        {"id": "b", "text": "我的退款到哪了", "layer": "authored_gap"},
    ]

    with pytest.raises(SystemExit, match="duplicate text"):
        build_intent_set.assert_unique_texts(rows)


# ------------------------------------------------------------------------- CLI
def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records),
        encoding="utf-8",
        newline="\n",
    )


def test_cli_close_without_confirmation_exits_nonzero_and_writes_nothing(
    tmp_path: Path,
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    queue_path = tmp_path / "queue.json"
    closures_path = tmp_path / "closures.jsonl"
    _write_jsonl(candidates, [_record("我的退款到哪了")])
    assert (
        cli.main(
            [
                "ingest",
                str(candidates),
                "--cycle",
                CYCLE,
                "--queue",
                str(queue_path),
            ]
        )
        == 0
    )
    before = queue_path.read_text(encoding="utf-8")
    gap_id = json.loads(before)["cases"][0]["id"]

    code = cli.main(
        [
            "close",
            gap_id,
            "--label",
            "REFUND_STATUS",
            "--note",
            "问退款进度",
            "--by",
            "reviewer",
            "--cycle",
            CYCLE,
            "--queue",
            str(queue_path),
            "--closures",
            str(closures_path),
        ]
    )

    assert code == 2
    assert queue_path.read_text(encoding="utf-8") == before
    assert not closures_path.exists()


def _ingest_one(candidates: Path, queue_path: Path) -> str:
    _write_jsonl(candidates, [_record("我的退款到哪了")])
    assert (
        cli.main(
            ["ingest", str(candidates), "--cycle", CYCLE, "--queue", str(queue_path)]
        )
        == 0
    )
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    return str(data["cases"][0]["id"])


def _close_args(
    gap_id: str, queue_path: Path, closures_path: Path
) -> list[str]:
    return [
        "close",
        gap_id,
        "--label",
        "REFUND_STATUS",
        "--note",
        "问退款进度",
        "--by",
        "reviewer",
        "--cycle",
        CYCLE,
        "--confirm-human",
        "--queue",
        str(queue_path),
        "--closures",
        str(closures_path),
    ]


def test_cli_close_does_not_leave_orphan_closures_when_the_queue_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    queue_path = tmp_path / "queue.json"
    closures_path = tmp_path / "closures.jsonl"
    gap_id = _ingest_one(candidates, queue_path)
    before = queue_path.read_text(encoding="utf-8")
    real_write = cli._write

    def failing_write(path: Path, text: str) -> None:
        if path == queue_path:
            raise OSError("queue write failed")
        real_write(path, text)

    monkeypatch.setattr(cli, "_write", failing_write)

    assert cli.main(_close_args(gap_id, queue_path, closures_path)) == 2
    assert "queue write failed" in capsys.readouterr().err
    assert queue_path.read_text(encoding="utf-8") == before
    assert not closures_path.exists()


def test_cli_close_rolls_back_the_queue_when_closures_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    queue_path = tmp_path / "queue.json"
    closures_path = tmp_path / "closures.jsonl"
    gap_id = _ingest_one(candidates, queue_path)
    before = queue_path.read_text(encoding="utf-8")
    real_write = cli._write

    def failing_write(path: Path, text: str) -> None:
        if path == closures_path:
            raise OSError("closures write failed")
        real_write(path, text)

    monkeypatch.setattr(cli, "_write", failing_write)

    assert cli.main(_close_args(gap_id, queue_path, closures_path)) == 2
    assert "closures write failed" in capsys.readouterr().err
    assert queue_path.read_text(encoding="utf-8") == before
    assert not closures_path.exists()


def test_cli_close_reports_a_missing_queue_instead_of_an_unknown_gap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    queue_path = tmp_path / "queue.json"
    closures_path = tmp_path / "closures.jsonl"

    code = cli.main(_close_args("gap-0123456789ab", queue_path, closures_path))

    assert code == 2
    assert "queue not found" in capsys.readouterr().err
    assert not queue_path.exists()


def test_cli_round_trip_ingest_propose_close_and_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    candidates = tmp_path / "candidates.jsonl"
    queue_path = tmp_path / "queue.json"
    closures_path = tmp_path / "closures.jsonl"
    report_path = tmp_path / "report.json"
    before_path = tmp_path / "before.json"
    after_path = tmp_path / "after.json"
    _write_jsonl(candidates, [_record("我的退款到哪了")])
    before_path.write_text(
        json.dumps(
            _metrics(
                {
                    "external": {
                        "cases": 201,
                        "coverage": 0.5,
                        "false_unknown": 91,
                    }
                }
            )
        ),
        encoding="utf-8",
    )
    after_path.write_text(
        json.dumps(
            _metrics(
                {
                    "external": {
                        "cases": 201,
                        "coverage": 0.49,
                        "false_unknown": 92,
                    }
                },
                ok=False,
                failures=["external: broken"],
            )
        ),
        encoding="utf-8",
    )
    common = ["--queue", str(queue_path), "--closures", str(closures_path)]

    assert cli.main(["ingest", str(candidates), "--cycle", CYCLE, *common]) == 0
    gap_id = json.loads(queue_path.read_text(encoding="utf-8"))["cases"][0]["id"]
    assert (
        cli.main(
            [
                "propose",
                gap_id,
                "--label",
                "REFUND_STATUS",
                "--note",
                "模型提议",
                "--by",
                "model:qwen3-8b",
                "--cycle",
                CYCLE,
                *common,
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "close",
                gap_id,
                "--label",
                "REFUND_STATUS",
                "--note",
                "问退款进度",
                "--by",
                "reviewer",
                "--confirm-human",
                "--cycle",
                CYCLE,
                *common,
            ]
        )
        == 0
    )
    assert (
        cli.main(
            [
                "report",
                "--before",
                str(before_path),
                "--after",
                str(after_path),
                "--cycle",
                CYCLE,
                "--output",
                str(report_path),
                "--queue",
                str(queue_path),
            ]
        )
        == 0
    )

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert report_text == gaps.render_report(report)
    assert report["queue"]["CLOSED"] == 1
    assert report["gate_regression"] is True
    assert closures_path.read_text(encoding="utf-8").count("\n") == 1
    assert "CLOSED=1" in capsys.readouterr().out
