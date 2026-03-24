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
