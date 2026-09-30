"""The readable catalogue page: complete, safe to open, and never about a different database state.

Every name on the page comes from a database, so the page is tested as if an attacker chose them.
It must also refuse to mix a snapshot with a bundle it was not built from, because statistics
from one collection beside relationships from another would describe no real state at all.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner
from dbig_builder.builder import assemble
from dbig_builder.cli import main as builder_main
from dbig_builder.config import BuilderConfig
from dbig_builder.render import SCRIPT, render_catalog
from dbig_contracts.artifact import write_artifact
from dbig_contracts.errors import OperatorError
from dbig_contracts.models import Diagnostic, Field, Object, Profile, Source, ValueTokens

KEY = "synthetic-bundle-signing-material-1234567890"
HOSTILE = "<script>alert(1)</script>"
# Breaks out of a single-quoted attribute if quotes are not escaped.
HOSTILE_ATTRIBUTE = "x' onmouseover='alert(2)"
TOKEN = hmac.new(b"token-material", b"42", hashlib.sha256).hexdigest()


def _common(when: datetime) -> dict:
    return {
        "source_id": "shop",
        "observed_at": when,
        "collector_version": "test",
        "provenance": "synthetic",
    }


def _bundle(path: Path, *, completeness: str = "complete", when: datetime | None = None) -> Path:
    common = _common(when or datetime(2026, 9, 1, tzinfo=UTC))
    customers = Object(
        id="o-customers", native_id="1", kind="table", name="customers", namespace="public",
        attributes={"row_count_estimate": 12000.0, "row_count_method": "pg_class.reltuples"},
        **common,
    )
    orders = Object(
        id="o-orders", native_id="2", kind="table", name="orders", namespace="public", **common
    )
    # No declared key points from here, so its `customer_id` is an inferred candidate.
    returns = Object(
        id="o-returns", native_id="4", kind="table", name="returns", namespace="public", **common
    )
    hostile = Object(
        id="o-hostile", native_id="3", kind="table", name=HOSTILE, namespace="public",
        attributes={"note": HOSTILE_ATTRIBUTE}, **common,
    )
    fields = [
        Field(id="f-cid", object_id=customers.id, native_id="1:1", name="id",
              data_type="integer", nullable=False, ordinal=1, **common),
        Field(id="f-email", object_id=customers.id, native_id="1:2", name="email",
              data_type="text", nullable=True, ordinal=2, **common),
        Field(id="f-oid", object_id=orders.id, native_id="2:1", name="id",
              data_type="integer", nullable=False, ordinal=1, **common),
        Field(id="f-ocust", object_id=orders.id, native_id="2:2", name="customer_id",
              data_type="integer", ordinal=2, **common),
        Field(id="f-rcust", object_id=returns.id, native_id="4:1", name="customer_id",
              data_type="integer", ordinal=1, **common),
        Field(id="f-hostile", object_id=hostile.id, native_id="3:1", name=HOSTILE_ATTRIBUTE,
              data_type="integer", ordinal=1, **common),
    ]
    constraints = [
        {"record_type": "constraint", "id": "c-pk", "native_id": "customers_pkey",
         "kind": "primary", "object_id": customers.id, "field_ordinals": [1], **common},
        {"record_type": "constraint", "id": "c-fk", "native_id": "orders_customer_fk",
         "kind": "foreign", "object_id": orders.id, "target_object_id": customers.id,
         "field_ordinals": [2], "target_field_ordinals": [1], **common},
    ]
    profiles = [
        Profile(id="p-email", field_id="f-email", object_id=customers.id,
                method="source_statistic", null_fraction=0.031, distinct_estimate=11800.0,
                uniqueness=0.98, average_width_bytes=24,
                stats_updated_at=datetime(2026, 8, 30, 12, 0, tzinfo=UTC), **common),
    ]
    diagnostics = [
        Diagnostic(id="d-1", code="IDENTITY_UNAVAILABLE", message=f"dropped {HOSTILE}", **common)
    ]
    tokens = [
        ValueTokens(id="t-1", field_id="f-cid", object_id=customers.id, key_id="run-1",
                    sample_count=1, sample_method="first_rows_limit:1", observed_count=1,
                    distinct_count=1, truncated=False, tokens=[TOKEN], **common)
    ]
    write_artifact(
        path,
        "collector-bundle",
        {
            "sources.jsonl": [
                Source(id="shop", kind="postgresql", name="shop", transport="tls-verified",
                       **common)
            ],
            "objects.jsonl": [customers, orders, returns, hostile],
            "fields.jsonl": fields,
            "constraints.jsonl": constraints,
            "profiles.jsonl": profiles,
            "diagnostics.jsonl": diagnostics,
            "tokens.jsonl": tokens,
        },
        producer="test",
        source_run_ids=["run-a"],
        completeness=completeness,
        signing_key=KEY.encode(),
        key_id="test",
    )
    return path


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> BuilderConfig:
    monkeypatch.setenv("BUNDLE_KEY", KEY)
    return BuilderConfig(signing_keys={"test": "BUNDLE_KEY"})


@pytest.fixture
def built(tmp_path: Path, config: BuilderConfig) -> dict[str, Path]:
    bundle = _bundle(tmp_path / "bundle")
    assemble([bundle], tmp_path / "snapshot", config)
    return {"bundle": bundle, "snapshot": tmp_path / "snapshot"}


def _render(built: dict[str, Path], config: BuilderConfig, out: Path, bundles=None) -> str:
    render_catalog(
        built["snapshot"], [built["bundle"]] if bundles is None else bundles, out, config
    )
    return out.read_text(encoding="utf-8")


# --- completeness -------------------------------------------------------------------------


def test_the_page_carries_every_part_of_the_catalogue(built, config, tmp_path):
    page = _render(built, config, tmp_path / "catalog.html")
    for expected in (
        "public.customers", "public.orders", "customer_id", "email",
        "~12,000</b> rows", "pg_class.reltuples",   # object size and where it came from
        "PK", "<span class='badge'>primary</span> <code>id</code>",  # keys, by column name
        "3.1%", "11,800", "98.0%", ">24<",            # column statistics, from the bundle
        "analyzed 2026-08-30 12:00Z",                 # how fresh those statistics are
        "IDENTITY_UNAVAILABLE",                       # what could not be catalogued
        "DECLARES_FK", "REFERENCE_CANDIDATE",         # relationships of both kinds
        "not calibrated",                             # confidence is null until measured
        "tls-verified",                               # how the source was reached
    ):
        assert expected in page, expected


def test_the_returned_counts_match_the_snapshot(built, config, tmp_path):
    counts = render_catalog(built["snapshot"], [built["bundle"]], tmp_path / "c.html", config)
    assert counts == {
        "sources": 1, "objects": 4, "fields": 6, "declared_fks": 1, "candidates": 1,
        "diagnostics": 1,
    }


def test_a_partial_snapshot_says_so_prominently(tmp_path, config):
    bundle = _bundle(tmp_path / "bundle", completeness="partial")
    assemble([bundle], tmp_path / "snapshot", config)
    page = _render({"bundle": bundle, "snapshot": tmp_path / "snapshot"}, config,
                   tmp_path / "c.html")
    assert "Completeness: <b>partial</b>" in page
    assert "banner bad" in page


def test_without_bundles_the_page_says_what_is_missing(built, config, tmp_path):
    page = _render(built, config, tmp_path / "c.html", bundles=[])
    assert "No bundle was supplied for <code>shop</code>" in page
    assert "<span class='badge'>primary</span>" not in page
    assert "public.customers" in page, "the snapshot's own content is still rendered"


# --- safety -------------------------------------------------------------------------------


def test_hostile_names_are_rendered_as_text(built, config, tmp_path):
    page = _render(built, config, tmp_path / "c.html")
    assert HOSTILE not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert HOSTILE_ATTRIBUTE not in page, "a quote in a name must not close an attribute"
    assert "onmouseover='" not in page


def test_the_only_script_is_the_pinned_filter(built, config, tmp_path):
    page = _render(built, config, tmp_path / "c.html")
    scripts = re.findall(r"<script>(.*?)</script>", page, flags=re.DOTALL)
    assert scripts == [SCRIPT]
    digest = base64.b64encode(hashlib.sha256(SCRIPT.encode()).digest()).decode()
    policy = re.search(r"Content-Security-Policy' content=\"([^\"]+)\"", page)
    assert policy is not None
    assert "default-src 'none'" in policy.group(1)
    assert f"script-src 'sha256-{digest}'" in policy.group(1)
    assert "unsafe-inline" not in policy.group(1).split("script-src", 1)[1].split(";", 1)[0]


def test_the_page_loads_nothing_from_anywhere(built, config, tmp_path):
    page = _render(built, config, tmp_path / "c.html")
    assert "http://" not in page and "https://" not in page
    assert " src=" not in page and "<link" not in page


def test_keys_are_shown_by_column_not_by_internal_identifier(built, config, tmp_path):
    """Found on a real run: the page listed a PostgreSQL OID and a MongoDB UUID in bytes form."""
    page = _render(built, config, tmp_path / "c.html")
    assert "customers_pkey" not in page


def test_value_tokens_never_reach_the_page(built, config, tmp_path):
    page = _render(built, config, tmp_path / "c.html")
    assert TOKEN not in page


# --- the bundle must be the one the snapshot was built from ---------------------------------


def test_a_bundle_from_another_collection_is_refused(built, config, tmp_path):
    """Same source, collected at another time: its statistics describe another state."""
    other = _bundle(tmp_path / "other", when=datetime(2026, 9, 2, tzinfo=UTC))
    out = tmp_path / "c.html"
    with pytest.raises(OperatorError, match="not assembled into this snapshot"):
        render_catalog(built["snapshot"], [other], out, config)
    assert not out.exists()


def test_a_failed_render_leaves_the_previous_page(built, config, tmp_path):
    out = tmp_path / "c.html"
    _render(built, config, out)
    before = out.read_bytes()
    other = _bundle(tmp_path / "other", when=datetime(2026, 9, 2, tzinfo=UTC))
    with pytest.raises(OperatorError):
        render_catalog(built["snapshot"], [other], out, config)
    assert out.read_bytes() == before
    assert list(tmp_path.glob(".catalog-*")) == [], "no temporary file is left behind"


def test_a_bundle_with_a_bad_signature_is_refused(built, tmp_path, monkeypatch):
    monkeypatch.setenv("BUNDLE_KEY", "a-different-key-of-sufficient-length-000")
    with pytest.raises(ValueError, match="invalid artifact signature"):
        render_catalog(
            built["snapshot"], [built["bundle"]], tmp_path / "c.html",
            BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
        )


def test_bundles_without_a_config_are_refused(built, tmp_path):
    with pytest.raises(OperatorError, match="--config is required"):
        render_catalog(built["snapshot"], [built["bundle"]], tmp_path / "c.html", None)


def test_a_collector_bundle_is_not_a_snapshot(config, tmp_path):
    """Unsigned, so it reaches the content-type check; a signed one fails earlier, for lack of
    a key, which would leave the check itself untested."""
    common = _common(datetime(2026, 9, 1, tzinfo=UTC))
    write_artifact(
        tmp_path / "unsigned",
        "collector-bundle",
        {"sources.jsonl": [Source(id="shop", kind="postgresql", name="shop", **common)]},
        producer="test",
        source_run_ids=["run-a"],
    )
    with pytest.raises(OperatorError, match="expected a catalog snapshot"):
        render_catalog(tmp_path / "unsigned", [], tmp_path / "c.html", config)


# --- determinism and the command line ----------------------------------------------------


def test_rendering_is_deterministic(built, config, tmp_path):
    first = _render(built, config, tmp_path / "a.html")
    second = _render(built, config, tmp_path / "b.html")
    assert first == second


def test_the_render_command(built, tmp_path, monkeypatch):
    monkeypatch.setenv("BUNDLE_KEY", KEY)
    (tmp_path / "builder.toml").write_text(
        '[builder]\nsigning_keys = { test = "BUNDLE_KEY" }\n', encoding="utf-8"
    )
    out = tmp_path / "catalog.html"
    result = CliRunner().invoke(
        builder_main,
        ["render", "--snapshot", str(built["snapshot"]), "--bundle", str(built["bundle"]),
         "--config", str(tmp_path / "builder.toml"), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["objects"] == 4
    assert out.exists()


def test_the_render_command_reports_a_mismatch_without_a_traceback(built, tmp_path, monkeypatch):
    monkeypatch.setenv("BUNDLE_KEY", KEY)
    (tmp_path / "builder.toml").write_text(
        '[builder]\nsigning_keys = { test = "BUNDLE_KEY" }\n', encoding="utf-8"
    )
    other = _bundle(tmp_path / "other", when=datetime(2026, 9, 2, tzinfo=UTC))
    result = CliRunner().invoke(
        builder_main,
        ["render", "--snapshot", str(built["snapshot"]), "--bundle", str(other),
         "--config", str(tmp_path / "builder.toml"), "--out", str(tmp_path / "c.html")],
    )
    assert result.exit_code == 1
    assert "not assembled into this snapshot" in result.output
    assert "Traceback" not in result.output


# --- found in review ------------------------------------------------------------------------


def test_the_same_bundle_twice_is_refused(built, config, tmp_path):
    """It used to double every key badge and diagnostic."""
    with pytest.raises(OperatorError, match="more than one bundle"):
        render_catalog(
            built["snapshot"], [built["bundle"], built["bundle"]], tmp_path / "c.html", config
        )


def test_the_page_gets_ordinary_file_permissions(built, config, tmp_path):
    """mkstemp makes files owner-only; a report in a shared directory must stay readable."""
    import os

    previous = os.umask(0o022)
    try:
        _render(built, config, tmp_path / "c.html")
    finally:
        os.umask(previous)
    assert (tmp_path / "c.html").stat().st_mode & 0o777 == 0o644


def test_the_filter_index_uses_the_same_lowercasing_as_the_browser():
    """`casefold()` turns "ß" into "ss", which the page's `toLowerCase()` never produces."""
    assert "Straße".lower() == "straße"
    import inspect

    from dbig_builder import render

    assert ".casefold()" not in inspect.getsource(render)
    assert "toLowerCase()" in render.SCRIPT


