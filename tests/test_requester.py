import json
import sqlite3
import sys
import types
from types import SimpleNamespace

import httpx
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
    # `customer_id` by its own name, ranked first; `customers.id` through its table's name.
    assert [item["name"] for item in found["results"]][0] == "customer_id"
    assert {item["name"] for item in found["results"]} == {"customer_id", "id"}
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
            choices=[
                SimpleNamespace(
                    finish_reason="stop", message=SimpleNamespace(content=self.content)
                )
            ]
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
        "MODEL_INVALID_CITATION"
    )
    lineage = FakeClient(
        json.dumps({"answer": "It flows into x.", "citations": [rows[0]["id"]]})
    )
    assert answer_question(state, "orders", config, client=lineage)["error"]["code"] == (
        "MODEL_UNAVAILABLE_OR_INVALID"
    )
    # DEC-6: the word is not a refusal; only a claim in the answer is.
    assert "error" not in answer_question(state, "show orders lineage", config, client=good)


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
    with pytest.raises(ValueError, match="REQUESTER_TOKEN, which is not set"):
        create_app(config)
    monkeypatch.setenv("REQUESTER_TOKEN", " ")
    with pytest.raises(ValueError, match="REQUESTER_TOKEN for requester API bearer token is empty"):
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


# --- retrieval found wanting on a real catalogue ---------------------------------------------


def test_a_term_matches_as_a_prefix(tmp_path, monkeypatch):
    """`customer` used to miss the `customers` table: FTS matched whole tokens only."""
    state = indexed(tmp_path, monkeypatch)
    names = {item["name"] for item in search(state, SearchRequest(query="customer"))["results"]}
    assert "customers" in names


def test_explicit_search_still_needs_every_term(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    assert search(state, SearchRequest(query="customer zebra"))["results"] == []


def test_a_question_retrieves_by_its_meaningful_words(tmp_path, monkeypatch):
    """A natural question matched nothing when every word was required."""
    state = indexed(tmp_path, monkeypatch)
    question = "which table stores the customer records?"
    assert search(state, SearchRequest(query=question))["results"] == []
    found = search(state, SearchRequest(query=question, match="any"))["results"]
    assert found and found[0]["name"] in {"customers", "customer_id"}


def test_a_question_of_only_common_words_retrieves_nothing(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    assert search(state, SearchRequest(query="what is this?", match="any"))["results"] == []


def test_fts_operators_in_a_query_are_data(tmp_path, monkeypatch):
    state = indexed(tmp_path, monkeypatch)
    for query in ('customer" OR "x', "NEAR(customer orders)", "customer*", "-customer"):
        search(state, SearchRequest(query=query))
        search(state, SearchRequest(query=query, match="any"))


def test_ask_never_falls_back_to_an_arbitrary_slice(tmp_path, monkeypatch):
    """No match used to send the model the first entries alphabetically."""
    state = indexed(tmp_path, monkeypatch)
    config = AnswerConfig(
        base_url="http://127.0.0.1:9/v1", model="m", model_digest="d", api_key_env="MODEL_KEY"
    )

    class Unreachable:
        class chat:  # noqa: N801 - mirrors the client's attribute path
            class completions:  # noqa: N801
                @staticmethod
                def create(**_: object) -> None:
                    raise AssertionError("the model must not be called without a match")

    result = answer_question(state, "zebra giraffe", config, client=Unreachable())
    assert result["error"]["code"] == "NO_CATALOGUE_MATCH"


def _custom_index(tmp_path):
    """A snapshot built to separate the retrieval rules: a column whose name starts with a
    stopword, a column findable only by its comment, and a column that sorts first
    alphabetically but matches only through its table's name."""
    from datetime import UTC, datetime

    from dbig_contracts.artifact import write_artifact
    from dbig_contracts.models import Field, Object, Source

    common = {"source_id": "s", "observed_at": datetime.now(UTC),
              "collector_version": "t", "provenance": "t"}
    nodes = [
        Source(id="s", kind="postgresql", name="s", **common),
        Object(id="o-customers", native_id="1", kind="table", name="customers", **common),
        Object(id="o-ledger", native_id="2", kind="table", name="ledger", **common),
        Field(id="f-aaa", object_id="o-customers", native_id="1:1", name="aaa",
              data_type="text", **common),
        Field(id="f-is", object_id="o-ledger", native_id="2:1", name="is_active",
              data_type="boolean", **common),
        Field(id="f-ref", object_id="o-ledger", native_id="2:2", name="customer_ref",
              data_type="integer", **common),
        Field(id="f-mail", object_id="o-ledger", native_id="2:3", name="addr",
              data_type="text", description="Contact address for invoices", **common),
    ]
    write_artifact(tmp_path / "snapshot", "catalog-snapshot",
                   {"nodes.jsonl": nodes, "edges.jsonl": [], "evidence.jsonl": []},
                   producer="t", source_run_ids=[])
    index_snapshot(tmp_path / "snapshot", tmp_path / "state")
    return tmp_path / "state"


def test_an_entry_matching_by_its_own_name_ranks_first(tmp_path):
    """`aaa` sorts first but matches only through its table; `customer_ref` matches itself."""
    state = _custom_index(tmp_path)
    names = [row["name"] for row in
             search(state, SearchRequest(query="customer", kind="field"))["results"]]
    assert names == ["customer_ref", "aaa"]


def test_a_comment_makes_a_column_findable(tmp_path):
    state = _custom_index(tmp_path)
    names = [row["name"] for row in search(state, SearchRequest(query="contact"))["results"]]
    assert names == ["addr"]


def test_stopwords_do_not_match_as_prefixes(tmp_path):
    """Without the stopword filter, "is" would prefix-match `is_active` in any question."""
    state = _custom_index(tmp_path)
    assert search(state, SearchRequest(query="is it", match="any"))["results"] == []


def test_ask_retrieves_for_a_natural_question(tmp_path):
    state = _custom_index(tmp_path)
    config = AnswerConfig(
        base_url="http://127.0.0.1:9/v1", model="m", model_digest="d", api_key_env="MODEL_KEY"
    )
    seen: list[dict] = []

    class Recording:
        class chat:  # noqa: N801 - mirrors the client's attribute path
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs: object) -> SimpleNamespace:
                    items = json.loads(kwargs["messages"][-1]["content"])["items"]  # type: ignore[index]
                    seen.extend(items)
                    body = json.dumps({"answer": "ledger.addr", "citations": [items[0]["id"]]})
                    return SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                finish_reason="stop", message=SimpleNamespace(content=body)
                            )
                        ]
                    )

    result = answer_question(state, "where do we keep the contact address?", config,
                             client=Recording())
    assert result.get("answer") == "ledger.addr", result
    assert seen[0]["name"] == "addr"
    assert seen[0]["description"] == "Contact address for invoices", "comments reach the model"
    assert seen[0]["object_name"] == "ledger"


