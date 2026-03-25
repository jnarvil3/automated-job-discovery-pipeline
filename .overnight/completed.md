# Completed Tasks

## Cycle 1

### Priority 1: Profile & Data Fixes (all completed in prior commits)
- [x] Updated config/profile.yaml with Amane's real data (commit 0402b60)
- [x] Fixed duplicate job detection across sources (commit 2a9ef25)
- [x] Tightened scoring: marketing roles capped at MEDIUM (commit 7c28458)
- [x] Updated cover letter generator to use Flink letter style (commit 50f9f6f)

### Priority 2: Core Improvements
- [x] Added Personio ATS browser handler with dispatcher integration (commit e11bd76)
- [x] Added 34 end-to-end tests with mock data (commit 008faa3)
- [x] Fixed ATS detector regex bug: board token captured protocol prefix for Personio/BambooHR/Workable (commit 008faa3)

## Cycle 2

### Priority 3: Review Feedback — All items addressed (commit 559c08f)

**Critical fixes:**
- [x] Scoring errors now default to LOW instead of MEDIUM (prevents auto-applying to unscored jobs)
- [x] Fixed .env.example: changed ANTHROPIC_API_KEY to OPENAI_API_KEY, added Adzuna keys
- [x] Fixed resume_path to point to actual CV file (`docs_from_amane/Amane Dias_CV.pdf.pdf`)
- [x] Added startup validation: checks resume exists, OPENAI_API_KEY set, warns on empty sender_email

**High priority:**
- [x] Wired up IndeedRSSCollector in main.py (was implemented but never imported)
- [x] Deleted dead delivery/auto_apply.py (conflicting generate_cover_letter function)
- [x] Added post-submit verification in browser engine + Personio handler (checks for success/error phrases)
- [x] Added retry logic (3 attempts with 2s backoff) to enricher fetch_full_description
- [x] Fixed TextExtractor nested skip-tag bug (depth counter instead of boolean)

**Medium priority:**
- [x] Added role-type pre-filtering to Himalayas collector (ROLE_KEYWORDS check on title)
- [x] Wrapped email digest HTML in proper template with viewport meta + CSS
- [x] Added per-company dedup in apply_dispatcher (max 1 app per company per 7 days)
- [x] Refined marketing cap: only triggers when marketing keyword is in job title, not description
- [x] Replaced deprecated datetime.utcnow() with datetime.now(timezone.utc)

## Cycle 3

### Priority 3: Review Feedback (Cycle 2) — All items addressed

**Critical fixes (commit 6155cfd):**
- [x] C1: Fixed question answerer experience lie — now says "8+ years" instead of "0-1"
- [x] C2: HIGH-tier jobs now get cover letters generated before auto-apply step
- [x] C3: Email sender reads SENDER_EMAIL from env; refuses to send from test domain if not configured

**High priority (commits 2cfdfd6, a100742):**
- [x] H1: Cover letter generator now sees 1500 chars of description (matching scorer) instead of 800; max_tokens bumped to 700
- [x] H2: Added timeout=30 to all OpenAI client constructors (scorer, cover letter, question answerer, form analyzer)
- [x] H3: Added 4 apply_dispatcher tests: per-company dedup, rate limit, dry run, quick_apply (38 tests total, all passing)
- [x] H4: Fixed _write_minimal_pdf to binary mode ("wb") with proper byte encoding

**Medium priority (commit 53b68f6):**
- [x] M1: Updated browser user agent from Chrome 120 to Chrome 131 in engine, personio, and enricher
- [x] M5: Added 8+ years experience context to scorer prompt to prevent incorrect LOW scoring
- [x] M6: Made langdetect deterministic with DetectorFactory.seed = 0
- [x] M2: Added Adzuna pagination (pages 1-3 per search), roughly tripling coverage

## Cycle 4

### Priority 3: Review Feedback (Cycle 3) — All items addressed

**Critical fixes:**
- [x] C1: Fixed save_job to use INSERT ... ON CONFLICT instead of INSERT OR REPLACE, preserving cover_letter, status, apply_method, apply_attempts, and apply_error on re-insert (commit db51299)
- [x] C2: Added server-side search filtering to Arbeitnow collector using ?search= parameter with 15 targeted queries, keeping client-side filter as safety net (commit 5e013dd)

**High priority fixes (commit bc4a1b1):**
- [x] H1: Added explicit import urllib.parse to adzuna.py (was working by accident via CPython internals)
- [x] H2: HTML-escaped cover letter text in email digest to prevent & in FP&A from breaking HTML
- [x] H3: Replaced hardcoded "Amane_Dias_CV.pdf" filenames in all 3 ATS handlers with candidate name-derived filenames
- [x] H4: Removed hardcoded name from _format_full_letter; now loads from config/profile.yaml
- [x] H5: Fixed scorer fit_score conversion to handle GPT string floats like "7.5" via int(float(...))

**Medium priority:**
- [x] M1: Replaced urllib.request with requests.Session in all 3 collectors (adzuna, arbeitnow, himalayas) for connection pooling (commit 85f2027)
- [x] M2: Replaced print() with logging module in main.py, scorer, apply_dispatcher, and email; added logging.basicConfig with timestamps (commit 3d14398)
- [x] M3: Added screenshot cleanup in browser_apply — deletes PNGs older than 7 days (commit 818c22d)
- [x] M4: Simplified rate_limiter to only count from applications table, removing divergent jobs-table fallback (commit 818c22d)
- [x] M5: Added 7 collector tests for Adzuna and Arbeitnow: parsing, missing fields, API keys, dedup, filtering, empty responses (commit b2278ed)
- [x] M6: Passed score_reason to cover letter generator prompt for more targeted letters (commit ad16bc1)

**Test status:** 45 tests, all passing.

## Cycle 5

### Priority 3: Review Feedback (Cycle 4) — All items addressed

**Critical fixes (commit fd1dded):**
- [x] C1: HTML-escaped job.title, job.company, job.location, job.score_reason, and job.url in email digest _job_card() — German company names with & and score reasons with special characters no longer break HTML
- [x] C2: Migrated core/enricher.py from print() to structured logging; removed print(..., end=" ") pattern that spliced into other log lines

**High priority fixes (commits dcbc651, bc7b7b4):**
- [x] H1: Fixed hardcoded name "Amane Dias" to "Amane Aguiar Dias de Azevedo" in question_answerer.py system prompt
- [x] H2: Completed logging migration for all 12 remaining modules: 4 collectors, cover_letter, browser/engine, browser/personio, browser/form_analyzer, 3 ATS integrations, question_answerer — zero print() calls remain
- [x] H3: Wrapped main.py pipeline body in try/finally to guarantee conn.close() on unhandled exceptions; removed redundant conn.close() from 3 early-return paths
- [x] H4: Moved log_application import from inside loop body to module level in apply_dispatcher.py

**Medium priority (commit b37df0f):**
- [x] M1: Added TestEmailDigest class with 3 tests: HTML escaping special characters, auto-applied categorization, empty job list handling
- [x] M2: Added TestHimalayasCollector (3 tests: parsing, role keyword filtering, empty response) and TestIndeedRSSCollector (3 tests: feed parsing, URL dedup, empty feed)
- [x] M3: Added cover letter generation failure check in apply_dispatcher — jobs without a cover letter are now marked quick_apply instead of submitting empty applications

**Test status:** 54 tests, all passing.
