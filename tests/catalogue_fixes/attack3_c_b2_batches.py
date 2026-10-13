# ruff: noqa
"""Attack H1(c) for B2 / DEC-10: the review sends candidates in batches.

Derived (before the implementation was opened) from artifacts/engineer-fixes.md B2 ("consider
reviewing candidates in batches instead of one call of 20"; check: the review of 21 candidates
completes) and the HANDOFF text of DEC-10: "review candidates in batches (configured size,
default 5)"; row B2: "`batch_size` (default 5) in ReviewConfig; first failing batch ends the
review and keeps earlier decisions". Names are invented and generic.

Run by path (not collected by default):
    uv run pytest tests/catalogue_fixes/attack3_c_b2_batches.py -q -p no:cacheprovider
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from catalogue_fixture_kit import MODEL_KEY, build_snapshot, builder_run, candidates
from dbig_builder.config import ReviewConfig
from dbig_contracts.artifact import read_artifact
from pydantic import ValidationError

CANARY = "CANARY-quokka-7731"


def child_tables(count: int, prefix: str = "zorblax") -> dict:
    tables = {"vendor": {"fields": [("id", "integer")], "key": ["id"]}}
    for number in range(count):
        tables[f"{prefix}{number:02d}"] = {
            "fields": [("id", "integer"), ("vendor_id", "integer")],
            "key": ["id"],
        }
    return tables


class Script:
    """A model stand-in whose behaviour is chosen per call number (1-based).

    `plan(call_number, candidates)` returns one of
      "ok"                      supports every candidate with a valid citation
      ("status", code)          forces an HTTP status
      "length"                  finish_reason length and empty content
      "garbage"                 200 with content that is not the schema (carries CANARY)
      "badcite"                 valid shape, invented evidence id
      ("sleep", seconds)        holds the reply back, then answers ok
      "foreign"                 decisions for ids of a candidate NOT in this batch
      "missing"                 drops the last decision of the batch
      "duplicate"               repeats the first decision
    """

    def __init__(self, plan=lambda n, c: "ok"):
        self.calls: list[list[dict]] = []
        self.active = 0
        self.max_active = 0
        script = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                batch = json.loads(body["messages"][-1]["content"])
                script.calls.append(batch)
                number = len(script.calls)
                script.active += 1
                script.max_active = max(script.max_active, script.active)
                try:
                    action = plan(number, batch)
                    if isinstance(action, tuple) and action[0] == "sleep":
                        time.sleep(action[1])
                        action = "ok"
                    if isinstance(action, tuple) and action[0] == "status":
                        return self._send(action[1], {"error": {"message": CANARY}}, "stop")
                    if action == "length":
                        return self._send(200, None, "length", "")
                    if action == "garbage":
                        return self._send(200, None, "stop", f"{CANARY} sure, these look related")
                    decisions = [
                        {"candidate_id": c["candidate_id"], "decision": "supports",
                         "cited_evidence_ids": [c["evidence"][0]["id"]], "reason": "names agree"}
                        for c in batch
                    ]
                    if action == "badcite":
                        decisions[0]["cited_evidence_ids"] = [f"{CANARY}-invented"]
                    if action == "foreign":
                        decisions[0]["candidate_id"] = "not-in-this-batch-" + CANARY
                    if action == "missing":
                        decisions = decisions[:-1]
                    if action == "duplicate":
                        decisions = decisions + decisions[:1]
                    self._send(200, None, "stop", json.dumps({"decisions": decisions}))
                finally:
                    script.active -= 1

            def _send(self, code, payload, finish, content=""):
                if payload is None:
                    payload = {
                        "id": "c", "object": "chat.completion", "created": 0, "model": "m",
                        "choices": [{"index": 0, "finish_reason": finish,
                                     "message": {"role": "assistant", "content": content}}],
                    }
                raw = json.dumps(payload).encode()
                try:
                    self.send_response(code)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except OSError:
                    pass

        class Server(ThreadingHTTPServer):
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def ids(self, call: int) -> list[str]:
        return [c["candidate_id"] for c in self.calls[call - 1]]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def review_config(script, **extra):
    extra.setdefault("timeout_seconds", 5)
    return ReviewConfig(
        base_url=script.url, model="m", model_digest="d", api_key_env="MODEL_KEY", **extra
    )


def run_review(tmp_path, monkeypatch, count, plan=lambda n, c: "ok", *, name="s", **review):
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    with Script(plan) as script:
        snapshot = build_snapshot(
            tmp_path, monkeypatch, {"alpha": {"tables": child_tables(count)}},
            review=review_config(script, **review), name=name,
        )
        calls = [list(c) for c in script.calls]
        ids = [[c["candidate_id"] for c in call] for call in calls]
        max_active = script.max_active
    run = builder_run(snapshot)
    manifest, data = read_artifact(snapshot)
    suggestions = [
        item.model_dump() for item in data["evidence.jsonl"]
        if item.model_dump()["kind"] == "model_suggestion"
    ]
    return {
        "snapshot": snapshot, "calls": calls, "ids": ids, "status": run["review_status"],
        "reasons": run.get("partial_reasons", []), "completeness": manifest.completeness,
        "suggestions": suggestions, "max_active": max_active, "run": run, "manifest": manifest,
    }


def reviewed_edges(result) -> set[str]:
    """Candidate ids that ended with a model_suggestion (via the evidence's own record)."""
    _, data = read_artifact(result["snapshot"])
    out = set()
    for item in data["evidence.jsonl"]:
        dump = item.model_dump()
        if dump["kind"] == "model_suggestion":
            out.add(json.dumps(dump, sort_keys=True))
    return out


# --- fixture sanity -------------------------------------------------------------------------------


@pytest.mark.parametrize("count", [1, 5, 6, 11, 20])
def test_fixture_makes_exactly_that_many_candidates(tmp_path, monkeypatch, count):
    snap = build_snapshot(tmp_path, monkeypatch, {"alpha": {"tables": child_tables(count)}})
    assert len(candidates(snap)) == count


# --- batch boundaries -----------------------------------------------------------------------------


@pytest.mark.parametrize("count", [0, 1, 4, 5, 6, 9, 10, 11, 15, 16, 20])
def test_default_batch_size_is_five_and_batches_partition_the_candidates(
    tmp_path, monkeypatch, count
):
    result = run_review(tmp_path, monkeypatch, count)
    assert len(result["calls"]) == math.ceil(count / 5), result["ids"]
    assert all(1 <= len(call) <= 5 for call in result["ids"])
    flat = [i for call in result["ids"] for i in call]
    assert len(flat) == len(set(flat)) == count, "every candidate exactly once"
    assert result["status"] == ("OK" if count else "NO_CANDIDATES")
    assert result["completeness"] == "complete"
    assert len(result["suggestions"]) == count
    # full batches first, remainder last
    sizes = [len(c) for c in result["ids"]]
    assert sizes == sorted(sizes, reverse=True)


def test_zero_candidates_means_zero_model_calls_and_no_failure(tmp_path, monkeypatch):
    result = run_review(tmp_path, monkeypatch, 0)
    assert result["calls"] == [] and result["completeness"] == "complete"
    assert "MODEL_REVIEW_FAILED" not in result["reasons"]


@pytest.mark.parametrize(
    ("count", "size", "expected_sizes"),
    [
        (1, 1, [1]), (3, 1, [1, 1, 1]), (3, 3, [3]), (4, 3, [3, 1]),
        (7, 2, [2, 2, 2, 1]), (5, 20, [5]), (5, 5, [5]), (6, 5, [5, 1]), (20, 20, [20]),
    ],
)
def test_configured_batch_size_is_honoured(tmp_path, monkeypatch, count, size, expected_sizes):
    result = run_review(tmp_path, monkeypatch, count, batch_size=size)
    assert [len(c) for c in result["ids"]] == expected_sizes
    assert result["completeness"] == "complete" and len(result["suggestions"]) == count


def test_max_candidates_still_caps_the_total_before_batching(tmp_path, monkeypatch):
    result = run_review(tmp_path, monkeypatch, 13, max_candidates=7, batch_size=5)
    assert [len(c) for c in result["ids"]] == [5, 2]
    assert result["completeness"] == "complete"


def test_default_cap_of_twenty_with_twenty_five_candidates(tmp_path, monkeypatch):
    result = run_review(tmp_path, monkeypatch, 25)
    assert [len(c) for c in result["ids"]] == [5, 5, 5, 5]


def test_the_report_case_21_candidates_complete_with_default_config(tmp_path, monkeypatch):
    """B2's check: 21 candidates, shipped defaults except that the cap is lifted to hold all."""
    result = run_review(tmp_path, monkeypatch, 21, max_candidates=21)
    assert result["completeness"] == "complete" and len(result["suggestions"]) == 21
    assert [len(c) for c in result["ids"]] == [5, 5, 5, 5, 1]


def test_old_behaviour_negative_default_is_not_one_call_of_all(tmp_path, monkeypatch):
    """Fails on the pre-change code, which sent all 12 candidates in one request."""
    result = run_review(tmp_path, monkeypatch, 12)
    assert len(result["calls"]) == 3
    assert max(len(c) for c in result["ids"]) <= 5


def test_batches_are_sequential_deterministic_and_each_request_is_self_contained(
    tmp_path, monkeypatch
):
    first = run_review(tmp_path, monkeypatch, 12, name="a")
    second = run_review(tmp_path, monkeypatch, 12, name="b")
    assert first["max_active"] == 1
    assert [sorted(c) for c in first["ids"]] == [sorted(c) for c in second["ids"]]
    assert first["ids"] == second["ids"], "same input, same batch order"
    # no candidate id of another batch leaks into a batch's request
    for call_a, call_b in zip(first["calls"], first["calls"][1:]):
        ids_a = {c["candidate_id"] for c in call_a}
        assert not ids_a & {c["candidate_id"] for c in call_b}
    # the same snapshot twice: reproducible suggestions count
    assert len(first["suggestions"]) == len(second["suggestions"]) == 12


# --- configuration --------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -5])
def test_batch_size_must_be_positive(bad):
    with pytest.raises(ValidationError):
        ReviewConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d",
                     api_key_env="MODEL_KEY", batch_size=bad)