def _answer_config() -> AnswerConfig:
    return AnswerConfig(
        base_url="http://127.0.0.1:8000/v1", model="private", model_digest="sha",
        api_key_env="VLLM_KEY",
    )


@pytest.mark.parametrize("how", ["version", "digest"])
def test_ask_on_a_stale_index_returns_the_code_and_the_http_routes_agree(
    tmp_path, monkeypatch, how
):
    """A stale index (another index version, or a snapshot changed since indexing) is the same
    typed error everywhere: `search` raises it, `ask` returns it as a code, and the HTTP search
    route answers 409 with it, instead of a traceback or a 500."""
    state = indexed(tmp_path, monkeypatch)
    if how == "version":
        database = sqlite3.connect(state / "catalog.sqlite3")
        database.execute("DELETE FROM meta WHERE key='index_version'")
        database.commit()
        database.close()
    else:
        manifest = tmp_path / "snapshot" / "manifest.json"
        changed = json.loads(manifest.read_text(encoding="utf-8"))
        changed["completeness"] = "partial"
        manifest.write_text(json.dumps(changed), encoding="utf-8")
    assert answer_question(state, "orders", _answer_config()) == {"error": {"code": "STALE_INDEX"}}
    monkeypatch.setenv("REQUESTER_TOKEN", "synthetic-api-token")
    config = RequesterConfig(
        snapshot_dir=tmp_path / "snapshot", state_dir=state, api_key_env="REQUESTER_TOKEN"
    )
    with TestClient(create_app(config)) as client:
        response = client.get(
            "/v1/catalogue/search?query=orders",
            headers={"Authorization": "Bearer synthetic-api-token"},
        )
    assert response.status_code == 409 and response.json()["detail"] == {"code": "STALE_INDEX"}


@pytest.mark.parametrize(
    ("cause", "code"),
    [(httpx.ConnectTimeout("x"), "MODEL_UNREACHABLE"), (httpx.ReadTimeout("x"), "MODEL_TIMEOUT")],
)
def test_a_connect_timeout_is_unreachable_and_a_read_timeout_is_a_timeout(
    tmp_path, monkeypatch, cause, code
):
    """A host that never completes the connection is down or firewalled; only a slow reply is a
    timeout the operator can fix with `timeout_seconds`."""
    from openai import APITimeoutError

    state = indexed(tmp_path, monkeypatch)

    class Slow:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs: object) -> None:
                    raise APITimeoutError(request=httpx.Request("POST", "http://x")) from cause

    result = answer_question(state, "orders", _answer_config(), client=Slow())
    assert result["error"]["code"] == code