def test_a_partial_snapshot_says_why(tmp_path, config):
    bundle = _bundle(tmp_path / "bundle", completeness="partial")
    assemble([bundle], tmp_path / "snapshot", config)
    page = _render({"bundle": bundle, "snapshot": tmp_path / "snapshot"}, config,
                   tmp_path / "c.html")
    assert "<code>SOURCE_BUNDLE_PARTIAL shop</code>" in page


def test_model_suggestions_are_shown_on_the_candidate_they_review(built, config, tmp_path):
    """They cite the edge through `subject_id`, not through the edge's `evidence_ids`, so the
    page used to show none of them."""
    from dbig_contracts.artifact import read_artifact
    from dbig_contracts.models import Evidence

    _, data = read_artifact(built["snapshot"])
    candidate = next(
        edge for edge in data["edges.jsonl"] if edge.model_dump()["kind"] == "REFERENCE_CANDIDATE"
    )
    suggestion = Evidence(
        id="model-1", kind="model_suggestion", subject_id=candidate.id,
        metrics={"decision": "supports", "model_digest": "rev-1"},
        explanation="names and types agree", source_id="shop",
        observed_at=datetime(2026, 9, 1, tzinfo=UTC), collector_version="t", provenance="rev-1",
    )
    write_artifact(
        tmp_path / "reviewed",
        "catalog-snapshot",
        {
            "nodes.jsonl": data["nodes.jsonl"], "edges.jsonl": data["edges.jsonl"],
            "evidence.jsonl": sorted([*data["evidence.jsonl"], suggestion], key=lambda e: e.id),
            "runs.jsonl": data["runs.jsonl"],
        },
        producer="test", source_run_ids=[record.id for record in data["runs.jsonl"]],
    )
    page = _render({"bundle": built["bundle"], "snapshot": tmp_path / "reviewed"}, config,
                   tmp_path / "c.html")
    assert "<b>supports</b>: names and types agree" in page
