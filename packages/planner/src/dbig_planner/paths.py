"""Bounded graph paths with lineage/reference separation and stable IDs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from itertools import islice
from pathlib import Path

import networkx as nx
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


def find_paths(directory: Path, request: PathRequest, kind: str, config: PlannerConfig) -> dict:
    graph = load_graph(directory)
    objects = graph["objects"]
    if request.from_id not in objects or request.to_id not in objects:
        raise ValueError("UNKNOWN_OBJECT")
    if request.from_id == request.to_id:
        raise ValueError("SAME_OBJECT")
    if kind not in {"lineage", "join"}:
        raise ValueError("UNKNOWN_PATH_KIND")
    max_hops = min(request.max_hops or config.max_hops, config.max_hops)
    max_paths = min(request.max_paths or config.max_paths, config.max_paths)
    edges = graph["lineage"] if kind == "lineage" else graph["references"]
    network: nx.Graph = nx.DiGraph() if kind == "lineage" else nx.Graph()
    selected: dict[tuple[str, str], dict] = {}
    for edge in edges:
        if kind == "join" and edge["status"] == "rejected":
            continue
        left, right = edge["from_id"], edge["to_id"]
        if left not in objects or right not in objects:
            continue
        key = (left, right) if kind == "lineage" else tuple(sorted((left, right)))
        if key in selected and selected[key]["status"] == "declared":
            continue
        selected[key] = edge
        network.add_edge(left, right, weight=1 if edge["status"] in {"declared", "executed"} else 2)
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
                digest = hashlib.sha256(
                    json.dumps([kind, nodes, edge_ids], separators=(",", ":")).encode()
                ).hexdigest()
                tentative = any(link["status"] == "tentative" for link in links)
                partial = graph["completeness"] == "partial" or any(
                    link.get("limitations") for link in links
                )
                item = {
                    "path_id": digest,
                    "kind": kind,
                    "object_ids": nodes,
                    "edge_ids": edge_ids,
                    "evidence_ids": sorted(
                        {evidence for link in links for evidence in link.get("evidence_ids", [])}
                    ),
                    "status": "tentative"
                    if tentative
                    else "partial"
                    if partial
                    else "observed"
                    if kind == "lineage"
                    else "declared",
                    "snapshot_digest": graph["snapshot_digest"],
                    "limitations": sorted(
                        {limit for link in links for limit in link.get("limitations", [])}
                    ),
                }
                found.append(item)
                if len(found) >= max_paths:
                    break
        except nx.NetworkXNoPath:
            pass
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
