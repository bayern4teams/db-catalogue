"""The reviewer over real HTTP, against a loopback stand-in that enforces vLLM's request contract.

The other reviewer tests inject a fake client, so the wire path an operator's first real run takes
was untested: the `/v1` base URL, the bearer key from its environment reference, and the exact
`response_format` body. The stand-in checks those against what vLLM's own source defines
(`JsonSchemaResponseFormat`: `name`, `description`, `schema`, `strict`, read from vLLM `main` at
3627a6a on 2026-09-30) and answers like a chat completion. It is a contract test, not a model
test: whether a given model follows the schema is measured only against a real vLLM.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig, ReviewConfig
from dbig_contracts.artifact import read_artifact
from pydantic import BaseModel, ConfigDict, Field
from test_builder import source_bundle

MODEL_KEY = "loopback-model-key-for-tests"
BUNDLE_KEY = "synthetic-key-12345678901234567890"


class VllmJsonSchemaFormat(BaseModel):
    """vLLM's `JsonSchemaResponseFormat`, made strict here so a drifted field fails the test.

    No `populate_by_name`, because vLLM's base model does not set it: the schema is accepted only
    under its wire name `schema`. A first version of this stand-in also accepted `json_schema`,
    and mutation testing showed a request using that key passed here although vLLM would treat
    it as an unknown field and generate unconstrained output.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")
    strict: bool | None = None