def test_batch_size_default_is_five_and_toml_loads(tmp_path, monkeypatch):
    base = ReviewConfig(base_url="http://127.0.0.1:1/v1", model="m", model_digest="d",
                        api_key_env="MODEL_KEY")
    assert base.batch_size == 5
    from click.testing import CliRunner
    from dbig_builder.cli import main
    from catalogue_fixture_kit import write_bundle, BUNDLE_KEY

    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    bundle = tmp_path / "bundle"
    write_bundle(bundle, "alpha", "postgresql", child_tables(7))
    with Script() as script:
        cfg = tmp_path / "b.toml"
        cfg.write_text(
            '[builder]\nsigning_keys = { test = "BUNDLE_KEY" }\n[builder.review]\n'
            f'base_url = "{script.url}"\nmodel = "m"\nmodel_digest = "d"\n'
            'api_key_env = "MODEL_KEY"\nbatch_size = 3\n', encoding="utf-8")
        result = CliRunner().invoke(
            main, ["assemble", "--config", str(cfg), "--bundle", str(bundle),
                   "--out", str(tmp_path / "snap")])
        sizes = [len(c) for c in script.calls]
    assert result.exit_code == 0, result.output
    assert sizes == [3, 3, 1]


# --- failure inside a batch ----------------------------------------------------------------------


