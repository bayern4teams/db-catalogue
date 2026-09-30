"""Cross-source value evidence: keyed tokens from a collector, containment in the assembler.

This is the capability the product exists for. Two databases that were never linked by a schema
can still hold the same identifiers, and nothing in either catalog says so. Name similarity alone
guesses; comparing the values themselves measures.

It is also the most dangerous capability in the system, so the tests below are as much about what
must *not* happen as about what must. A raw value must never reach an artifact. A token must be
useless outside its run. Two token sets that cannot be compared must produce no number at all,
because a fabricated zero reads as a counterexample rather than as ignorance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dbig_builder.builder import MIN_TOKEN_SUPPORT, assemble
from dbig_builder.config import BuilderConfig
from dbig_collectors.config import SourceConfig
from dbig_collectors.runtime import Budget, discover_mongodb, discover_postgresql
from dbig_contracts.artifact import read_artifact, write_artifact
from dbig_contracts.ids import stable_id
from dbig_contracts.models import Field, Object, Source, ValueTokens
from test_collectors import PgConnection, PgCursor

SYNTHETIC_MATERIAL = "synthetic-value-tokenising-material-over-32-bytes"
OTHER_MATERIAL = "a-different-value-tokenising-material-over-32-bytes"
BUNDLE_MATERIAL = "synthetic-bundle-signing-material-1234567890"


def _config(**overrides) -> SourceConfig:
    base = {
        "source_id": "site-a",
        "kind": "postgresql",
        "connection_env": "DBIG_CONNECTION",
        "signing_key_env": "DBIG_SIGNING_KEY",
        "signing_key_id": "test",
        "namespaces": ["public"],
    }
    return SourceConfig.model_validate({**base, **overrides})


def _tokenising(**overrides) -> SourceConfig:
    defaults = {
        "sample_rows": 10,
        "value_token_key_env": "DBIG_TOKENISER",
        "value_token_key_id": "run-1",  # noqa: S106 - key identifier, not a key
    }
    return _config(**{**defaults, **overrides})


# --- configuration: the feature cannot be half-enabled ----------------------------------


def test_a_token_key_without_an_id_is_refused():
    """An ID is what makes two sides comparable, so a key without one produces tokens nobody
    can match, and an ID without a key claims a comparison that was never computed."""
    with pytest.raises(ValueError, match="value_token_key_env and value_token_key_id"):
        _config(
            value_token_key_env="DBIG_TOKENISER"  # noqa: S106 - variable name
        )
    with pytest.raises(ValueError, match="value_token_key_env and value_token_key_id"):
        _config(value_token_key_id="run-1")  # noqa: S106 - identifier, not a key


# --- collector: tokens are opt-in twice over --------------------------------------------


def test_no_tokens_without_sampling(monkeypatch):
    """A token can only come from a value that was read, and reading values is not the default."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    configured = _config(
        value_token_key_env="DBIG_TOKENISER",  # noqa: S106 - environment variable name
        value_token_key_id="run-1",  # noqa: S106 - a key identifier, not a key
    )
    records = discover_postgresql(PgConnection(), configured, Budget(50))
    assert records["tokens.jsonl"] == []


def test_no_tokens_without_a_key(monkeypatch):
    """Sampling alone must not start producing comparable tokens."""
    records = discover_postgresql(PgConnection(), _config(sample_rows=10), Budget(50))
    assert records["tokens.jsonl"] == []


