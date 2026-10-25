"""H3: a report-shaped synthetic scenario, run through every stage, scored against planted truth.

Plan section 9, H3. Four invented sources reproduce the failure SHAPES of the live report, not its
names (plan section 2.4):

* `pgmain` (PostgreSQL-like): no declared keys, lowercase names with no separator (`warehouseid`),
  keys known only from a planner statistic, two decoys (`valid`, `paid`) that must propose nothing.
* `chsrc` (ClickHouse-like): camelCase columns (`supplierID`, `supplierRevenue`) and a declared
  one-column sorting key, no statistics.
* `mgsrc` (MongoDB-like): a declared reference, plus schema-description collections that are
  excluded from search with `exclude_objects`.
* The two datasets in `pgmain` and `chsrc` carry the same table names. Their `supplier` and
  `warehouse` keys are DISJOINT value domains (a name match that is false); their `region` keys
  are the SAME domain (a name match that is true).

Ground truth is planted in `TRUE_LINKS` and `FALSE_LINKS` from those domains, not read from any
output. Each stage is run the way the existing tests run it: `assemble` (value tokens present =
sampling on, absent = sampling off), `render_catalog`, `index_snapshot`, `search`,
`answer_question` against the recording stub, `build_graph`, `find_paths`, `render_plan`.

`run_scenario` returns plain counts. The same function is run against the baseline commit by
`.tmp/h3/run_baseline.py` (it uses only calls that exist at that commit), and the numbers go in
`docs/plans/catalogue-fixes/PHASE5_H3.md`. The tests below assert the AFTER numbers; the
constants they compare to are planted counts, never read back from the code under test.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import catalogue_fixture_kit as kit
import pytest
from catalogue_fixture_kit import (
    MODEL_KEY,
    ModelStub,
    build_snapshot,
    cite_first,
    support_everything,
)
from dbig_builder.builder import assemble
from dbig_builder.config import BuilderConfig, ReviewConfig
from dbig_builder.render import render_catalog
from dbig_contracts.artifact import read_artifact
from dbig_planner.config import PlannerConfig
from dbig_planner.graph import build_graph
from dbig_planner.paths import PathRequest, find_paths
from dbig_planner.plans import PlanRequest, render_plan
from dbig_requester.answer import answer_question
from dbig_requester.config import AnswerConfig
from dbig_requester.search import SearchRequest, index_snapshot, search

PG, CH, MG = "pgmain", "chsrc", "mgsrc"
EXCLUDE = {MG: ["__schemas", "*_source_profiles"]}


# --- the planted scenario -----------------------------------------------------------------------


def _ints(start: int, count: int) -> list[int]:
    return list(range(start, start + count))


def sources(sampling: bool) -> dict[str, dict]:
    """The three sources. `sampling` adds value tokens for the key columns (what a source config
    with `sample_rows` produces); without it the bundles carry none."""

    def values(**columns: list[int]) -> dict:
        return {"values": columns} if sampling else {}

    pg_tables = {
        "supplier": {
            "fields": [("supplierid", "integer"), ("regionid", "integer"), ("website", "text")],
            "unique": {"supplierid": 1.0},
            # Free text that mentions schema words only: the shape that defeated the old
            # "nothing matches" guard (B4).
            "descriptions": {"website": "The column that holds the number stored in this table"},
            **values(supplierid=_ints(1, 12), regionid=_ints(1, 8)),
        },
        "warehouse": {
            "fields": [("warehouseid", "integer"), ("supplierid", "integer"), ("city", "text")],
            "unique": {"warehouseid": 1.0},
            **values(warehouseid=_ints(1, 12), supplierid=_ints(1, 12)),
        },
        "parcel": {
            "fields": [
                ("parcelid", "integer"), ("warehouseid", "integer"),
                ("tierlevelid", "integer"), ("valid", "integer"), ("paid", "integer"),
            ],
            "unique": {"parcelid": 1.0},
            # 8 distinct sampled values of which 2 are in the parent: a true link with a 25%
            # overlap (partial clone) that must never be dropped.
            **values(parcelid=_ints(1, 12), warehouseid=_ints(1, 12),
                     tierlevelid=[1, 2, *_ints(900, 6)]),
        },
        "servicetier": {
            "fields": [("tierlevelid", "integer"), ("tiername", "text")],
            "unique": {"tierlevelid": 1.0},
            **values(tierlevelid=_ints(1, 6)),
        },
        "region": {
            "fields": [("regionid", "integer"), ("regionname", "text")],
            "unique": {"regionid": 1.0},
            **values(regionid=_ints(1, 8)),
        },
    }
    ch_tables = {
        "Supplier": {
            "fields": [("supplierID", "Int64"), ("regionID", "Int64"),
                       ("supplierName", "String"), ("supplierRevenue", "Float64")],
            "weak_key": "supplierID",
            **values(supplierID=_ints(100001, 12), regionID=_ints(1, 8)),
        },
        "Warehouse": {
            "fields": [("warehouseID", "Int64"), ("supplierID", "Int64"),
                       ("warehouseCity", "String")],
            "weak_key": "warehouseID",
            **values(warehouseID=_ints(100001, 12), supplierID=_ints(100001, 12)),
        },
        "Region": {
            "fields": [("regionID", "Int64"), ("regionName", "String")],
            "weak_key": "regionID",
            **values(regionID=_ints(1, 8)),
        },
        "Category": {
            "fields": [("categoryID", "Int64"), ("categoryName", "String")],
            "weak_key": "categoryID",
        },
    }
    mg_tables = {
        "merchant": {
            "fields": [("_id", "str"), ("label", "str")],
            "key": ["_id"],
            "kind": "collection",
        },
        "merchantnote": {
            "fields": [("_id", "str"), ("merchantid", "str"), ("body", "str")],
            "key": ["_id"], "kind": "collection", "foreign": [("merchantid", "merchant", "_id")],
        },
        "__schemas": {
            "fields": [("db_schema.supplier.columns.website", "str")], "kind": "collection",
        },
        "supplier_source_profiles": {"fields": [("website", "str")], "kind": "collection"},
    }
    return {
        PG: {"kind": "postgresql", "tables": pg_tables},
        CH: {"kind": "clickhouse", "tables": ch_tables},
        MG: {"kind": "mongodb", "tables": mg_tables},
    }


def _link(child: str, parent: str) -> tuple[str, str, str, str, str, str]:
    """`pg.warehouse.supplierid` style strings to (source, table, column) pairs flattened."""
    return (*child.split("."), *parent.split("."))  # type: ignore[return-value]


# Planted from the value domains above. Same domain: a true link. Disjoint domains with a matching
# name: a false link. Nothing else may be proposed.
TRUE_LINKS = {
    # inside pgmain: the lowercase no-separator names, no declared key (D3, DEC-12)
    _link("pgmain.warehouse.supplierid", "pgmain.supplier.supplierid"),
    _link("pgmain.parcel.warehouseid", "pgmain.warehouse.warehouseid"),
    _link("pgmain.supplier.regionid", "pgmain.region.regionid"),
    _link("pgmain.parcel.tierlevelid", "pgmain.servicetier.tierlevelid"),  # 25% overlap
    # inside chsrc: camelCase names, parents known only by the sorting key (DEC-2)
    _link("chsrc.Warehouse.supplierID", "chsrc.Supplier.supplierID"),
    _link("chsrc.Supplier.regionID", "chsrc.Region.regionID"),
    # across: the same region domain in both datasets
    _link("pgmain.supplier.regionid", "chsrc.Region.regionID"),
    _link("chsrc.Supplier.regionID", "pgmain.region.regionid"),
    _link("pgmain.region.regionid", "chsrc.Region.regionID"),
    _link("chsrc.Region.regionID", "pgmain.region.regionid"),
}
FALSE_LINKS = {
    # across: same names, disjoint value domains
    _link("pgmain.supplier.supplierid", "chsrc.Supplier.supplierID"),
    _link("chsrc.Supplier.supplierID", "pgmain.supplier.supplierid"),
    _link("pgmain.warehouse.supplierid", "chsrc.Supplier.supplierID"),
    _link("chsrc.Warehouse.supplierID", "pgmain.supplier.supplierid"),
    _link("pgmain.warehouse.warehouseid", "chsrc.Warehouse.warehouseID"),
    _link("chsrc.Warehouse.warehouseID", "pgmain.warehouse.warehouseid"),
    _link("pgmain.parcel.warehouseid", "chsrc.Warehouse.warehouseID"),
}
DECOYS = {"valid", "paid"}  # `xid` style: the stem is not a known object, so nothing is proposed

# Planted join questions: (label, from (source, table), to (source, table)).
JOIN_QUESTIONS = {
    "J1 two hops, lowercase": ((PG, "parcel"), (PG, "supplier")),
    "J2 two hops, lowercase": ((PG, "warehouse"), (PG, "region")),
    "J3 two hops, camelCase": ((CH, "Warehouse"), (CH, "Region")),
    "J4 across sources": ((PG, "supplier"), (CH, "Supplier")),
    "X1 shared key name": ((PG, "parcel"), (PG, "servicetier")),
}
NO_PATH_QUESTION = ((PG, "parcel"), (CH, "Category"))
DECLARED_QUESTION = ((MG, "merchantnote"), (MG, "merchant"))

# Planted retrieval questions. Each target is (table or None, name): the entry the model must be
# shown among the top ten to answer. A column is (table, column); an object is (None, name).
QUESTIONS = {
    "website": ("Which column holds a supplier's website?", {("supplier", "website")}),
    # Only the split word `revenue` can match: the table word is left out on purpose.
    "revenue": ("What is the revenue?", {("Supplier", "supplierRevenue")}),
    "mongo": (
        "Which collections are in MongoDB?",
        {(None, "merchant"), (None, "merchantnote")},
    ),
    "engine": (
        "Which tables are in ClickHouse?",
        {(None, "Supplier"), (None, "Warehouse"), (None, "Region"), (None, "Category")},
    ),
    "plural": ("List all the categories", {(None, "Category")}),
    "link": (
        "How does a warehouse relate to its supplier?",
        {(None, "warehouse"), (None, "supplier"), ("warehouse", "supplierid")},
    ),
    "tier": ("In which table is the service tier name?", {("servicetier", "tiername")}),
    "city": ("What is the city of a warehouse?",
             {("warehouse", "city"), ("Warehouse", "warehouseCity")}),
}
# No catalogue entry is about a card: only schema words in a description can match.
UNANSWERABLE = "Which column holds a payment card number stored in a table?"
NOISE_OBJECTS = {"__schemas", "supplier_source_profiles"}


# --- reading outputs ----------------------------------------------------------------------------


class _Env:
    """`monkeypatch.setenv` for a caller with no pytest (the baseline runner)."""

    @staticmethod
    def setenv(name: str, value: str) -> None:
        os.environ[name] = value


def read_links(snapshot: Path) -> list[dict]:
    """Every REFERENCE_CANDIDATE as a planted-link key plus the sentence the page shows."""
    _, data = read_artifact(snapshot)
    names = {item.id: item.name for item in data["nodes.jsonl"]}
    objects = {item.id: item for item in data["nodes.jsonl"] if item.record_type == "object"}
    owner = {
        item.id: objects[item.object_id]
        for item in data["nodes.jsonl"]
        if item.record_type == "field"
    }
    found = []
    for edge in data["edges.jsonl"]:
        if edge.kind != "REFERENCE_CANDIDATE":
            continue
        for child, parent in edge.model_extra["field_pairs"]:
            found.append({
                "id": edge.id,
                "link": (
                    owner[child].source_id, owner[child].name, names[child],
                    owner[parent].source_id, owner[parent].name, names[parent],
                ),
                "text": edge.explanation.casefold(),
            })
    return found


def score_links(snapshot: Path) -> dict[str, int]:
    found = read_links(snapshot)
    links = {item["link"] for item in found}
    return {
        "candidates": len(found),
        "true_found": len(links & TRUE_LINKS),
        "false_found": len(links & FALSE_LINKS),
        "true_missed": len(TRUE_LINKS - links),
        "unplanned": len(links - TRUE_LINKS - FALSE_LINKS),
        "decoy_children": sum(1 for link in links if link[2] in DECOYS),
        "values_not_compared": sum("values not compared" in item["text"] for item in found),
        "with_overlap_number": sum(
            bool(re.search(r"overlap (?:≈|at least )\d+%", item["text"])) for item in found
        ),
    }


def _object_ids(snapshot: Path) -> dict[tuple[str, str], str]:
    _, data = read_artifact(snapshot)
    return {
        (item.source_id, item.name): item.id
        for item in data["nodes.jsonl"]
        if item.record_type == "object"
    }


def _answer_config(url: str) -> AnswerConfig:
    return AnswerConfig(
        base_url=url, model="m", model_digest="d", api_key_env="MODEL_KEY", timeout_seconds=10
    )


def _recall(items: list[dict], targets: set[tuple[str | None, str]]) -> int:
    shown = {(item.get("object_name"), item["name"]) for item in items}
    shown |= {(None, item["name"]) for item in items if item["kind"] != "field"}
    return sum(1 for target in targets if target in shown)


# --- the stages ---------------------------------------------------------------------------------


def stage_build(root: Path, env: Any, sampling: bool) -> tuple[Path, dict]:
    snapshot = build_snapshot(root, env, sources(sampling), name=f"snap-{int(sampling)}")
    manifest, _ = read_artifact(snapshot)
    page = root / f"page-{int(sampling)}.html"
    counts = render_catalog(snapshot, [], page, None)
    text = page.read_text(encoding="utf-8")
    quarter = _link("pgmain.parcel.tierlevelid", "pgmain.servicetier.tierlevelid")
    metrics = {
        **score_links(snapshot),
        "quarter_overlap_sentence": next(
            (item["text"] for item in read_links(snapshot) if item["link"] == quarter), ""
        ),
        "completeness": manifest.completeness,
        "page_values_not_compared": text.lower().count("values not compared"),
        "render_diagnostics": counts["diagnostics"],
    }
    return snapshot, metrics


def stage_review(root: Path, env: Any, sampling: bool) -> dict:
    """The reviewer on the same sources: how many model calls, and is the snapshot complete."""
    env.setenv("MODEL_KEY", MODEL_KEY)
    with ModelStub(support_everything) as stub:
        review = ReviewConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=10,
        )
        snapshot = build_snapshot(
            root, env, sources(sampling), review=review, name=f"reviewed-{int(sampling)}"
        )
        calls = len(stub.requests)
    manifest, _ = read_artifact(snapshot)
    return {"review_calls": calls, "review_completeness": manifest.completeness}


def stage_search(snapshot: Path, root: Path, sampling: bool) -> tuple[Path, dict]:
    plain = root / f"state-plain-{int(sampling)}"
    index_snapshot(snapshot, plain)
    state = root / f"state-{int(sampling)}"
    try:
        index_snapshot(snapshot, state, EXCLUDE)
        excludable = True
    except TypeError:  # the baseline has no exclude list
        index_snapshot(snapshot, state)
        excludable = False

    def names(path: Path, query: str) -> list[str]:
        request = SearchRequest(query=query, limit=50)
        return [item["name"] for item in search(path, request)["results"]]

    return state, {
        "search_revenue_hits": sum(name == "supplierRevenue" for name in names(state, "revenue")),
        "search_plural_hits": sum(name == "Category" for name in names(state, "categories")),
        "search_noise_default": sum(
            1 for item in search(plain, SearchRequest(query="website", limit=50))["results"]
            if item.get("object_name") in NOISE_OBJECTS or item["name"] in NOISE_OBJECTS
        ),
        "search_noise_excluded": sum(
            1 for item in search(state, SearchRequest(query="website", limit=50))["results"]
            if item.get("object_name") in NOISE_OBJECTS or item["name"] in NOISE_OBJECTS
        ),
        "exclude_supported": int(excludable),
    }


def stage_ask(state: Path, env: Any) -> dict:
    """`ask` against a stub that behaves like llama.cpp (400 on a bounded schema)."""
    env.setenv("MODEL_KEY", MODEL_KEY)
    found = total = answered = cited_names = relationship_rows = noise_items = 0
    per_question: dict[str, int] = {}
    with ModelStub(cite_first, reject_bounds=True) as stub:
        config = _answer_config(stub.url)
        for label, (question, targets) in QUESTIONS.items():
            before = len(stub.requests)
            reply = answer_question(state, question, config)
            if len(stub.requests) > before:
                payload = json.loads(stub.requests[-1]["body"]["messages"][-1]["content"])
                hit = _recall(payload["items"], targets)
                relationship_rows += len(payload.get("relationships", []))
                noise_items += sum(
                    1 for item in payload["items"]
                    if item.get("object_name") in NOISE_OBJECTS or item["name"] in NOISE_OBJECTS
                )
            else:
                hit = 0
            per_question[label] = hit
            found += hit
            total += len(targets)
            answered += int("error" not in reply and bool(reply.get("citations")))
            cited_names += int(bool(reply.get("cited")))
        before = len(stub.requests)
        refused = answer_question(state, UNANSWERABLE, config)
        unanswerable_calls = len(stub.requests) - before
    return {
        "recall_found": found,
        "recall_total": total,
        "recall_by_question": per_question,
        "questions_answered_with_citation": answered,
        "replies_with_resolved_names": cited_names,
        "relationship_rows_sent": relationship_rows,
        "noise_items_sent_to_model": noise_items,
        "unanswerable_model_calls": unanswerable_calls,
        "unanswerable_code": refused.get("error", {}).get("code"),
        "question_count": len(QUESTIONS),
    }


def stage_planner(snapshot: Path, root: Path, sampling: bool) -> dict:
    graph_dir = root / f"graph-{int(sampling)}"
    (root / "observations").mkdir(exist_ok=True)
    # P7-5: this test pins the warning on an unmeasured cross-source path (hidden by default).
    config = PlannerConfig(graph_dir=graph_dir, include_unverified_cross_source=True)
    build_graph(snapshot, root / "observations", graph_dir, config)
    ids = _object_ids(snapshot)
    _, data = read_artifact(snapshot)
    edge_links = {item["id"]: item["link"] for item in read_links(snapshot)}
    declared = {edge.id for edge in data["edges.jsonl"] if edge.kind == "DECLARES_FK"}

    def ask(pair: tuple[tuple[str, str], tuple[str, str]]) -> list[dict]:
        request = PathRequest(from_id=ids[pair[0]], to_id=ids[pair[1]], max_paths=10)
        return find_paths(graph_dir, request, "join", config)["paths"]

    metrics: dict[str, Any] = {}
    joined = 0
    false_paths = false_flagged = 0
    for label, pair in JOIN_QUESTIONS.items():
        paths = ask(pair)
        through_false = [
            item for item in paths
            if any(edge_links.get(edge) in FALSE_LINKS for edge in item["edge_ids"])
        ]
        true_only = [item for item in paths if item not in through_false]
        flagged = [item for item in through_false if item.get("warnings")]
        metrics[label] = {
            "paths": len(paths), "false_paths": len(through_false),
            "false_paths_flagged": len(flagged),
            "true_paths": len(true_only),
            "first_warns": bool(paths and paths[0].get("warnings")),
        }
        joined += int(bool(true_only))
        false_paths += len(through_false)
        false_flagged += len(flagged)
    metrics_out: dict[str, Any] = {
        "join_questions_with_true_path": joined,
        "join_questions": len(JOIN_QUESTIONS),
        "join_paths_through_false_link": false_paths,
        "join_false_paths_flagged": false_flagged,
        "join_detail": metrics,
        "no_path_question_paths": len(ask(NO_PATH_QUESTION)),
    }
    # Plans: same-source SQL, the cross-source path, and the MongoDB reference.
    plans = {}
    for label, pair in (("J1", JOIN_QUESTIONS["J1 two hops, lowercase"]),
                        ("J4", JOIN_QUESTIONS["J4 across sources"]),
                        ("mongo", DECLARED_QUESTION)):
        paths = ask(pair)
        if not paths:
            plans[label] = None
            continue
        plan = render_plan(graph_dir, PlanRequest(path_id=paths[0]["path_id"]), config)
        plans[label] = {
            "status": plan["status"],
            "steps": [step["kind"] for step in plan["steps"]],
            "unresolved": sorted(plan.get("unresolved_prerequisites", [])),
            "warnings": sorted(plan.get("warnings", [])),
        }
    metrics_out["plans"] = plans
    metrics_out["declared_edges"] = len(declared)
    return metrics_out


def stage_partial(root: Path, env: Any) -> dict:
    """Partial snapshots from three causes: which reasons exist, and where they are shown."""
    env.setenv("MODEL_KEY", MODEL_KEY)
    # Plain snake_case names and a declared key, so that the reviewer has a candidate to review
    # at every commit under test, the baseline included.
    one_source = {"alpha": {"tables": {
        "vendor": {"fields": [("id", "integer")], "key": ["id"]},
        "shipment": {"fields": [("id", "integer"), ("vendor_id", "integer")], "key": ["id"]},
    }}}
    real_write = kit.write_artifact
    results: dict[str, Any] = {}

    def shown(snapshot: Path, name: str) -> dict:
        manifest, _ = read_artifact(snapshot)
        page = root / f"partial-{name}.html"
        counts = render_catalog(snapshot, [], page, None)
        text = page.read_text(encoding="utf-8")
        reasons = list(getattr(manifest, "partial_reasons", None) or [])
        # The builder run row carries the reasons on the page; the codes are the words of it.
        codes = sorted(set(re.findall(r"\b[A-Z]{2,}(?:_[A-Z]+)+\b", text)))
        return {
            "completeness": manifest.completeness,
            "manifest_reasons": reasons,
            "page_codes": codes,
            "render_diagnostics": counts["diagnostics"],
        }

    with ModelStub(support_everything, status=400) as stub:
        review = ReviewConfig(
            base_url=stub.url, model="m", model_digest="d", api_key_env="MODEL_KEY",
            timeout_seconds=10,
        )
        failed = build_snapshot(root, env, one_source, review=review, name="p-review")
    results["review_failed"] = shown(failed, "review")

    def partial_write(*args: Any, **kwargs: Any) -> Any:
        return real_write(*args, completeness="partial", **kwargs)

    kit.write_artifact = partial_write
    try:
        partial_bundle = build_snapshot(root, env, one_source, name="p-bundle")
    finally:
        kit.write_artifact = real_write
    results["source_bundle_partial"] = shown(partial_bundle, "bundle")

    complete = build_snapshot(root, env, one_source, name="p-complete")
    env.setenv("BUNDLE_KEY", kit.BUNDLE_KEY)
    bundle = root / "bundle-alpha-p-complete"
    reused = root / "p-reused"
    assemble(
        [bundle], reused, BuilderConfig(signing_keys={"test": "BUNDLE_KEY"}),
        partial_source_receipts={"alpha": "receipt-1"},
    )
    results["previous_bundle_used"] = shown(reused, "reused")

    results["complete"] = shown(complete, "complete")
    return results


def run_scenario(root: Path, env: Any, sampling: bool) -> dict:
    """Every stage once; plain counts. Also run, unchanged, against the baseline commit."""
    root.mkdir(parents=True, exist_ok=True)
    snapshot, built = stage_build(root, env, sampling)
    state, retrieval = stage_search(snapshot, root, sampling)
    return {
        "build": built,
        "review": stage_review(root, env, sampling),
        "search": retrieval,
        "ask": stage_ask(state, env),
        "planner": stage_planner(snapshot, root, sampling),
    }


# --- the checks ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def outcome(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """The scenario run once per mode (the stages are slow to repeat per assertion)."""
    mp = pytest.MonkeyPatch()
    try:
        root = tmp_path_factory.mktemp("h3")
        return {
            "on": run_scenario(root / "on", mp, True),
            "off": run_scenario(root / "off", mp, False),
            "partial": stage_partial(root / "partial", mp),
        }
    finally:
        mp.undo()


def test_the_planted_truth_is_consistent():
    """The ground truth itself: no link is both true and false, none is within one table, and
    every planted link names a source, a table and a column."""
    assert TRUE_LINKS.isdisjoint(FALSE_LINKS)
    assert (len(TRUE_LINKS), len(FALSE_LINKS)) == (10, 7)
    assert all(len(link) == 6 for link in TRUE_LINKS | FALSE_LINKS)
    assert all(link[:2] != link[3:5] for link in TRUE_LINKS | FALSE_LINKS)


def test_sampling_on_proposes_every_true_link_and_no_false_one(outcome):
    build = outcome["on"]["build"]
    assert build["true_found"] == len(TRUE_LINKS), build
    assert build["true_missed"] == 0, build
    assert build["false_found"] == 0, build  # 0 of 12 sampled values shared: dropped
    assert build["unplanned"] == 0 and build["decoy_children"] == 0, build
    assert build["candidates"] == len(TRUE_LINKS)


def test_sampling_on_shows_the_overlap_number_and_keeps_a_quarter_overlap(outcome):
    build = outcome["on"]["build"]
    assert build["with_overlap_number"] == len(TRUE_LINKS), build
    assert build["values_not_compared"] == 0, build
    quarter = build["quarter_overlap_sentence"]
    assert "25%" in quarter and "of 8 sampled" in quarter, quarter


def test_sampling_off_keeps_every_candidate_and_says_values_were_not_compared(outcome):
    build = outcome["off"]["build"]
    assert build["true_found"] == len(TRUE_LINKS) and build["true_missed"] == 0, build
    # Nothing can be dropped on a measurement that was never made: the planted false links stay,
    # and every candidate (true or false) is labelled instead.
    assert build["false_found"] == len(FALSE_LINKS), build
    assert build["unplanned"] == 0 and build["decoy_children"] == 0, build
    assert build["candidates"] == len(TRUE_LINKS) + len(FALSE_LINKS)
    assert build["values_not_compared"] == build["candidates"], build
    assert build["with_overlap_number"] == 0, build
    assert build["page_values_not_compared"] >= build["candidates"], build


def test_the_sampling_modes_differ_by_exactly_the_planted_false_links(outcome):
    assert (
        outcome["off"]["build"]["candidates"] - outcome["on"]["build"]["candidates"]
        == len(FALSE_LINKS)
    )


def test_join_paths_for_lineage_free_questions_are_found_in_both_modes(outcome):
    for mode in ("on", "off"):
        planner = outcome[mode]["planner"]
        assert planner["join_questions"] == len(JOIN_QUESTIONS)
        assert planner["join_questions_with_true_path"] == len(JOIN_QUESTIONS), (mode, planner)
        assert planner["no_path_question_paths"] == 0, (mode, planner)


def test_sampling_on_has_no_join_path_through_a_false_link(outcome):
    planner = outcome["on"]["planner"]
    assert planner["join_paths_through_false_link"] == 0, planner
    detail = planner["join_detail"]["J4 across sources"]
    assert detail["paths"] >= 1 and detail["false_paths"] == 0 and not detail["first_warns"]


def test_sampling_off_the_false_cross_source_path_exists_and_carries_the_warning(outcome):
    detail = outcome["off"]["planner"]["join_detail"]["J4 across sources"]
    assert detail["false_paths"] >= 1, detail  # the one-hop name match is still a path
    assert detail["first_warns"], detail  # ... but it is flagged, not presented as evidence
    assert detail["true_paths"] >= 1  # the true route through the shared domain is also offered


def test_plans_are_rendered_without_execution(outcome):
    for mode in ("on", "off"):
        plans = outcome[mode]["planner"]["plans"]
        assert plans["J1"]["steps"] and plans["J1"]["status"], plans
        assert plans["J4"]["status"] == "unresolved", plans
        assert "approved_staging_or_federated_target" in plans["J4"]["unresolved"], plans
        assert plans["J4"]["steps"].count("source_read") == 2
        assert plans["J4"]["steps"][-1] == "logical_join"
        assert plans["mongo"] and plans["mongo"]["steps"], plans


def test_retrieval_puts_every_planted_target_in_the_top_ten(outcome):
    for mode in ("on", "off"):
        ask = outcome[mode]["ask"]
        assert ask["recall_found"] == ask["recall_total"], (mode, ask["recall_by_question"])


def test_ask_answers_every_answerable_question_against_a_llama_cpp_style_server(outcome):
    for mode in ("on", "off"):
        ask = outcome[mode]["ask"]
        assert ask["questions_answered_with_citation"] == ask["question_count"], ask
        assert ask["replies_with_resolved_names"] == ask["question_count"], ask


def test_an_unanswerable_question_gets_no_match_and_no_model_call(outcome):
    for mode in ("on", "off"):
        ask = outcome[mode]["ask"]
        assert ask["unanswerable_code"] == "NO_CATALOGUE_MATCH", ask
        assert ask["unanswerable_model_calls"] == 0, ask


def test_relationships_reach_the_model_with_their_evidence_status(outcome):
    for mode in ("on", "off"):
        assert outcome[mode]["ask"]["relationship_rows_sent"] >= 1, mode


def test_schema_description_collections_are_excluded_only_when_configured(outcome):
    for mode in ("on", "off"):
        search_counts = outcome[mode]["search"]
        assert search_counts["search_noise_default"] == 2, search_counts  # default: none excluded
        assert search_counts["search_noise_excluded"] == 0, search_counts


def test_search_splits_camel_case_and_folds_plurals(outcome):
    for mode in ("on", "off"):
        search_counts = outcome[mode]["search"]
        assert search_counts["search_revenue_hits"] == 1, search_counts
        assert search_counts["search_plural_hits"] == 1, search_counts


def test_the_reviewer_runs_in_batches_and_the_snapshot_stays_complete(outcome):
    for mode in ("on", "off"):
        review = outcome[mode]["review"]
        assert review["review_completeness"] == "complete", review
    # Batches of 5 (the configured default): ceil(candidates / 5) calls, not one call for all.
    on, off = outcome["on"]["review"], outcome["off"]["review"]
    assert on["review_calls"] == -(-outcome["on"]["build"]["candidates"] // 5), on
    assert off["review_calls"] == -(-outcome["off"]["build"]["candidates"] // 5), off


def test_a_complete_snapshot_reports_no_diagnostics(outcome):
    for mode in ("on", "off"):
        assert outcome[mode]["build"]["completeness"] == "complete"
        assert outcome[mode]["build"]["render_diagnostics"] == 0
    assert outcome["partial"]["complete"]["render_diagnostics"] == 0
    assert outcome["partial"]["complete"]["manifest_reasons"] == []


def test_every_partial_reason_is_shown_on_the_page_in_the_manifest_and_in_the_count(outcome):
    expected = {
        "review_failed": {"MODEL_REVIEW_FAILED", "MODEL_REJECTED_REQUEST"},
        "source_bundle_partial": {"SOURCE_BUNDLE_PARTIAL alpha"},
        "previous_bundle_used": {"PREVIOUS_COMPLETE_BUNDLE_USED alpha"},
    }
    for cause, reasons in expected.items():
        shown = outcome["partial"][cause]
        assert shown["completeness"] == "partial", (cause, shown)
        assert set(shown["manifest_reasons"]) == reasons, (cause, shown)
        assert shown["render_diagnostics"] == len(reasons), (cause, shown)
        for reason in reasons:
            assert reason.split(" ")[0] in shown["page_codes"], (cause, shown)


def test_nothing_sampled_or_secret_reaches_the_outputs(outcome, tmp_path_factory):
    """Counts and names only: no token digest, and not the model key, in any snapshot file."""
    root = tmp_path_factory.getbasetemp()
    tokens: set[str] = set()
    for bundle in root.glob("h3*/on/bundle-*"):
        path = bundle / "tokens.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                tokens.update(json.loads(line)["tokens"])
    assert tokens, "sampling on must have produced tokens to look for"
    for snapshot in root.glob("h3*/*/snap-*"):
        for path in snapshot.iterdir():
            content = path.read_text(encoding="utf-8", errors="replace")
            assert MODEL_KEY not in content
            assert not any(token in content for token in tokens), path.name

