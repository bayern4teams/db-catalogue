# Step 3: the 40 questions with the model (2026-10-20)

**Run.** Owner yes 2026-10-20; the tunnel was up (`/v1/models` returned 200 and listed `Qwen3.8-Flash-Next`). The questions ran on a PostgreSQL + ClickHouse snapshot (`snapshot-on-nomongo`: the step 1 bundles, sampling on, probe off), as on 2026-10-18.

**Configuration** (`config/requester-q40.toml`): no `max_tokens`, timeout 900 s, `retrieval_limit` 10.

**Duration.** 40 questions, 51 minutes in total: median 59 s, maximum 291 s. Every exit code was 0 and stderr was empty.

**Evidence:**
- `step3-answers.json`: catalogue names only
- `step3-grades.json`
- `step3-context.json`: the request the app really builds, recorded with a stub model and no model call

## What reaches the model (no model call)
| Check | Before | After |
|---|---|---|
| Expected names in the model's context | 59 of 134 (44%) | **104 of 134 (78%)** |
| `max_tokens` in any request | not sent (F3) | not sent, in all 38 requests |
| `outcome` in the schema's `required` | no | **yes**, in all 38 |
| Relationships with source and namespace | none | **638 of 638**; 232 of them are cross-source |
| Largest prompt | n/a | 30,454 bytes (112 items); no context error |

## Grades
Graded by the lead against the owner-approved expected answers; the six unclear grades were decided by the owner (marked "owner" in `step3-grades.json`).

| | Right | Partial | Wrong | NOT_ESTABLISHED | NO_CATALOGUE_MATCH | error |
|---|---|---|---|---|---|---|
| 2026-10-18 | 17 | 13 | **6** | 2 | 2 | 0 |
| **2026-10-20** | **19** | 13 | **0** | 5 | 2 | 1 (`MODEL_INVALID_CITATION`) |

By category on 2026-10-20:
- **Direct:** 10 Right.
- **Steps:** 2 Right, 5 Partial, 2 NOT_ESTABLISHED, 1 error.
- **Comprehensive:** 3 Right, 5 Partial, 2 NO_CATALOGUE_MATCH.
- **Inference:** 4 Right, 3 Partial, 3 NOT_ESTABLISHED.

**Better (9):**
- N-S5: Wrong → Partial
- N-C6: Wrong → Right
- N-C10: Wrong → Right
- N-I1: Wrong → Partial
- N-I3: Wrong → Partial
- N-I10: Wrong → NOT_ESTABLISHED
- N-C2: NOT_ESTABLISHED → Partial
- N-C9: NOT_ESTABLISHED → Partial
- N-C8: Partial → Right

**Worse (6):**
- N-S2: Partial → `MODEL_INVALID_CITATION`
- N-S4: Partial → NOT_ESTABLISHED
- N-S8: Partial → NOT_ESTABLISHED
- N-I2: Partial → NOT_ESTABLISHED
- N-I5: Partial → NOT_ESTABLISHED
- N-I6: Right → Partial

## What the checks showed
- **F11 works.** No answer writes a PostgreSQL-ClickHouse join as one query. Every cross-source candidate the model mentions, it calls not joinable in one query: N-S3, N-S7, N-C8, N-I1 and N-I3. The six Wrong answers of 2026-10-18 were all cross-source joins or false lists, and none recurred.
- **F12 works.** In 0 answers does the text say "not established" while the code says answered; there were 4 on 2026-10-18.
- **F13 works on retrieval** (78% of expected names reach the model). The two type questions, N-C4 and N-C7, still find nothing, as expected with option C deferred.
- **F14 reaches the model.** N-C8 and N-I10 repeat "value overlap 0% of 4042 sampled child values" for `PersonContact -> Person`, where the real overlap is 25% (1,082,567 of 4,320,757). N-S3 and N-C8 repeat "1% of 2702" for `Experience -> Company`, where it is 25%.

## New findings (owner decides; nothing changed)
- **F16, multi-hop paths missing from the context.** N-S4, N-S8 and N-I10 have a valid path through a table the search did not retrieve:
  - N-S4 and N-I10: through `ch.Person` or `ch.Experience`;
  - N-S8: `companyindustry -> industryclassification`.

  The model is given only the relationships of the retrieved objects, so it honestly answers "not established". On 2026-10-18, N-S4 and N-S8 were Partial.
- **F17, bare verdicts.** N-I2 (empty answer text) and N-I5 (only "not_established") give the right verdict with no explanation, although the prompt asks the model to say what it checked. On 2026-10-18 both were Partial.
- **N-S2, `MODEL_INVALID_CITATION`:** the model cited an ID the app does not allow. Which ID cannot be told without a recorded reply; that needs another model call, and so the owner's yes.