FAILURES = {
    "status400": (("status", 400), "MODEL_REJECTED_REQUEST"),
    "length": ("length", "MODEL_TRUNCATED"),
    "garbage": ("garbage", None),   # any code but never the old umbrella if a specific one exists
    "badcite": ("badcite", "MODEL_INVALID_CITATION"),
    "timeout": (("sleep", 3), "MODEL_TIMEOUT"),
}


def failing_on(call_number, kind):
    action = FAILURES[kind][0]
    return lambda n, c: action if n == call_number else "ok"


@pytest.mark.parametrize("kind", list(FAILURES))
@pytest.mark.parametrize(("count", "size", "fail_at"), [(12, 5, 2), (12, 5, 3), (4, 1, 2)])
def test_first_failing_batch_ends_the_review_and_keeps_earlier_decisions(
    tmp_path, monkeypatch, kind, count, size, fail_at
):
    result = run_review(
        tmp_path, monkeypatch, count, failing_on(fail_at, kind), batch_size=size,
        timeout_seconds=1, name=f"{kind}{count}{fail_at}",
    )
    assert len(result["calls"]) == fail_at, "no batch after the failing one is sent"
    kept = sum(len(c) for c in result["ids"][: fail_at - 1])
    assert len(result["suggestions"]) == kept, "earlier decisions kept, failing batch adds none"
    assert result["completeness"] == "partial"
    assert "MODEL_REVIEW_FAILED" in result["reasons"]
    expected = FAILURES[kind][1]
    if expected:
        assert result["status"] == expected and expected in result["reasons"], result["reasons"]
    else:
        assert result["status"] != "OK" and result["status"] in result["reasons"]
    # the failing batch's candidates have no suggestion; earlier ones do
    cited = json.dumps(result["suggestions"])
    for failed in result["ids"][fail_at - 1]:
        assert failed not in cited


