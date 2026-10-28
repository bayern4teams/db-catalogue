# Prints the catalogue output (names, types, counts; no values) of snapshot-both as Markdown.
import json, collections
R = "artifacts/run-20261028-remote/snapshot-both"
nodes = {n["id"]: n for n in map(json.loads, open(f"{R}/nodes.jsonl"))}
objs = [n for n in nodes.values() if n["id"].startswith("object:")]
fields = collections.defaultdict(list)
for n in nodes.values():
    if n["id"].startswith("field:"):
        fields[n.get("object_id") or n.get("parent_id")].append(n)
name = lambda i: f'{nodes[i].get("source_id")}.{nodes[i].get("name")}'
print("### Tables per source\n\n| Source | Table | Columns | Column names (type) |\n|---|---|---|---|")
for o in sorted(objs, key=lambda o: (o.get("source_id"), o.get("name"))):
    fs = sorted(fields.get(o["id"], []), key=lambda f: f.get("name"))
    cols = ", ".join(f'{f.get("name")} ({f.get("data_type") or f.get("native_type") or "?"})' for f in fs)
    print(f'| {o.get("source_id")} | {o.get("name")} | {len(fs)} | {cols} |')
print("\n### Join candidates (all `tentative`, awaiting human review)\n\n| Tier | From | To | Columns |\n|---|---|---|---|")
for e in sorted(map(json.loads, open(f"{R}/edges.jsonl")), key=lambda e: (e["evidence_tier"], name(e["from_id"]))):
    pairs = "; ".join(f'{nodes.get(a,{}).get("name")} = {nodes.get(b,{}).get("name")}' for a, b in e.get("field_pairs") or [])
    print(f'| {e["evidence_tier"]} | {name(e["from_id"])} | {name(e["to_id"])} | {pairs} |')
