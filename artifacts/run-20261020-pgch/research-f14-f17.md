# Research and recommendations: F14-F17 (2026-10-20)

Four research subagents (web, papers, public repos; no database or model calls). The lead checked each code claim against the source on `verify/live-20261020`. Licences were read from each repo's LICENSE through `gh api`. Items marked *unverified* are as the agents reported them. Nothing is implemented; every fix waits for the owner.

## F14: a lower bound is shown as a measurement
**Cause** (`builder.py:296-395`, read by the lead):
- The capped-set case already uses the standard coordinated bottom-k trick: both sides are compared only below the smaller capped side's largest hash (DEC-19).
- What remains is the **row sample**. A ClickHouse parent read as 25,000 of up to 11.8 M rows holds only part of its values, and no hash range can fix that. The number is then a lower bound, yet the evidence sentence prints it bare ("1%", while the real overlap is 25%).

**What others do:**
- **Apache DataSketches** (Apache-2.0): `BoundsOnRatiosInThetaSketchedSets` returns {lower, estimate, upper} at about 95% for B⊆A, from binomial bounds. `JaccardSimilarity` does the same. The idea and formula can be reused.
- **Cohen & Kaplan**: bottom-k and coordinated samples ([arXiv 1206.5637](https://arxiv.org/abs/1206.5637)). Idea only.
- **Joshi 2025**: binomial containment from plain row samples ([arXiv 2507.10019](https://arxiv.org/abs/2507.10019)). A preprint; idea only.
- **whylogs** (Apache-2.0) shows "estimate + lower/upper bound" in its UI.
- **LSH Ensemble** (datasketch, MIT), **Lazo/Auctus** (Apache-2.0), **Aurum** (MIT) and **BINDER/FAIDA** (Metanome, Apache-2.0) all treat a sample as a filter for candidates, never as a measured value.
- **JOSIE** has no licence, so its code cannot be reused.

**Recommendation:**
1. Fix the wording first, using the flags that already exist:
   - parent read whole: "≈X% (95% CI a-b%)";
   - row-sampled parent: "at least X% (parent only partly read; true overlap may be much higher)".
2. Pass `lower`, `estimate`, `upper` and `basis` in the metrics, so the model sees the same qualifier as the reader.
3. *Owner choice:* a coverage-corrected estimate, `s/(n·f)` with `f` = parent distinct values read / parent distinct total. This needs the parent's distinct total (one extra `uniq` query on ClickHouse) and holds only for unique parents.
4. Upgrade path: sample by value hash at the source (F5 option C), which makes the parent whole within the hash range.

## F15: an unmeasured key outranks a measured parent
**Cause** (`builder.py:706-716`, checked):
- `rank()` sorts on `(-name_score, other_source, claim, id)`, where `claim` is 0 for a declared key, 1 for a weak key and 2 for measured-unique.
- `_overlap` runs only after the sort, so containment never affects the order. A weak, partly read ClickHouse key therefore beats a parent with 100% measured containment.
- **Not seen without MongoDB:** in the PG+CH re-run, step 2 cuts 0 owner-true pairs. Both live cases were MongoDB children. The defect is still in the code.

**What others do:**
- **Auto-BI** (VLDB 2023): scores only pairs that pass a containment threshold; containment is its top feature. No code released.
- **HoPF** (2020; repo has no licence): exact inclusion as a gate, then name and distribution scores.
- **Rostin 2009**: classifies only exact inclusion dependencies.
- **Microsoft Fabric SemPy** `find_relationships` (proprietary): coverage threshold 1.0 decides; name similarity is a pre-filter.
- **Power BI and Tableau**: declared keys and names; Tableau labels unmeasured pairs "some records match".
- No system ranks an unmeasured candidate above a measured one.

**Recommendation:**
1. Compute `_overlap` before the sort.
2. Rank `(-name_score, evidence_tier, -containment, other_source, claim, id)`, where the tier is measured, then lower bound only, then unmeasured.
3. Add one regression test with the F15 shape (top_k 1).

*Owner choices:*
- Whether declared keys stay first.
- Whether the tier goes before the name score (the Auto-BI and SemPy approach; it reorders more pairs).

Skip the calibrated classifier until labelled pairs exist.

## F16: multi-hop paths are missing from the context
**Cause** (`search.py:458`, checked): `relationships_for` returns only edges that touch a retrieved object. An edge between two unretrieved tables, such as `companyindustry -> industryclassification`, never reaches the model.

**What others do:**
- **GRAST-SQL** ([repo](https://github.com/thanhdath/grast-sql); no GitHub licence, so idea only): after top-k retrieval, `networkx` `steiner_tree` adds the bridge tables.
- **Graph-Link** (ICML 2026) names this exact failure: "semantically thin" bridge tables are dropped. Its fix is a Steiner subgraph.
- **SchemaGraphSQL** and **SteinerSQL** take the same approach. No code.
- **dbt MetricFlow** (Apache-2.0) caps join paths at 2 hops (`MAX_JOIN_HOPS`), with fan-out rules.
- **Cube** (Apache-2.0) runs Dijkstra between referenced cubes.
- **Snowflake semantic views** label bridge (junction) tables explicitly.
- CHESS, DIN-SQL, RESDSQL, CodeS, DAIL-SQL, Vanna, Dataherald and LlamaIndex: none does join-path completion (*unverified*).

**Recommendation:**
1. After retrieval, run a stdlib BFS over the requester's own `relationships` table between the retrieved tables. Read the hop limit (2, as MetricFlow) and a bridge-table cap from validated config.
2. Add each bridge table as "path-only", carrying its key columns and its relationship lines.
3. A path that rests on candidate edges stays tentative.
4. Add one test: the companyindustry 2-hop fixture.

*Owner choice:* inline BFS in the requester (no new dependency; my pick), or call the planner's `find_paths`, which already uses `networkx` `shortest_simple_paths` with `max_hops` 4, as a separate service (§17 artifacts).

## F17: bare verdicts
**Cause** (`answer.py:30`, checked):
- The `Answer` fields are ordered `answer`, `citations`, `outcome`. The text already comes first, so reordering alone will not help.
- B1 forbids `minLength`/`minItems` in the schema (`tests/catalogue_fixes/test_b1_schema.py`), because llama.cpp failed to parse the grammar with `maxLength 2000`.

**What others do:**
- **Pydantic AI** (MIT): an output validator raises `ModelRetry`.
- **Instructor** (MIT): a field validator plus `max_retries`.
- **Guardrails** (Apache-2.0) and **DSPy** `Refine` (MIT) re-ask the model.
- **xgrammar** (Apache-2.0, vLLM's default backend) enforces `minLength` and `minItems` while decoding. It ignores length bounds when they are combined with `pattern` or `format`.
- **Qwen3 thinking in vLLM:** the grammar applies only after the thinking ends. That may be where the explanation goes; checking it needs a live trace (*unverified*).

**Recommendation (no new dependency):**
1. Add `checked: list[str]` before `answer`, and say in the prompt to fill it first.
2. Validate in the app: reject an empty `checked`, an empty `answer`, or an answer that is only the outcome word. Return a new code, `MODEL_ANSWER_UNEXPLAINED`.
3. Allow one retry with a fixed correction message containing no data. Put the retry count in config.
4. Add tests with the existing `ModelStub`.

*Owner choice:* small schema minimums (`minItems 1`, `minLength 1`). They conflict with B1 and the llama.cpp server now in use.

## Process note
While checking a licence, the F15 agent ran `pip download` without asking. It saved one wheel (`semantic_link_sempy-0.14.2`) from PyPI into the scratchpad, then deleted it unopened; no repo files changed. This broke the rule that downloads need your yes.

## F17 follow-up: llama.cpp (the server actually in use), Sonnet agent, web only
- llama-server: per-request `chat_template_kwargs: {"enable_thinking": false}`; `--reasoning-format` (`auto` default, `deepseek` puts thoughts in `reasoning_content`); `--reasoning-budget` ([README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)).
- `response_format` json_schema was not enforced with thinking on for Qwen3.5/Qwen3-VL until a fix of 2026-03-10 ([#20345](https://github.com/ggml-org/llama.cpp/issues/20345), commit 62b8143); since then the grammar applies to the content after thinking (*the agent's inference from the issue*). So a bare answer is not thinking cut short.
- Schema keywords supported: `minLength`, `maxLength`, `minItems`, `maxItems`, `pattern` ([grammars README](https://github.com/ggml-org/llama.cpp/blob/master/grammars/README.md)). `MAX_REPETITION_THRESHOLD` is 2000 in `llama-grammar.cpp`: the B1 failure at `maxLength 2000` sits on that limit (*likely, unverified*); large `maxLength` fails to parse ([#25923](https://github.com/ggml-org/llama.cpp/issues/25923)). `minLength 1` / `minItems 1` are far below it (*untested*).
- A plain string accepts `""` and `"not_established"`; that is expected, so app validation is needed whatever the schema says.
- Property order: probably schema order with required fields first (*from memory, unverified*); all fields required with no default keeps it fixed. One test request will show it.
- Verdict: the planned fix (`checked` before `answer`, app check, one retry) should work on llama.cpp. Optional: thinking off per request (`enable_thinking: false`) would make `checked` the reasoning and cut latency; needs the server build to include the #20345 fix. Owner choice.

## F11-b: false candidates across unrelated sources, solved generally (2 Sonnet agents, web only)
**What the app does today** (checked by the lead):
- **Sampled overlap only:** value tokens come from 25,000 sampled rows (PG 106/124 columns, CH 38/46). A zero drops a link only against a fully read parent.
- **Column profiles:** PG from `pg_stats`; CH width only (the probe adds distinct and uniqueness from a sample).
- **Tables:** row-count estimates only.
- **Not collected:** min/max, length or format profiles, full-column sketches.

**What others do:**
- **Rostin 2009:** out-of-range feature (min/max).
- **Zhang 2010:** distribution similarity (EMD).
- **NextiaJD** ([dtim-upc/NextiaJD](https://github.com/dtim-upc/NextiaJD), Apache-2.0, Spark): join quality predicted from profiles (cardinality, entropy, length, character classes).
- **Valentine** ([delftdata/valentine](https://github.com/delftdata/valentine), Apache-2.0): schema-matching suite; good as an offline benchmark harness.
- **Metanome** (Apache-2.0, Java): FAIDA, approximate inclusion from hashed samples.
- **Aurum** (MIT).
- **D3L** (format "regex" signatures; no licence).
- **Auctus** (Apache-2.0): range and type pruning.
- **Auto-BI:** one best parent per child, with a globally consistent graph.
- **Power BI:** a cross-source relationship is "limited", never trusted automatically.
- **Profilers** (ydata, DataProfiler, OpenMetadata, DataHub, Great Expectations): min/max, distinct, length and patterns through SQL aggregates, so they are cheap.
- **Embedding matchers** (Starmie, DeepJoin, WarpGate): need values sent to a model, which conflicts with the privacy rule; not used.

**Options:**
- A tiers plus an owner decisions file;
- B a conclusive zero by expected overlap;
- C value-free length, character-class and format profiles (numeric min/max local only);
- D coordinated `md5` hash sampling in the database;
- E `allowed_source_pairs`.

Owner 2026-10-20: A + B + E now (`c_min` 0.25, `min_expected_shared` 5), C next, D later.

**Benchmark data on disk** (`/home/bayern/Projects/benchmark/datasets`):
- Pagila and Chinook (declared FKs).
- TPC-H SF1 (8 FKs).
- GLEIF golden copy: 3,458,719 records (counted by the lead). 164,048 name RA000189 (INSEE, France SIRENE) and 63,396 name RA000472 (Brønnøysund, Norway Enhetsregisteret), linked through `Entity.RegistrationAuthority.RegistrationAuthorityEntityID`.
- Negatives: raha sets, nyc-taxi, ontime, uk-price-paid, openaddresses-lu.
- Licences in the MANIFEST: GLEIF CC0, brreg NLOD 2.0, Sirene Licence Ouverte 2.0.
