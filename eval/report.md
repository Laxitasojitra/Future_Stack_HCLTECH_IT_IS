# Evaluation report

Golden set: 47 questions · as-of date 2026-10-24 · LLM: offline-rules · embeddings: hashing-1024 · hybrid search (ChromaDB + BM25, RRF)

| Metric | Result |
|---|---|
| Overall pass rate | 100% (47/47) |
| Answer accuracy (type + required facts + no forbidden facts) | 100% (47/47) |
| Citation accuracy | 100% (44/44) |
| Tool accuracy | 100% (26/26) |
| Retrieval hit rate (expected clause cited) | 100% (35/35) |
| Privacy compliance | 100% (5/5) |
| Safety compliance | 100% (12/12) |
| Action success rate | 100% (2/2) |
| Latency p50 / p95 | 8 ms / 16 ms |
| Live ingestion checks | 100% (4/4) |

## By category

| Category | Passed |
|---|---|
| action | 2/2 |
| billing | 3/3 |
| care_plan | 1/1 |
| clarification | 1/1 |
| conflict | 1/1 |
| eligibility | 5/5 |
| injection | 2/2 |
| lab_result | 3/3 |
| live_ingestion | 2/2 |
| medication | 2/2 |
| multi_step | 4/4 |
| not_found | 3/3 |
| personal_data | 1/1 |
| policy_fact | 6/6 |
| privacy | 3/3 |
| procedure | 3/3 |
| safety | 2/2 |
| version_resolution | 3/3 |

## Live ingestion (R11)

| Check | Result | Detail |
|---|---|---|
| Reject document with missing metadata | pass | Missing required metadata: doc_id, title, policy_key, doc_type, authority_level, version, effective_date, status. |
| Rejected document stays unsearchable | pass | - Visits to Intensive Care Units shall be limited to one designated visitor per patient per day. [GOV-ADV-2026-14 §3.1,  |
| New version searchable without restart (7 AM-10 PM -> 6 AM-11 PM) | pass | ingested SOP-RAD-001 v1.3 |
| Only administrators can upload | pass | Only authorised administrators can upload knowledge-base documents. |

## Failures

None.
