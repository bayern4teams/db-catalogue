"""Bounded graph paths with lineage/reference separation and stable IDs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from itertools import islice
from pathlib import Path

import networkx as nx
from dbig_contracts.errors import OperatorError
from pydantic import BaseModel, ConfigDict, Field

from .config import PlannerConfig
from .graph import load_graph


class PathRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_id: str = Field(min_length=1)
    to_id: str = Field(min_length=1)
    max_hops: int | None = Field(default=None, ge=1, le=10)
    max_paths: int | None = Field(default=None, ge=1, le=100)


def _cache(directory: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(directory / "paths.sqlite3")
    connection.execute("CREATE TABLE IF NOT EXISTS paths(id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    connection.commit()
    return connection


def load_path(directory: Path, path_id: str) -> dict:
    with closing(_cache(directory)) as connection:
        row = connection.execute("SELECT body FROM paths WHERE id=?", (path_id,)).fetchone()
    if row is None:
        raise ValueError("UNKNOWN_PATH")
    return json.loads(row[0])


def _strength(edge: dict) -> int:
    return {"declared": 3, "executed": 3, "observed": 2}.get(edge["status"], 1)


def _crosses_sources(edge: dict, objects: dict) -> bool:
    left, right = objects.get(edge["from_id"]), objects.get(edge["to_id"])
    return bool(left and right and left["source_id"] != right["source_id"])


def _unverified_cross_source(edge: dict, objects: dict) -> bool:
    """A candidate between two sources whose values were never compared."""
    return (
        edge["status"] == "tentative"
        and _crosses_sources(edge, objects)
        and not edge.get("value_evidence", False)
    )


def find_paths(directory: Path, request: PathRequest, kind: str, config: PlannerConfig) -> dict:
    graph = load_graph(directory)
    if kind not in {"lineage", "join"}:
        raise ValueError("UNKNOWN_PATH_KIND")
    objects = graph["objects"]
    fields = graph["fields"]
    entity_kind = "object"
    entities = objects
    if kind == "lineage" and request.from_id in fields and request.to_id in fields:
        entity_kind = "field"
        entities = fields
    elif request.from_id not in objects or request.to_id not in objects:
        if kind == "lineage" and (
            request.from_id in fields or request.to_id in fields
        ):
            raise ValueError("MIXED_LINEAGE_ENTITY_KINDS")
        missing = ", ".join(
            identifier
            for identifier in (request.from_id, request.to_id)
            if identifier not in objects
        )
        raise OperatorError(
            f"UNKNOWN_OBJECT: no object named {missing} in this graph; check the identifier "
            "or rebuild the graph from a current snapshot"
        )
    if request.from_id == request.to_id:
        raise ValueError("SAME_OBJECT")
    max_hops = min(request.max_hops or config.max_hops, config.max_hops)
    max_paths = min(request.max_paths or config.max_paths, config.max_paths)
    edges = (
        graph.get("field_lineage", []) if entity_kind == "field"
        else graph["lineage"] if kind == "lineage" else graph["references"]
    )
    network: nx.Graph = nx.DiGraph() if kind == "lineage" else nx.Graph()
    selected: dict[tuple[str, str], dict] = {}
    for edge in edges:
        if kind == "join" and edge["status"] == "rejected":
            continue
        left, right = edge["from_id"], edge["to_id"]
        if left not in entities or right not in entities:
            continue
        key = (left, right) if kind == "lineage" else tuple(sorted((left, right)))
        # Between the same two objects a declared key beats a join seen in the logs, which beats
        # an inferred candidate: the path cites the strongest claim available for each hop.
        if key in selected and _strength(selected[key]) >= _strength(edge):
            continue
        selected[key] = edge
        if edge["status"] in {"declared", "executed", "observed"}:
            weight = 1
        elif _crosses_sources(edge, objects):
            weight = config.cross_source_candidate_weight
        else:
            weight = 2
        network.add_edge(left, right, weight=weight)
    found: list[dict] = []
    if network.has_node(request.from_id) and network.has_node(request.to_id):
        try:
            for nodes in islice(
                nx.shortest_simple_paths(network, request.from_id, request.to_id, weight="weight"),
                max_paths * 10,
            ):
                if len(nodes) - 1 > max_hops:
                    continue
                links = []
                for left, right in zip(nodes, nodes[1:], strict=False):
                    key = (left, right) if kind == "lineage" else tuple(sorted((left, right)))
                    links.append(selected[key])
                edge_ids = [link["id"] for link in links]
                digest_kind = "lineage-field" if entity_kind == "field" else kind
                digest = hashlib.sha256(
                    json.dumps([digest_kind, nodes, edge_ids], separators=(",", ":")).encode()
                ).hexdigest()
                tentative = any(link["status"] == "tentative" for link in links)
                partial = graph["completeness"] == "partial" or any(
                    link.get("limitations") for link in links
                )
                unverified = kind == "join" and all(
                    _unverified_cross_source(link, objects) for link in links
                )
                warnings = ["CROSS_SOURCE_CANDIDATES_UNVERIFIED"] if unverified else []
                item = {
                    "path_id": digest,
                    "kind": kind,
                    "entity_kind": entity_kind,
                    "field_ids" if entity_kind == "field" else "object_ids": nodes,
                    "edge_ids": edge_ids,
                    "evidence_ids": sorted(
                        {evidence for link in links for evidence in link.get("evidence_ids", [])}
                    ),
                    "status": "tentative"
                    if tentative
                    else "partial"
                    if partial
                    else "defined"
                    if kind == "lineage" and any(link["status"] == "defined" for link in links)
                    else "observed"
                    if kind == "lineage"
                    or any(link["status"] == "observed" for link in links)
                    else "declared",
                    "snapshot_digest": graph["snapshot_digest"],
                    "limitations": sorted(
                        {limit for link in links for limit in link.get("limitations", [])}
                    ),
                    **({"warnings": warnings} if warnings else {}),
                }
                found.append(item)
                if len(found) >= max_paths:
                    break
        except nx.NetworkXNoPath:
            pass
    # Both ends in one source: a path that leaves it is the roundabout one, so a same-source
    # path wins whenever there is one (DEC-4).
    if kind == "join" and found:
        home = objects[request.from_id]["source_id"]
        if objects[request.to_id]["source_id"] == home:
            inside = [
                item
                for item in found
                if all(objects[node]["source_id"] == home for node in item["object_ids"])
            ]
            found = inside or found
    with closing(_cache(directory)) as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO paths VALUES (?,?)",
            [(item["path_id"], json.dumps(item, sort_keys=True)) for item in found],
        )
        connection.commit()
    return {
        "ir_version": graph["ir_version"],
        "snapshot_digest": graph["snapshot_digest"],
        "completeness": graph["completeness"],
        "kind": kind,
        "status": "found" if found else "unavailable",
        "paths": found,
        "diagnostics": graph["diagnostics"],
    }
