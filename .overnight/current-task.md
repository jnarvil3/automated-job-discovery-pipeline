# Current Task Queue

## Priority 1: Profile & Data Fixes
(All completed — see completed.md)

## Priority 2: Core Improvements
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 1)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 2)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 3)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 4)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 5)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 6)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 7)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 8)
(All completed — see completed.md)

## Priority 3: Review Feedback (Cycle 9)

### Critical
- [ ] **C1: Fix thread-unsafe `requests.Session` in enricher.** In `core/enricher.py`, remove the module-level `_session = requests.Session()` (line 18-23). Instead, create a `_HEADERS` dict at module level. Update `fetch_full_description()` to accept a `session` parameter. In `_fetch_for_job()`, create a fresh `requests.Session()` per call with `session.headers.update(_HEADERS)` and pass it to `fetch_full_description()`. This ensures each thread in the ThreadPoolExecutor gets its own session.

- [ ] **C2: Fix German filter false positives on negated phrases.** In `core/enricher.py`, add a `NEGATION_RE` compiled pattern that matches phrases like "not required", "no german required", "german is not needed", "don't require german", "nicht erforderlich". In `requires_german()`, after a pattern match is found, extract ±80 chars of context and check if `NEGATION_RE` matches within that context. If it does, `continue` to the next pattern instead of returning True. Add 3 tests: `test_no_german_required_passes()`, `test_german_not_needed_passes()`, `test_dont_require_german_passes()`.

- [ ] **C3: Freeze Job.id at construction time.** In `core/models.py`, change `Job.id` from a `@property` that computes from `self.url` on every access to a value frozen at `__post_init__`. Add a `_frozen_id: str = field(init=False, repr=False, default="")` field. In `__post_init__`, set `self._frozen_id = hashlib.sha256(normalize_url(self.url).encode()).hexdigest()[:16]`. Change the `id` property to return `self._frozen_id`. This ensures the ID doesn't change when `job.url` is updated after a redirect in `enrich_jobs()`.

### High Priority
- [ ] **H1: Migrate Greenhouse and Lever ATS to `requests` library.** In `delivery/ats/greenhouse.py` and `delivery/ats/lever.py`, replace `urllib.request`/`urllib.parse`/`BytesIO` multipart construction with `requests.post()` using `data` and `files` parameters. Remove the manual `add_field`/`add_file` helper functions. Handle errors with `requests.exceptions.HTTPError` instead of `urllib.error.HTTPError`. Keep the same `ApplicationResult` return values.

- [ ] **H2: Add `commit` parameter to `log_application()`.** In `core/database.py:157`, add `commit: bool = True` parameter to `log_application()`. Only call `conn.commit()` when `commit=True`. In `delivery/apply_dispatcher.py`, pass `commit=False` to both `log_application()` calls (lines 330 and 344), then add a single `conn.commit()` after the apply loop completes.

- [ ] **H3: Skip cover letter generation when no apply method is viable.** In `delivery/apply_dispatcher.py`, before the `generate_cover_letter()` call at line 271, add a check: `has_api = methods.get("api") and job.ats_platform in ("greenhouse", "lever", "workable"); has_browser = methods.get("browser") and job.ats_platform; has_email = methods.get("email") and job.apply_email`. If none are true, set `job.status = "quick_apply"`, increment `skipped`, and `continue` without calling GPT.

- [ ] **H4: Add tests for `generate_cover_letter()` and `generate_cover_letter_pdf()`.** Add a `TestCoverLetterGeneration` class in `tests/test_pipeline.py` with: (1) `test_generate_returns_text` — mock OpenAI to return a letter body, verify non-empty string returned, (2) `test_generate_returns_empty_on_failure` — mock OpenAI to raise an exception, verify empty string returned, (3) `test_pdf_creates_file` — call `generate_cover_letter_pdf()` with a test job and letter text, verify the returned path exists and file size > 0. Clean up the file after test.

- [ ] **H5: Guard `score_reason` in UPSERT.** In `core/database.py:139`, change `score_reason = excluded.score_reason` to `score_reason = CASE WHEN excluded.score_reason != '' THEN excluded.score_reason ELSE jobs.score_reason END`. Add a test: save a job with score_reason="Good match", then re-save the same job with score_reason="" and verify the original reason is preserved.

- [ ] **H6: Handle truncation in `_write_minimal_pdf()`.** In `delivery/cover_letter.py:232-233`, when `y < 72`, log a warning and return empty string instead of silently truncating: `log.warning("Cover letter too long for minimal PDF — skipping"); filepath.unlink(missing_ok=True); return ""`. This forces the pipeline to use the text version.
