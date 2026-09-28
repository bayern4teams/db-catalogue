# Threat model

Scope: the v1 pipeline as implemented — source-local collectors, signed bundles, the assembler,
and the independent requester and planner. Written against the code on 2026-09-27, so it
describes what holds today, and says plainly where it does not.

Every mitigation below names the test that holds it. A mitigation with no test is listed as a
gap, not as a control. Live GPU, vLLM and Langfuse behaviour is deferred by user decision until
an accessible GPU host exists and is never claimed as tested here.

## Trust boundaries

1. **Source ↔ collector.** The collector holds the only database credentials. It runs at the
   source site and is the only component that connects to a database.
2. **Collector ↔ assembler.** A sealed, signed bundle on a filesystem or object store. The
   assembler has no database credentials and never opens a source connection.
3. **Assembler ↔ consumers.** A published snapshot. The requester and planner read it and never
   reach a source.
4. **Assembler ↔ reviewer model.** A private vLLM endpoint that receives redacted candidate
   context and holds no credentials, no database access and no authority.
5. **Operator ↔ HTTP surface.** A trusted private network. Loopback bind, bearer token.

## Assets

Database credentials and HMAC signing keys; raw source values; SQL text, comments and
descriptions; the integrity of the published graph; the availability of the sources being
scanned.

## Threats and controls

### T1 — A collector holds more privilege than it needs, or overloads a source

A read-only identity per source, separable metadata and profiling grants, per-source query
budgets, statement timeouts and bounded samples. Budget exhaustion is a diagnostic and a
`partial` bundle, never a silent truncation.

*Tests:* `test_mongo_count_respects_budget_and_reports_driver_failure`,
`test_mongo_sample_byte_budget_stops_without_publishing_values`,
`test_postgresql_16_catalog_and_read_only_role` (CI containers, least-privilege role).

*Gap:* ClickHouse `readonly`, memory and read-row limits are specified but not asserted by a
test against a live server.

### T2 — A bundle is tampered with, truncated or swapped in transit

Each file carries a SHA-256 digest and a record count in the manifest; the manifest is signed
with HMAC-SHA256 under a per-run key referenced by environment variable, never written into any
artifact. The reader refuses a digest mismatch, a size mismatch, a record-count disagreement, a
duplicate ID, an unsafe path, an unknown major version and a missing file. Publication is atomic:
the manifest is renamed last, so a consumer never reads a partial directory.

*Tests:* `test_signed_bundle_roundtrip_and_tampering`, `test_validate_bundle_refuses_a_wrong_signing_key`,
`test_validate_bundle_refuses_an_unsigned_artifact`, `test_a_tampered_golden_row_is_refused`,
`test_the_manifest_digests_still_match_the_committed_files`.

### T3 — A scan request is forged, replayed or exceeds its authorization

A scan request is signed; the collector verifies the signature, checks the requester against
`authorized_requesters`, checks each scope against the allow-list, and refuses a second request
that reuses an idempotency key with different content (`REPLAY_CONFLICT`). An unsigned request
is refused outright.

*Tests:* `test_scheduler_bundle_authorization_replay_and_local_queue`,
`test_accept_scan_refuses_an_unsigned_request`,
`test_accept_scan_refuses_a_request_signed_with_the_wrong_key`,
`test_collector_http_auth_and_signature`, `test_private_http_dispatch_uses_signed_body`.

### T4 — A secret leaks into an artifact, a log, an error or a code graph

Every secret is resolved from an environment variable *reference* held in validated
configuration, through one resolver that fails closed on a missing, blank, unset or empty
variable, names the variable and purpose, and never includes the value in the message. No
credential, DSN or key is ever written to a bundle, a snapshot, a test snapshot or a developer
graph index.

*Tests:* the ten tests in `tests/test_security.py`, in particular
`test_a_refusal_never_contains_the_secret_value` and
`test_the_purpose_is_reported_so_the_operator_knows_which_secret`; plus
`test_collect_signed_bundle_contains_no_secret`, and the four API startup tests that refuse to
serve without their token.

*Gap:* a driver exception could still carry connection detail. `test_mongo_count_respects_budget_and_reports_driver_failure`
asserts a value-free diagnostic for one such path; the others are not individually covered.

### T5 — The reviewer model is prompted into inventing or escalating a claim

Source metadata, SQL text and comments are treated as untrusted data, never as instructions. The
model's response must satisfy a strict schema; every cited evidence ID is validated against the
snapshot and an invented one is rejected. A refusal, timeout, parse error or provider outage
fails closed to `uncertain`. The model can contribute only weak evidence and can never declare
lineage, and graph publication is deterministic and completes whether or not the model or its
telemetry is reachable.

*Tests:* `test_reviewer_validates_citations_and_only_adds_weak_evidence`,
`test_reviewer_rejects_invented_evidence_and_outage`,
`test_answers_validate_citations_and_outage`, `test_answer_telemetry_outage_does_not_hide_answer`.

