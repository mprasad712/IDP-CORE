# IDP Database Audit — table & column gap analysis

Date: 2026-07-08 (branch `backend-aditya`)
Scope: all 19 `idp_*` tables defined in
`agentcore/src/backend/base/agentcore/services/database/models/idp/{config,documents,match}.py`.

Method: for every table, all write sites and read sites in the backend were located and the
columns actually populated at each write site were compared against the schema. Root cause of
each gap is noted. Nothing here has been changed yet — this is the fix list.

Legend: ✅ working as designed · ⚠️ partial / column gaps · 🟡 write-only (never read) · ❌ dead

---

## Summary table

| Table | Status | Written by | Read by |
|---|---|---|---|
| `idp_documents` | ⚠️ | upload API, trigger service (mail/SharePoint/OneDrive), splitting | everywhere |
| `idp_processing_jobs` | ⚠️ | pipeline, graph_native | status API, reports, metrics |
| `idp_extracted_headers` | ⚠️ | extraction.save_extraction_results, HITL PATCH | detail API, rules, reports |
| `idp_extracted_line_items` | ⚠️ | same + row add/delete API | same |
| `idp_document_classifications` | ✅ | classification.classify_and_persist (classifier node only) | reports.py |
| `idp_detected_elements` | ✅ | visual_detection (detection node only) | doc detail API, `visual_element` rules |
| `idp_document_sections` | 🟡 | long_doc.persist_sections (long docs only) | **nobody** |
| `idp_entity_links` | 🟡 | entity_linking.link_entities (multi-page docs only) | **nobody** |
| `idp_field_configurations` | ⚠️ | field-configs API, catalogue seeding, classifier auto-clone | config API, extraction |
| `idp_field_config_headers` | ⚠️ | same | same |
| `idp_field_config_line_items` | ⚠️ | same | same |
| `idp_agents` | ⚠️ | `sync_idp_agent_from_graph` on every agent save | pipeline config resolution |
| `idp_agent_rules` | ⚠️ | canvas Rules/Conditions sync | pipeline (only when no rules node!) |
| `idp_review_sessions` | ⚠️ | POST /review, POST /approve | reports.py |
| `idp_bulk_processing_batches` | ❌ | **nobody** | **nobody** |
| `idp_document_batches` | ❌ | **nobody** | **nobody** |
| `idp_match_results` | ✅ | matching_service | matching API |
| `idp_match_discrepancies` | ✅ | matching_service | matching API |
| `idp_agent_match_configs` | ✅ | matching API | matching_service |

---

## 1. Dead tables (never written, never read)

### `idp_bulk_processing_batches` and `idp_document_batches`
- Only appearances in code: model definition, `__init__.py` exports, and the original
  `ef20f6472bed_add_idp_tables.py` migration. **Zero constructor calls, zero queries.**
- Designed for a batch-ingestion feature (auto/manual trigger, progress counters) that was
  never built; connector ingestion (trigger service) inserts `idp_documents` rows directly
  instead, one at a time.
- Related dead column: `idp_agents.auto_stop_after_batch` — read by nothing.
- **Fix options:** drop both tables (+ the `auto_stop_after_batch` column) in a migration, or
  keep and build batching on top later. Nothing depends on them either way.

## 2. Write-only tables (populated but no consumer)

### `idp_document_sections` (long_doc.py)
- Only written when a document is *long* (> 8 pages or > ~12k est. tokens) — short docs never
  get rows. `parent_section_id` is **always NULL** (flat v1, hierarchy deferred).
- **No API or service ever reads it back.** It was persisted "for traceability" but the review
  UI has no sections endpoint.
- **Fix:** expose it in the document-detail API (or drop the persist step).

### `idp_entity_links` (entity_linking.py)
- Only written when an extracted header value is found on ≥ 2 distinct pages; single-page docs
  (the common case) never produce rows.
- `entity_type` actually stores the **header field name**, not an entity category — naming debt.
- **No reader anywhere.** The intended "value appears on pages 1 and 3" review-UI hint was never
  wired up.
- **Fix:** expose in doc detail API + surface in review UI, or drop the feature.

## 3. `idp_agents` — the 592-rows-all-default problem

Root cause: the table predates the canvas-first design. The row is now upserted by
`sync_idp_agent_from_graph()` (agent_config.py) on every agent save, and that function only
writes `agent_id`, `extraction_mode`, `default_rule_action='pending_review'`, `is_active`,
`extra`, audit columns. At runtime `resolve_pipeline_config()` reads the **graph** first and
uses the columns only as fallbacks — so the columns never get real data:

