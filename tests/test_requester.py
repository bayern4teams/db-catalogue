import json
import sys
import types
from types import SimpleNamespace

import pytest
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig
from dbig_requester.answer import answer_question
from dbig_requester.api import create_app
from dbig_requester.config import AnswerConfig, RequesterConfig
from dbig_requester.search import SearchRequest, index_snapshot, search
from fastapi.testclient import TestClient
from test_builder import source_bundle


def indexed(tmp_path, monkeypatch):
    source_bundle(tmp_path / "bundle")
    monkeypatch.setenv("BUNDLE_KEY", "synthetic-key-12345678901234567890")
    assemble(
        [tmp_path / "bundle"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    index_snapshot(tmp_path / "snapshot", tmp_path / "state")
    return tmp_path / "state"


def test_search_filters_and_stale_digest(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    found = search(state, SearchRequest(query="customer", kind="field"))
    assert {item["name"] for item in found["results"]} == {"customer_id"}
    assert all(item["source_id"] == "site-a" for item in found["results"])
    assert search(state, SearchRequest(query="customers", tag="billing"))["results"]
    assert search(state, SearchRequest(query="customers", capability="sql_read"))["results"]
    assert search(state, SearchRequest(query="customers", tag="missing"))["results"] == []
    assert search(state, SearchRequest(query="customer", max_age_seconds=0))["results"] == []
    manifest = tmp_path / "snapshot" / "manifest.json"
    changed = json.loads(manifest.read_text(encoding="utf-8"))
    changed["completeness"] = "partial"
    manifest.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(ValueError, match="STALE_INDEX"):
        search(state, SearchRequest())


class FakeClient:
    def __init__(self, content):
        self.content = content
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


def test_answers_validate_citations_and_outage(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    config = AnswerConfig(
        base_url="http://127.0.0.1:8000/v1",
        model="private",
        model_digest="sha",
        api_key_env="VLLM_KEY",
    )
    rows = search(state, SearchRequest(query="orders", limit=10))["results"]
    good = FakeClient(
        json.dumps({"answer": "Orders are in the catalog.", "citations": [rows[0]["id"]]})
    )
    assert answer_question(state, "orders", config, client=good)["citations"] == [rows[0]["id"]]
    bad = FakeClient(json.dumps({"answer": "Invented", "citations": ["unknown"]}))
    assert answer_question(state, "orders", config, client=bad)["error"]["code"] == (
        "MODEL_UNAVAILABLE_OR_INVALID"
    )
    assert answer_question(state, "show lineage", config)["error"]["code"] == (
        "LINEAGE_REQUIRES_PLANNER"
    )


def test_authenticated_requester_api_and_payload_limit(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
    config = RequesterConfig(
        snapshot_dir=tmp_path / "snapshot", state_dir=state, api_key_env="REQUESTER_TOKEN"
    )
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/catalogue/search").status_code == 401
        response = client.get(
            "/v1/catalogue/search?query=orders",
            headers={"Authorization": "Bearer synthetic-api-token"},
        )
        assert response.status_code == 200 and response.json()["results"]
        too_large = client.post(
            "/v1/catalogue/questions",
            json={"question": "x" * 70_000},
            headers={"Authorization": "Bearer synthetic-api-token"},
        )
        assert too_large.status_code == 413


def test_requester_api_requires_secret_at_startup_and_uses_configured_body_limit(
    tmp_path, monkeypatch
):
    config = RequesterConfig(
        snapshot_dir=tmp_path / "snapshot",
        state_dir=tmp_path / "state",
        api_key_env="REQUESTER_TOKEN",
        max_request_bytes=16,
    )
    monkeypatch.delenv("REQUESTER_TOKEN", raising=False)
    with pytest.raises(ValueError, match="API key environment reference required"):
        create_app(config)
    monkeypatch.setenv("REQUESTER_TOKEN", " ")
    with pytest.raises(ValueError, match="API key environment reference required"):
        create_app(config)
    monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/catalogue/questions",
            json={"question": "more than sixteen bytes"},
            headers={"Authorization": "Bearer synthetic-api-token"},
        )
        assert response.status_code == 413


def test_answer_telemetry_outage_does_not_hide_answer(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    rows = search(state, SearchRequest(query="orders"))["results"]
    monkeypatch.setenv("LF_PUBLIC", "synthetic-public")
    monkeypatch.setenv("LF_SECRET", "canary-secret")
    calls = []

    class Span:
        def end(self):
            raise RuntimeError("telemetry offline")

    class FakeLangfuse:
        def __init__(self, **kwargs):
            pass

        def start_observation(self, **kwargs):
            calls.append(kwargs)
            return Span()

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=FakeLangfuse))
    config = AnswerConfig(
        base_url="http://127.0.0.1:8000/v1",
        model="private",
        model_digest="sha",
        api_key_env="VLLM_KEY",
        langfuse_url="http://127.0.0.1:3000",
        langfuse_public_key_env="LF_PUBLIC",
        langfuse_secret_key_env="LF_SECRET",  # noqa: S106 - environment variable name
    )
    client = FakeClient(json.dumps({"answer": "Catalogued.", "citations": [rows[0]["id"]]}))
    assert answer_question(state, "orders", config, client=client)["answer"] == "Catalogued."
    assert "input" not in calls[0] and "output" not in calls[0]
    assert "canary-secret" not in json.dumps(calls[0])