class Stub:
    def __init__(self, answer) -> None:
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_: object) -> None:
                pass

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.requests.append(
                    {"path": self.path, "auth": self.headers.get("Authorization"), "body": body}
                )
                if self.headers.get("Authorization") != f"Bearer {MODEL_KEY}":
                    self._send(401, {"error": {"message": "unauthorized"}})
                    return
                content = answer(json.loads(body["messages"][-1]["content"]))
                self._send(200, {
                    "id": "cmpl-1", "object": "chat.completion", "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
                })

            def _send(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _supports_everything(candidates: list[dict]) -> str:
    return json.dumps({"decisions": [
        {"candidate_id": item["candidate_id"], "decision": "supports",
         "cited_evidence_ids": [item["evidence"][0]["id"]], "reason": "names and types agree"}
        for item in candidates
    ]})


def _run(tmp_path: Path, monkeypatch, answer, *, key: str = MODEL_KEY) -> tuple[Stub, dict]:
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    monkeypatch.setenv("MODEL_KEY", key)
    stub = Stub(answer)
    try:
        source_bundle(tmp_path / "bundle")
        assemble(
            [tmp_path / "bundle"],
            tmp_path / "snapshot",
            BuilderConfig(
                signing_keys={"test": "BUNDLE_KEY"},
                review=ReviewConfig(
                    base_url=stub.url, model="local-model", model_digest="rev-abc123",
                    api_key_env="MODEL_KEY", timeout_seconds=10,
                ),
            ),
        )
    finally:
        stub.close()
    manifest, data = read_artifact(tmp_path / "snapshot")
    builder_run = next(
        item.model_dump() for item in data["runs.jsonl"] if item.source_id == "builder"
    )
    return stub, {
        "manifest": manifest,
        "status": builder_run["review_status"],
        "reasons": builder_run.get("partial_reasons", []),
        "suggestions": [
            item.model_dump() for item in data["evidence.jsonl"]
            if item.model_dump()["kind"] == "model_suggestion"
        ],
    }


def test_the_request_matches_what_vllm_accepts(tmp_path, monkeypatch):
    stub, _ = _run(tmp_path, monkeypatch, _supports_everything)
    assert len(stub.requests) == 1
    request = stub.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == f"Bearer {MODEL_KEY}"
    body = request["body"]
    assert body["model"] == "local-model"
    assert body["response_format"]["type"] == "json_schema"
    parsed = VllmJsonSchemaFormat.model_validate(body["response_format"]["json_schema"])
    assert parsed.json_schema and parsed.strict is True
    assert body["messages"][0]["role"] == "system"


def test_a_valid_answer_becomes_weak_evidence_and_nothing_more(tmp_path, monkeypatch):
    _, result = _run(tmp_path, monkeypatch, _supports_everything)
    assert result["status"] == "OK"
    assert result["suggestions"], "a supported candidate must be recorded as a suggestion"
    for item in result["suggestions"]:
        assert item["metrics"]["model_digest"] == "rev-abc123"
    assert result["manifest"].completeness == "complete"


def test_nothing_secret_is_sent_to_the_model(tmp_path, monkeypatch):
    stub, _ = _run(tmp_path, monkeypatch, _supports_everything)
    sent = json.dumps(stub.requests[0]["body"])
    assert BUNDLE_KEY not in sent and MODEL_KEY not in sent


def test_a_wrong_key_still_publishes_the_deterministic_catalogue(tmp_path, monkeypatch):
    stub, result = _run(tmp_path, monkeypatch, _supports_everything, key="not-the-key")
    assert stub.requests[0]["auth"] == "Bearer not-the-key"
    assert result["status"] == "MODEL_UNAVAILABLE_OR_INVALID"
    assert result["suggestions"] == []
    assert result["manifest"].completeness == "partial"
    assert result["reasons"] == ["MODEL_REVIEW_FAILED"], "the page must be able to say why"


def test_an_invented_citation_is_refused(tmp_path, monkeypatch):
    def invent(candidates: list[dict]) -> str:
        return json.dumps({"decisions": [
            {"candidate_id": candidates[0]["candidate_id"], "decision": "supports",
             "cited_evidence_ids": ["evidence-that-does-not-exist"], "reason": "trust me"}
        ]})

    _, result = _run(tmp_path, monkeypatch, invent)
    assert result["status"] == "MODEL_UNAVAILABLE_OR_INVALID"
    assert result["suggestions"] == []


def test_output_that_is_not_the_schema_is_refused(tmp_path, monkeypatch):
    _, result = _run(tmp_path, monkeypatch, lambda _: "Sure! These look related.")
    assert result["status"] == "MODEL_UNAVAILABLE_OR_INVALID"


@pytest.mark.parametrize("url", ["http://192.168.1.20:8000/v1", "http://localhost:8000/v1"])
def test_plain_http_is_only_for_a_loopback_literal(url):
    """A vLLM on another machine needs HTTPS; a name is refused because it could resolve
    anywhere. This is the rule the runbook tells operators about."""
    with pytest.raises(ValueError, match="private endpoint"):
        ReviewConfig(base_url=url, model="m", model_digest="d", api_key_env="MODEL_KEY")


def test_ask_over_http_sends_the_question_context_and_accepts_a_cited_answer(
    tmp_path, monkeypatch
):
    """The requester's `ask`, end to end through the real client, against the same stand-in."""
    from dbig_requester.answer import answer_question
    from dbig_requester.config import AnswerConfig
    from dbig_requester.search import index_snapshot

    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_KEY)
    monkeypatch.setenv("MODEL_KEY", MODEL_KEY)
    source_bundle(tmp_path / "bundle")
    assemble([tmp_path / "bundle"], tmp_path / "snapshot",
             BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}))
    index_snapshot(tmp_path / "snapshot", tmp_path / "state")

    def cite_first(body: dict) -> str:
        first = body["items"][0]
        return json.dumps({"answer": f"{first['name']} is catalogued.", "citations": [first["id"]]})

    stub = Stub(cite_first)
    try:
        result = answer_question(
            tmp_path / "state", "which table keeps the customers?",
            AnswerConfig(base_url=stub.url, model="local-model", model_digest="rev-abc123",
                         api_key_env="MODEL_KEY"),
        )
    finally:
        stub.close()
    request = stub.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert request["auth"] == f"Bearer {MODEL_KEY}"
    VllmJsonSchemaFormat.model_validate(request["body"]["response_format"]["json_schema"])
    sent = json.loads(request["body"]["messages"][-1]["content"])
    assert sent["items"][0]["name"] == "customers", "the question must retrieve what it asks about"
    assert result["citations"] == [sent["items"][0]["id"]]
    assert result["model_digest"] == "rev-abc123"