| Column | Observed | Why |
|---|---|---|
| `extraction_mode` | always `dynamic_prompting` | `_idp_extraction_mode()` maps the extractor node's `extraction_mode` field, but current node templates (incl. universal pipeline) don't have that field → default branch. The *actual* runtime mode is resolved per-run from the graph (`config_name` promotes to named_config; classifier can switch it mid-run) and is recorded in `idp_processing_jobs.extraction_mode_used`, **never written back here** → column is misleading. |
| `field_config_id` | always NULL | Canvas sync never sets it. Config is resolved per-run from the extractor node's `config_name`/`config_names` or the classifier's auto-select. |
| `dynamic_prompt` | always NULL | Prompt lives on the extractor node (`_field(extractor,"prompt")`); column is only a fallback. |
| `preprocessing_steps` | always NULL | Never written anywhere. Preprocessing is now the canvas nodes themselves (Scan Corrector, Page Selector, ...). Dead column. |
| `multi_doc_split` | always `false` | **Feature gap, not just a data gap** — see below. |
| `default_rule_action` | always `pending_review` | Only set at row creation; Approval Gate / rules nodes control the real action via `cfg.approve_value`. |
| `auto_stop_after_batch` | always `true` (default) | Read by nothing (batch feature dead). |

A full CRUD API exists at `api/idp/idp_agents.py` (`/idp-agents`) that can populate all of
these columns — but **the frontend never calls it** (zero references to `idp-agents` in
`src/frontend`). It's an orphaned API from the pre-canvas design.

### ⚠️ `multi_doc_split` is an unreachable feature
`pipeline.py:608` gates splitting on `cfg.multi_doc_split`, which comes **only** from
`idp_agents.multi_doc_split` (agent_config.py:635). There is no splitter canvas node, no
graph field, and the only writer is the unused `/idp-agents` API. So `splitting.py` can never
run from the UI — the column must be flipped by hand in the DB or via a manual API call.
**Fix:** add a canvas toggle (extractor node field or a Document Splitter node) and read it in
`resolve_pipeline_config`, or expose the flag in agent settings UI.

**Overall fix direction for `idp_agents`:** either (a) make `sync_idp_agent_from_graph` write
back the resolved values (mode, prompt, config id) so the table reflects reality, or (b) slim
the table down to what's actually used (`agent_id`, `default_rule_action`, `multi_doc_split`,
`is_active`, `deleted_at`, `extra`) and drop the vestigial columns.

## 4. `idp_agent_rules` — schema far richer than what's written

Two disjoint code paths:

1. **Canvas sync** (`_sync_canvas_rules`, agent_config.py:684-741) — the only real writer.
   Deletes all rows and re-inserts from the Rules/Conditions node, but writes a *degenerate*
   shape: `rule_group` always `1`, `action` always `'auto_approve'`, `condition_type` only
   `confidence_overall` or `field_value_numeric`. Columns `pattern`, `field_b`, `extra` →
   **always NULL**.
2. **Runtime evaluation** (pipeline.py:426-486) — when a Rules node is on the canvas, the
   pipeline builds richer `IdpAgentRule` objects **in memory** (supports `field_presence`,
   `visual_element`, `field_value_text/date`, OR-groups via per-rule groups) and *ignores the
   persisted rows entirely*. Persisted rows are only consulted when there is **no** rules node.

So the DB rows are a lossy, drifted copy of the canvas — anyone reading the table (e.g. the
rules CRUD API `api/idp/rules.py`, which supports all 9 condition types + `field_b`/`pattern`)
sees less than what actually executes. The rules CRUD API itself is **never called by the
frontend**.

**Fix:** make `_sync_canvas_rules` write the same mapping the pipeline evaluates (same
condition-type inference, OR → per-rule groups, action from Approval Gate) so the table matches
runtime; or stop persisting and treat the canvas as the only source.

## 5. `idp_documents` — column-level gaps