*Deferred:* schema-constrained decoding behaviour on a real pinned vLLM build, and Langfuse
masking, are untested until a GPU host exists.

### T6 — Unsafe or unresolvable SQL produces a wrong or dangerous claim

SQL is parsed, never executed. Unresolvable or ambiguous references abstain with a diagnostic
rather than guessing a column. A `SELECT` yields read usage and possible join evidence, never
write lineage. Rendered plans quote catalogue identifiers and refuse Mongo operator injection.
The planner never executes anything it renders.

*Tests:* `test_sql_parser_rejects_unsafe_and_unmapped`,
`test_sql_lineage_and_join_paths_are_separate`,
`test_catalog_identifiers_are_quoted_and_mongo_operators_refused`,
`test_clickhouse_materialized_view_target_abstains_without_false_read`,
`test_openlineage_job_event_paths_are_defined_not_observed`,
`test_join_paths_and_lineage_paths_are_different_questions`.

### T7 — Raw values or low-entropy tokens disclose data

Metadata-only is the default; sampling, query history and cross-source token comparison are
opt-in per source. Samples contribute type unions, presence flags and bounded counts, never
values. Cross-source comparison uses HMAC tokens under a per-run key, held only in assembler
memory inside one run boundary and never published as durable graph fields. View definitions and
ClickHouse key expressions are published as SHA-256 digests, not text.

Cross-source comparison is implemented as of 2026-09-27 and is off unless sampling *and* a
run-key reference are both configured. Tokens are `HMAC-SHA256` under a per-run key, ride in their
own bundle file so a deployment can decline to transport them, and never cross into a snapshot:
only aggregate containment metrics do.

*Tests:* `test_mongo_metadata_only_never_samples`, `test_mongo_validator_and_type_only_sample`,
`test_postgres_view_definition_is_hashed_without_literals`, `test_no_raw_value_reaches_the_bundle`,
`test_tokens_never_reach_the_snapshot`,
`test_the_same_value_under_a_different_key_gives_a_different_token`,
`test_no_tokens_without_sampling`, `test_no_tokens_without_a_key`,
`test_values_with_no_agreed_normalisation_are_skipped`.

*Residual risk, accepted:* a plain hash of a low-cardinality value is guessable, which is why
tokens are keyed; replacing the HMAC with a bare digest is caught by
`test_a_token_is_the_keyed_digest_of_the_normalised_text`. Python cannot guarantee memory
zeroization, so deployment isolation and short retention remain part of the control.

### T8 — A rename or a partial scan corrupts identity or deletes real objects

IDs derive from server-assigned identifiers, so a rename keeps identity. An object with no
native identifier is dropped with an `IDENTITY_UNAVAILABLE` diagnostic rather than given a
name-derived ID that a later rename would silently reassign. Deletion is emitted only after a
complete authoritative scan of the same scope; a partial or permission-denied scan leaves prior
entities in place. The manifest counts diagnostics by code, so a drop cannot pass unnoticed.

*Tests:* `test_postgresql_object_and_field_ids_survive_a_rename`,
`test_clickhouse_object_id_survives_a_rename`, `test_mongodb_collection_id_survives_a_rename`
(all three in CI containers), `test_clickhouse_drops_a_table_with_no_uuid_and_says_so`,
`test_mongo_drops_a_collection_with_no_uuid_and_says_so`,
`test_an_unidentifiable_object_never_reaches_a_published_bundle`,
`test_a_manifest_that_under_reports_dropped_objects_is_refused`.

### T9 — The HTTP surface is exposed beyond the trusted network

Per the 2026-09-27 decision the adapters target a private network. All three `serve` commands
refuse a non-loopback bind address and refuse a hostname rather than resolving it, since a name
could resolve off-host. Each service refuses to start without its bearer-token reference, and
enforces a configured request body limit.

*Tests:* `test_serve_refuses_a_non_loopback_bind` (three commands × three addresses),
`test_serve_refuses_a_hostname_rather_than_resolving_it`,
`test_authenticated_requester_api_and_payload_limit`, `test_planner_api_auth_and_cli`,
`test_collector_http_requires_secret_at_startup_and_enforces_body_limit`.

*Explicitly out of scope:* rate limiting per caller, authorization scopes, audit logging and
TLS termination. Public exposure is unsupported, not partially hardened.

## Known gaps

1. ClickHouse server-side read limits are specified but not asserted against a live server.
2. Driver exceptions are only spot-checked for credential leakage.
3. Live vLLM and Langfuse behaviour, including masking, is untested by user decision.
4. No test yet binds a real loopback port and serves an HTTP request end to end; the app and the
   bind refusal are tested separately.
5. The source of observed SQL and job metadata is still unchosen, so executed lineage is
   unavailable and its privacy policy is not yet written.