def test_first_batch_failing_keeps_nothing_and_sends_one_request(tmp_path, monkeypatch):
    result = run_review(tmp_path, monkeypatch, 12, failing_on(1, "status400"))
    assert len(result["calls"]) == 1 and result["suggestions"] == []
    assert result["completeness"] == "partial"


def test_a_failure_does_not_discard_the_deterministic_catalogue(tmp_path, monkeypatch):
    good = run_review(tmp_path, monkeypatch, 12, name="good")
    bad = run_review(tmp_path, monkeypatch, 12, failing_on(2, "length"), name="bad")
    assert candidates(good["snapshot"]) == candidates(bad["snapshot"])


def test_failure_in_a_later_batch_is_reported_as_that_batchs_cause(tmp_path, monkeypatch):
    def plan(n, c):
        return ("status", 400) if n == 2 else ("sleep", 0) if n == 1 else "length"
    result = run_review(tmp_path, monkeypatch, 15, plan)
    assert result["status"] == "MODEL_REJECTED_REQUEST" and len(result["calls"]) == 2


def test_timeout_is_per_call_not_for_the_whole_review(tmp_path, monkeypatch):
    """Three batches of 0.8 s each against timeout_seconds=1: each call is within the timeout."""
    result = run_review(
        tmp_path, monkeypatch, 15, lambda n, c: ("sleep", 0.8), timeout_seconds=1
    )
    assert result["completeness"] == "complete" and len(result["calls"]) == 3


@pytest.mark.parametrize("quirk", ["foreign", "missing", "duplicate"])
def test_a_batch_answer_about_the_wrong_candidates_is_not_silently_accepted(
    tmp_path, monkeypatch, quirk
):
    """The model answers for an id outside this batch / skips one / repeats one. Anything but a
    clean OK review of all candidates must be visible (partial) and never invent a suggestion for
    a candidate that is not in the batch."""
    result = run_review(tmp_path, monkeypatch, 12, lambda n, c: quirk if n == 2 else "ok")
    text = json.dumps(result["suggestions"])
    assert "not-in-this-batch" not in text
    if quirk == "foreign":
        assert result["completeness"] == "partial", result["status"]
    if quirk == "duplicate":
        counts = {}
        for item in result["suggestions"]:
            counts[json.dumps(item["subject_id"] if "subject_id" in item else item.get("id"))] = 1
        assert len(result["suggestions"]) <= 12


# --- nothing leaks --------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["status400", "garbage", "badcite", "length"])
def test_failure_text_never_carries_the_response_the_key_or_candidate_names(
    tmp_path, monkeypatch, caplog, capsys, kind
):
    caplog.set_level(logging.DEBUG)
    result = run_review(tmp_path, monkeypatch, 12, failing_on(2, kind), name=f"leak{kind}")
    blob = json.dumps(result["run"], default=str) + result["manifest"].model_dump_json()
    blob += (result["snapshot"] / "manifest.json").read_text(encoding="utf-8")
    for path in result["snapshot"].glob("*.jsonl"):
        if path.name != "nodes.jsonl":
            blob += path.read_text(encoding="utf-8") if path.name in {"runs.jsonl"} else ""
    blob += "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("dbig"))
    out = capsys.readouterr()
    blob += out.out + out.err
    assert CANARY not in blob
    assert MODEL_KEY not in blob
    # the invented citation / forced-error body text must not be echoed in status or reasons
    assert CANARY not in " ".join(result["reasons"]) + result["status"]
    # candidate names (tables) are not in the failure record
    assert "zorblax" not in result["status"] + " ".join(result["reasons"])
    assert "zorblax" not in json.dumps(result["run"].get("partial_reasons", []))