| Column | Gap |
|---|---|
| `page_count` | NULL at creation for uploads/connector docs; only backfilled at pipeline.py:1302 when processing reaches OCR. Docs that fail early keep NULL. Split children get it at creation. |
| `checksum` | Set for uploads and all trigger-service ingests (sha256), **explicitly NULL for split children** (splitting.py:186). |
| `mime_type` | Set for uploads and mail attachments; **NULL for SharePoint/OneDrive ingests** (trigger service passes `mime_type=None` at service.py:2491, 2701). |
| `predicted_type` | Only when a Document Classifier node is on the canvas. |
| `route_label` | Only when a Multi-Branch Router node is on the canvas. |
| `run_env` / `run_version` | Set at upload (documents.py:219) + copied to split children; rows created before the H4 migration are NULL (NULL = dev draft semantics — intended). Trigger-service ingests: verify they set it (they predate H4). |
| `extra` | Used only for the `review_draft` marker. |

## 6. `idp_processing_jobs` — column-level gaps

| Column | Gap |
|---|---|
| `retry_count` | **Always 0.** Nothing increments it for IDP jobs (the only `retry_count` writers are observability provisioning jobs). Dead until a retry feature exists. |
| `extra` | Never written. Dead. |
| `math_reconcile_attempts` | Only set when a Math Reconciliation node is enabled (pipeline.py:821); otherwise 0 — fine. |
| `extraction_mode_used`, `steps_completed`, `log`, timings, `error_message` | Properly populated by pipeline + graph_native. |

## 7. `idp_extracted_headers` / `idp_extracted_line_items`

Healthy overall (values, OCR-evidence confidence, `reasoning_trace`, `source_location`,
`reviewed_value`/`is_reviewed` all written). Gaps:
- `extra` column on both tables: **never written**. Dead.
- `source_location` is NULL whenever the value is NULL or OCR evidence isn't found, and
  `confidence_score` is NULL (not 0) for manually added rows (processed_docs.py:580) — readers
  must handle both; not a bug, just note it.

## 8. Field configuration tables

- Populated from three writers: Field Configurations page API, `DEFAULT_TEMPLATES` seeding
  (catalogue.py — includes per-field `prompt`), and the classifier's **auto-clone** of a global
  template into an org.
- **Bug-level gap: the classifier auto-clone drops `prompt`** on both headers
  (classification.py:267-276) and line items (:281-290) — a cloned config silently loses the
  per-field extraction prompts the template shipped with. It also drops `doc_type` and
  `visibility` (clone gets defaults). Fix: copy `prompt=h.prompt` / `li.prompt`,
  `doc_type=global_template.doc_type` in the clone.
- `doc_type` on org configs created through the API is optional and often NULL (only the
  template-clone path at field_configs.py:688 fills it reliably).

## 9. `idp_review_sessions`

- Written on both POST /review and POST /approve (good audit trail).
- `review_started_at` is **not the actual review start** — it's approximated as
  `doc.processing_completed_at or doc.updated_at` (processed_docs.py:674, 713), so
  "review duration" computed from these rows is fiction. `extra` never written.

## 10. Match tables (`idp_match_results`, `idp_match_discrepancies`, `idp_agent_match_configs`)

- All three are properly written/read by `matching_service.py` + `api/idp/matching.py`.
  `gr_value`/`gr_document_number` NULL for 2-way matches (by design). No gaps found.

---

## Prioritized fix list

1. **Classifier auto-clone drops per-field `prompt`** (classification.py) — silent quality
   regression for auto-selected configs. Small fix, real impact.
2. **`multi_doc_split` unreachable** — decide: canvas toggle or remove the splitting path.
3. **`idp_agent_rules` drift** — persisted rules ≠ evaluated rules; align `_sync_canvas_rules`
   with the pipeline's mapping (groups, action, condition types, `pattern`/`field_b`).
4. **`idp_agents` vestigial columns** — write back resolved values or drop
   `preprocessing_steps`, `auto_stop_after_batch`, (probably) `dynamic_prompt`,
   `field_config_id`; make `extraction_mode` honest or remove it.
5. **Dead batch tables** — drop `idp_bulk_processing_batches` + `idp_document_batches` or
   schedule the feature.
6. **Write-only tables** — add read APIs for `idp_document_sections` and `idp_entity_links`
   (review-UI features they were built for) or stop writing them.
7. **`mime_type` NULL for SharePoint/OneDrive ingests** — derive from file extension in the
   trigger service.
8. **Orphaned APIs** — `/idp-agents` CRUD and `/idp-agents/{id}/rules` CRUD are not called by
   the frontend; either wire the UI to them or delete to reduce surface area.
9. Minor: `idp_review_sessions.review_started_at` semantics; `extra` columns on
   jobs/headers/line-items/review-sessions never used; `retry_count` never incremented.