def test_tokens_are_emitted_when_both_are_configured(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    records = discover_postgresql(PgConnection(), _tokenising(), Budget(50))
    entry = records["tokens.jsonl"][0]
    assert entry.record_type == "value_tokens"
    assert entry.key_id == "run-1"
    assert entry.algorithm == "hmac-sha256"
    assert entry.field_id == records["fields.jsonl"][0].id
    assert entry.observed_count == 3
    assert entry.distinct_count == 3
    assert entry.truncated is False
    assert entry.sample_method == "first_rows_limit:10"
    assert entry.tokens == sorted(entry.tokens), "sorted so the file's digest is stable"


def test_a_short_token_key_is_refused(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", "too-short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        discover_postgresql(PgConnection(), _tokenising(), Budget(50))


def test_the_sample_is_bounded_by_the_configured_row_limit(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    connection = PgConnection()
    discover_postgresql(connection, _tokenising(sample_rows=5), Budget(50))
    sample_calls = [
        (query, params) for query, params in connection.fake_cursor.calls
        if query.startswith('SELECT "')
    ]
    assert sample_calls, "a tokenising scan must issue a bounded value read"
    for query, params in sample_calls:
        assert "LIMIT" in query
        assert params == (5,), "the row limit must be bound, not interpolated"
        assert "*" not in query, "columns are named explicitly rather than selected with *"


def test_tokenising_costs_one_charged_query_per_table(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    plain = Budget(50)
    discover_postgresql(PgConnection(), _config(), plain)
    tokenised = Budget(50)
    discover_postgresql(PgConnection(), _tokenising(), tokenised)
    assert tokenised.used == plain.used + 1, "one bounded read for the single fixture table"


# --- the security properties ------------------------------------------------------------


def test_no_raw_value_reaches_the_bundle(tmp_path, monkeypatch):
    """The whole point of tokenising. The sampled values are 1, 2 and 3; none may appear."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)

    class Revealing(PgCursor):
        sample = [("canary-value-one",), ("canary-value-two",)]

    connection = PgConnection()
    connection.fake_cursor = Revealing()
    data = discover_postgresql(connection, _tokenising(), Budget(50))
    data["sources.jsonl"] = [_source()]
    write_artifact(
        tmp_path / "bundle", "collector-bundle", data, producer="test", source_run_ids=["run-a"]
    )
    for path in sorted((tmp_path / "bundle").iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        assert "canary-value" not in content, f"a raw value leaked into {path.name}"
        assert SYNTHETIC_MATERIAL not in content, f"the token key leaked into {path.name}"


def test_the_same_value_under_a_different_key_gives_a_different_token(monkeypatch):
    """What makes a token useless outside its run. An unkeyed digest of a small integer is
    recovered by trying every candidate; a keyed one is not."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    first = discover_postgresql(PgConnection(), _tokenising(), Budget(50))["tokens.jsonl"][0]
    monkeypatch.setenv("DBIG_TOKENISER", OTHER_MATERIAL)
    second = discover_postgresql(PgConnection(), _tokenising(), Budget(50))["tokens.jsonl"][0]
    assert set(first.tokens).isdisjoint(second.tokens)


def test_a_token_is_the_keyed_digest_of_the_normalised_text(monkeypatch):
    """Pins the construction, so a change to it is a deliberate contract change: two sources can
    only agree on a token if they agree on exactly this."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    entry = discover_postgresql(PgConnection(), _tokenising(), Budget(50))["tokens.jsonl"][0]
    expected = {
        hmac.new(SYNTHETIC_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()
        for value in (1, 2, 3)
    }
    assert set(entry.tokens) == expected


def test_an_integer_and_its_text_form_agree(monkeypatch):
    """The cross-source case that matters most: a MongoDB string identifier pointing at a
    relational integer key must produce the same token on both sides."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)

    class AsText(PgCursor):
        sample = [("1",), ("2",), ("3",)]

    connection = PgConnection()
    connection.fake_cursor = AsText()
    as_text = discover_postgresql(connection, _tokenising(), Budget(50))["tokens.jsonl"][0]
    as_int = discover_postgresql(PgConnection(), _tokenising(), Budget(50))["tokens.jsonl"][0]
    assert set(as_text.tokens) == set(as_int.tokens)


def test_values_with_no_agreed_normalisation_are_skipped(monkeypatch):
    """Booleans and bytes are counted as unusable rather than coerced, because no normalisation
    for them would be agreed by two different engines."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)

    class Mixed(PgCursor):
        sample = [(True,), (b"bytes",), (7,), (None,)]

    connection = PgConnection()
    connection.fake_cursor = Mixed()
    entry = discover_postgresql(connection, _tokenising(), Budget(50))["tokens.jsonl"][0]
    assert entry.observed_count == 1, "only the integer is usable"
    assert entry.distinct_count == 1


def test_a_capped_token_set_says_it_is_capped(monkeypatch):
    """A cap makes containment a lower bound, and the denominators must still describe the
    sample rather than the cap."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)

    class Many(PgCursor):
        sample = [(value,) for value in range(20)]

    connection = PgConnection()
    connection.fake_cursor = Many()
    entry = discover_postgresql(
        connection, _tokenising(sample_rows=20, max_value_tokens=5), Budget(50)
    )["tokens.jsonl"][0]
    assert len(entry.tokens) == 5
    assert entry.distinct_count == 20, "the denominator describes the sample, not the cap"
    assert entry.observed_count == 20
    assert entry.truncated is True


# --- assembler: containment, and abstention -------------------------------------------


def _source(source_id: str = "site-a") -> Source:
    return Source(
        id=source_id,
        kind="postgresql",
        name=source_id,
        source_id=source_id,
        observed_at=datetime.now(UTC),
        collector_version="test",
        provenance="synthetic",
    )


def _common(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "observed_at": datetime.now(UTC),
        "collector_version": "test",
        "provenance": "synthetic",
    }


def _tokens(field_id: str, object_id: str, values, *, key_id="run-1", cap=None, source="site-a"):
    digests = sorted(
        {
            hmac.new(SYNTHETIC_MATERIAL.encode(), str(value).encode(), hashlib.sha256).hexdigest()
            for value in values
        }
    )
    kept = digests[:cap] if cap else digests
    return ValueTokens(
        id=stable_id(source, "tokens", field_id),
        field_id=field_id,
        object_id=object_id,
        key_id=key_id,
        sample_count=len(list(values)),
        sample_method="first_rows_limit:10",
        observed_count=len(list(values)),
        distinct_count=len(digests),
        truncated=len(kept) < len(digests),
        tokens=kept,
        **_common(source),
    )


def _bundle(path: Path, *, child_values, parent_values, child_key="run-1", parent_key="run-1",
            child_cap=None):
    """One bundle holding a child field, a candidate parent key, and token sets for both."""
    common = _common("site-a")
    orders = Object(
        id=stable_id("site-a", "object", "1"), native_id="1", kind="table", name="orders", **common
    )
    customers = Object(
        id=stable_id("site-a", "object", "2"), native_id="2", kind="table", name="customers",
        **common,
    )
    customer_id = Field(
        id=stable_id("site-a", "field", "1:1"), object_id=orders.id, native_id="1:1",
        name="customer_id", data_type="integer", **common,
    )
    identifier = Field(
        id=stable_id("site-a", "field", "2:1"), object_id=customers.id, native_id="2:1",
        name="id", data_type="integer", **common,
    )
    tables = {
        "sources.jsonl": [_source()],
        "objects.jsonl": [orders, customers],
        "fields.jsonl": [customer_id, identifier],
        "constraints.jsonl": [
            {
                "record_type": "constraint",
                "id": stable_id("site-a", "constraint", "pk"),
                "native_id": "pk",
                "kind": "primary",
                "object_id": customers.id,
                "field_names": ["id"],
                **common,
            }
        ],
        "tokens.jsonl": [
            _tokens(customer_id.id, orders.id, child_values, key_id=child_key, cap=child_cap),
            _tokens(identifier.id, customers.id, parent_values, key_id=parent_key),
        ],
    }
    write_artifact(
        path, "collector-bundle", tables, producer="test", source_run_ids=["run-a"],
        signing_key=BUNDLE_MATERIAL.encode(), key_id="test",
    )
    return {"child": customer_id, "parent": identifier}


def _assemble(tmp_path: Path, monkeypatch, **bundle_kwargs) -> dict:
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_MATERIAL)
    fields = _bundle(tmp_path / "bundle", **bundle_kwargs)
    assemble(
        [tmp_path / "bundle"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    _, data = read_artifact(tmp_path / "snapshot")
    return {
        "fields": fields,
        "evidence": {item.id: item for item in data["evidence.jsonl"]},
        "edges": list(data["edges.jsonl"]),
        "snapshot": tmp_path / "snapshot",
    }


def _containment_evidence(result: dict):
    return [
        item for item in result["evidence"].values()
        if item.model_dump()["kind"] == "value_containment"
    ]


def test_containment_is_measured_and_cited(tmp_path, monkeypatch):
    """Every child value appears among the parent's, which is what a real reference looks like."""
    result = _assemble(
        tmp_path, monkeypatch, child_values=[1, 2, 3, 4], parent_values=[1, 2, 3, 4, 5, 6]
    )
    found = _containment_evidence(result)
    assert len(found) == 1
    metrics = found[0].model_dump()["metrics"]
    assert metrics["contained_fraction"] == 1.0
    assert metrics["shared_tokens"] == 4
    assert metrics["child_distinct_sampled"] == 4
    assert metrics["parent_distinct_sampled"] == 6
    assert metrics["jaccard"] == round(4 / 6, 6)
    assert metrics["lower_bound_only"] is False
    candidate = next(
        edge for edge in result["edges"] if edge.model_dump()["kind"] == "REFERENCE_CANDIDATE"
    )
    assert found[0].id in candidate.model_dump()["evidence_ids"], "the edge must cite it"


def test_partial_containment_is_reported_as_it_is(tmp_path, monkeypatch):
    result = _assemble(
        tmp_path, monkeypatch, child_values=[1, 2, 3, 99], parent_values=[1, 2, 3, 4, 5]
    )
    metrics = _containment_evidence(result)[0].model_dump()["metrics"]
    assert metrics["contained_fraction"] == 0.75
    assert metrics["shared_tokens"] == 3


def test_containment_does_not_promote_the_edge(tmp_path, monkeypatch):
    """Value overlap is evidence, not a verdict. Promotion needs calibration that does not exist
    yet, so the tier stays where the deterministic rules put it and confidence stays null."""
    result = _assemble(
        tmp_path, monkeypatch, child_values=[1, 2, 3, 4], parent_values=[1, 2, 3, 4]
    )
    candidate = next(
        edge for edge in result["edges"] if edge.model_dump()["kind"] == "REFERENCE_CANDIDATE"
    )
    dumped = candidate.model_dump()
    assert dumped["status"] == "tentative"
    assert dumped["confidence"] is None


def test_tokens_never_reach_the_snapshot(tmp_path, monkeypatch):
    """A bundle is a controlled transfer; the published graph is not. No token may cross."""
    result = _assemble(
        tmp_path, monkeypatch, child_values=[1, 2, 3, 4], parent_values=[1, 2, 3, 4]
    )
    tokens = json.loads(
        (tmp_path / "bundle" / "tokens.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )["tokens"]
    assert tokens, "the fixture must actually carry tokens"
    for path in sorted(result["snapshot"].iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        for token in tokens:
            assert token not in content, f"a token leaked into {path.name}"


def test_different_key_ids_abstain_rather_than_report_zero(tmp_path, monkeypatch):
    """Tokens keyed differently are unrelated byte strings. Reporting their zero overlap would
    read as a counterexample, which is the opposite of the truth."""
    result = _assemble(
        tmp_path, monkeypatch, child_values=[1, 2, 3, 4], parent_values=[1, 2, 3, 4],
        child_key="run-1", parent_key="run-2",
    )
    assert _containment_evidence(result) == []


def test_thin_support_abstains(tmp_path, monkeypatch):
    """Below the minimum distinct count there is nothing worth calling a measurement."""
    values = list(range(MIN_TOKEN_SUPPORT - 1))
    result = _assemble(tmp_path, monkeypatch, child_values=values, parent_values=values)
    assert _containment_evidence(result) == []


def test_support_at_the_threshold_is_measured(tmp_path, monkeypatch):
    values = list(range(MIN_TOKEN_SUPPORT))
    result = _assemble(tmp_path, monkeypatch, child_values=values, parent_values=values)
    assert len(_containment_evidence(result)) == 1


def test_a_capped_child_marks_the_result_a_lower_bound(tmp_path, monkeypatch):
    result = _assemble(
        tmp_path, monkeypatch, child_values=list(range(10)), parent_values=list(range(10)),
        child_cap=5,
    )
    metrics = _containment_evidence(result)[0].model_dump()["metrics"]
    assert metrics["lower_bound_only"] is True
    assert metrics["child_distinct_sampled"] == 5, "computed from the tokens actually present"


def test_a_snapshot_without_tokens_still_assembles(tmp_path, monkeypatch):
    """Tokens are optional. A metadata-only bundle must assemble exactly as before, with the
    candidate edge citing only its name, type and key evidence."""
    monkeypatch.setenv("BUNDLE_KEY", BUNDLE_MATERIAL)
    fields = _bundle(tmp_path / "bundle", child_values=[1, 2, 3, 4], parent_values=[1, 2, 3, 4])
    assert fields
    stripped = tmp_path / "stripped"
    stripped.mkdir()
    for path in sorted((tmp_path / "bundle").iterdir()):
        if path.name == "tokens.jsonl":
            (stripped / path.name).write_bytes(b"")
        else:
            (stripped / path.name).write_bytes(path.read_bytes())
    # The manifest no longer matches, so re-publish from the parsed rows instead of editing it.
    _, rows = read_artifact(tmp_path / "bundle", signing_key=BUNDLE_MATERIAL.encode())
    rows.pop("tokens.jsonl")
    write_artifact(
        tmp_path / "plain", "collector-bundle", rows, producer="test",
        source_run_ids=["run-a"], signing_key=BUNDLE_MATERIAL.encode(), key_id="test",
    )
    assemble(
        [tmp_path / "plain"],
        tmp_path / "snapshot",
        BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
    )
    _, data = read_artifact(tmp_path / "snapshot")
    kinds = {item.model_dump()["kind"] for item in data["evidence.jsonl"]}
    assert "value_containment" not in kinds
    assert any(
        edge.model_dump()["kind"] == "REFERENCE_CANDIDATE" for edge in data["edges.jsonl"]
    )


# --- MongoDB tokens, and the cross-engine case ------------------------------------------
#
# PostgreSQL tokens alone cannot reach the case the design is really about: a MongoDB identifier
# held as a string pointing at a relational integer key, with no schema anywhere saying so. That
# needs both engines producing comparable tokens, which means the same keying and the same
# normalisation on both sides.


def _mongo_config(**overrides) -> SourceConfig:
    defaults = {
        "source_id": "site-b",
        "kind": "mongodb",
        "connection_env": "DBIG_CONNECTION",
        "signing_key_env": "DBIG_SIGNING_KEY",
        "signing_key_id": "test",
        "namespaces": ["shop"],
        "sample_rows": 10,
        "value_token_key_env": "DBIG_TOKENISER",  # noqa: S106 - environment variable name
        "value_token_key_id": "run-1",
    }
    return SourceConfig.model_validate({**defaults, **overrides})


def _mongo_client(documents):
    class Cursor:
        def limit(self, count):
            return self

        def max_time_ms(self, timeout):
            return self

        def __iter__(self):
            return iter(documents)

        def close(self):
            pass

    class Db:
        def list_collections(self):
            return [{"name": "orders", "info": {"uuid": "uuid-77"}}]

        def __getitem__(self, name):
            return self

        def list_indexes(self):
            return []

        def find(self, filter):
            return Cursor()

        def estimated_document_count(self, **kwargs):
            return len(documents)

    class Client:
        def __getitem__(self, database):
            return Db()

    return Client()


def test_mongo_emits_no_tokens_without_a_key(monkeypatch):
    records = discover_mongodb(
        _mongo_client([{"_id": 1, "customer_id": "7"}]),
        _mongo_config(value_token_key_env=None, value_token_key_id=None),
        Budget(50),
    )
    assert records["tokens.jsonl"] == []


def test_mongo_emits_no_tokens_without_a_sample(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    records = discover_mongodb(
        _mongo_client([{"_id": 1, "customer_id": "7"}]),
        _mongo_config(sample_rows=0),
        Budget(50),
    )
    assert records["tokens.jsonl"] == []


def test_mongo_tokens_are_keyed_and_bounded(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    documents = [{"_id": index, "customer_id": str(index)} for index in range(1, 6)]
    records = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    by_field = {entry.field_id: entry for entry in records["tokens.jsonl"]}
    assert by_field, "a sampled MongoDB collection with a key must produce tokens"
    entry = next(iter(by_field.values()))
    assert entry.key_id == "run-1"
    assert entry.sample_method == "first_documents_limit:10"
    assert entry.observed_count == 5
    assert entry.distinct_count == 5


def test_a_mongo_string_identifier_and_a_relational_integer_key_agree(monkeypatch):
    """The cross-engine case, end to end at the token level. MongoDB holds "1", "2", "3" as
    strings; PostgreSQL holds 1, 2, 3 as integers. Nothing in either catalog links them, and
    their token sets must still match exactly, or containment could never find the reference."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    relational = discover_postgresql(PgConnection(), _tokenising(), Budget(50))
    documents = [{"customer_id": str(value)} for value in (1, 2, 3)]
    document_side = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    relational_tokens = set(relational["tokens.jsonl"][0].tokens)
    mongo_tokens = set(document_side["tokens.jsonl"][0].tokens)
    assert relational_tokens == mongo_tokens


def test_a_mongo_token_set_under_another_key_does_not_match(monkeypatch):
    """The same guarantee as on the relational side: outside its run a token is meaningless."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    documents = [{"customer_id": str(value)} for value in (1, 2, 3)]
    first = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    monkeypatch.setenv("DBIG_TOKENISER", OTHER_MATERIAL)
    second = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    assert set(first["tokens.jsonl"][0].tokens).isdisjoint(second["tokens.jsonl"][0].tokens)


def test_no_raw_mongo_value_reaches_the_bundle(tmp_path, monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    documents = [{"customer_id": "canary-document-value"}, {"customer_id": "another-canary"}]
    data = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    data["sources.jsonl"] = [_source("site-b")]
    write_artifact(
        tmp_path / "bundle", "collector-bundle", data, producer="test", source_run_ids=["run-b"]
    )
    for path in sorted((tmp_path / "bundle").iterdir()):
        content = path.read_text(encoding="utf-8", errors="replace")
        assert "canary" not in content, f"a raw document value leaked into {path.name}"


def test_mongo_array_elements_are_tokenised_at_their_path(monkeypatch):
    """Array members are their own path in the catalogue, so their values belong to that path."""
    monkeypatch.setenv("DBIG_TOKENISER", SYNTHETIC_MATERIAL)
    documents = [{"tags": ["a", "b"]}, {"tags": ["c"]}]
    records = discover_mongodb(_mongo_client(documents), _mongo_config(), Budget(50))
    fields = {field.name: field.id for field in records["fields.jsonl"]}
    by_field = {entry.field_id: entry for entry in records["tokens.jsonl"]}
    assert "tags[]" in fields
    entry = by_field[fields["tags[]"]]
    assert entry.observed_count == 3, "three array members across two documents"
    assert entry.distinct_count == 3


def test_a_short_mongo_token_key_is_refused(monkeypatch):
    monkeypatch.setenv("DBIG_TOKENISER", "too-short")
    with pytest.raises(ValueError, match="at least 32 bytes"):
        discover_mongodb(
            _mongo_client([{"customer_id": "1"}]), _mongo_config(), Budget(50)
        )
