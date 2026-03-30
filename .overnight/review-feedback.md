# Review — Cycle 9
Date: Tue Mar 24 22:29:04 -03 2026

**Test status:** 77/77 passing (0.50s)
**Lines changed in last 5 commits:** +626, -141 across 21 files

Nine cycles of iterative improvement have produced a well-structured, thoroughly tested pipeline. The recent work on data integrity (batch commits, UPSERT logic, cleanup_duplicates window function), parallelization (scorer + enricher), and test coverage (77 tests) is solid. This review focuses on thread safety, filtering correctness, and ID stability — bugs that won't show up in single-threaded tests but will bite in production.

---

## Critical Issues (fix immediately)

### C1: Module-level `requests.Session` shared across threads is not thread-safe
**File:** `core/enricher.py:18-23`
**Problem:** `_session = requests.Session()` is created at module level and shared across the `ThreadPoolExecutor(max_workers=5)` in `enrich_jobs()`. The `requests` library [documents Sessions as NOT thread-safe](https://requests.readthedocs.io/en/latest/user/advanced/#session-objects). This can cause corrupted responses, cookie bleed across requests, or intermittent `ConnectionError` in production.
**Fix:** Create a session per thread. Simplest approach — instantiate inside `_fetch_for_job()`:
```python
_HEADERS = {
    "User-Agent": "Mozilla/5.0 ...",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

def _fetch_for_job(job: Job) -> tuple[Job, str | None, str, str]:
    session = requests.Session()
    session.headers.update(_HEADERS)
    full_text, final_url, raw_html = fetch_full_description(job.url, session=session)
    return job, full_text, final_url, raw_html
```
Update `fetch_full_description` to accept a `session` parameter instead of using the module-level `_session`.

### C2: German language filter has false positives on negated phrases
**File:** `core/enricher.py:28-30`
**Problem:** The regex `(require|need|must have|expect).*german` matches sentences like "We do NOT require German" because `.*` bridges across the negation word. Similarly, `german\s+(is\s+)?(required|mandatory...)` matches "German is NOT required". This means Amane could miss good English-friendly jobs that explicitly say German is NOT needed.
**Fix:** In `requires_german()`, after finding a match, check whether the ±80 character context around the match contains a negation. If so, skip it:
```python
NEGATION_RE = re.compile(
    r"\b(not|n't|no|without|don't|doesn't|isn't|not required|not necessary|not needed|not mandatory|nicht erforderlich|nicht notwendig)\b",
    re.IGNORECASE,
)

def requires_german(text: str) -> tuple[bool, str]:
    for pattern in COMPILED_PATTERNS:
        match = pattern.search(text)
        if match:
            start = max(0, match.start() - 80)
            end = min(len(text), match.end() + 80)
            context = text[start:end]
            if NEGATION_RE.search(context):
                continue  # Negated — skip this match
            return True, f"German required: '...{context.strip()}...'"
    return False, ""
```

### C3: Job ID changes after URL redirect, breaking UPSERT and retry lookups
**File:** `core/enricher.py:189-190`, `core/models.py:43-45`
**Problem:** `Job.id` is a `@property` computed dynamically from `normalize_url(self.url)`. When `enrich_jobs()` updates `job.url` to the post-redirect `final_url` (line 190), the job's ID silently changes. Jobs saved before enrichment (German-rejected at main.py:172) get one ID; the same conceptual job saved after enrichment gets a different ID. This means:
1. UPSERT in `save_job` creates a duplicate instead of updating
2. `get_retry_candidates()` returns jobs whose IDs don't match DB rows (because the URL was already redirected when first saved, but the retry constructs a Job with that URL, getting yet another ID if normalization differs)
**Fix:** Freeze the ID at construction time so it survives URL changes:
```python
@dataclass
class Job:
    ...
    _frozen_id: str = field(init=False, repr=False, default="")

    def __post_init__(self):
        self._frozen_id = hashlib.sha256(normalize_url(self.url).encode()).hexdigest()[:16]

    @property
    def id(self) -> str:
        return self._frozen_id
```

---

## High Priority Improvements

### H1: Greenhouse and Lever ATS modules still use `urllib.request` with manual multipart encoding
**Files:** `delivery/ats/greenhouse.py:10-11, 47-98`, `delivery/ats/lever.py:12-14, 49-92`
**Problem:** The enricher was migrated to `requests` (Cycle 8), but the ATS submission modules still use `urllib.request` with hand-rolled `BytesIO` multipart form-data. This means no connection pooling, no automatic redirect following, and fragile manual boundary encoding.
**Fix:** Migrate both to `requests`. Replace the manual `BytesIO` multipart building with `requests.post(url, data=fields, files=file_list, headers=headers, timeout=30)`. This cuts ~40 lines from each file and makes the code more robust. Handle `requests.exceptions.HTTPError` instead of `urllib.error.HTTPError`.

### H2: `log_application()` still commits after every insert, defeating batch strategy
**File:** `core/database.py:165`
**Problem:** `save_job` was fixed to support `commit=False` for batching (Cycle 8). But `log_application()` always calls `conn.commit()`. In `apply_to_jobs()`, each application triggers a `log_application()` commit plus a later batched `save_job()` — mixing single and batch commits.
**Fix:** Add `commit: bool = True` parameter to `log_application()`. In `apply_to_jobs()`, pass `commit=False` and do a single `conn.commit()` after the apply loop.

### H3: Cover letter generated (GPT API call) even when no apply method is available
**File:** `delivery/apply_dispatcher.py:269-276`
**Problem:** For every eligible MEDIUM job, `generate_cover_letter()` is called (OpenAI API request, ~$0.002 each) BEFORE checking whether any apply method can actually be used. Jobs with no ATS, no apply email, and no browser target still burn a GPT call, then fall through to `quick_apply`.
**Fix:** Pre-check viability before calling GPT:
```python
has_api = methods.get("api") and job.ats_platform in ("greenhouse", "lever", "workable")
has_browser = methods.get("browser") and job.ats_platform
has_email = methods.get("email") and job.apply_email
if not (has_api or has_browser or has_email):
    job.status = "quick_apply"
    skipped += 1
    continue
# Only now generate cover letter
```

### H4: No test for `generate_cover_letter()` or `generate_cover_letter_pdf()`
**Problem:** `TestCoverLetter` only covers the string formatter `_format_full_letter()`. The GPT call wrapper `generate_cover_letter()` and the PDF/DOCX generators have zero test coverage. A broken prompt, a changed API response format, or a reportlab import failure would go undetected.
**Fix:** Add at least 3 tests:
```python
class TestCoverLetterGeneration:
    def test_generate_returns_text_with_mocked_openai(self):
        """Mock OpenAI, verify generate_cover_letter returns the GPT response."""
    def test_generate_returns_empty_on_api_failure(self):
        """Mock OpenAI to raise, verify empty string returned (not exception)."""
    def test_pdf_creates_valid_file(self):
        """Call generate_cover_letter_pdf with a test letter, verify file exists and size > 0."""
```

### H5: `score_reason` unconditionally overwritten on UPSERT
**File:** `core/database.py:139`
**Problem:** `score_reason = excluded.score_reason` always takes the incoming value. If a retry candidate is re-saved with an empty `score_reason` (because it was reconstructed from DB without re-scoring), the original detailed reason is lost.
**Fix:** Guard like other fields:
```sql
score_reason = CASE WHEN excluded.score_reason != '' THEN excluded.score_reason ELSE jobs.score_reason END
```

### H6: `_write_minimal_pdf()` silently drops the bottom half of long cover letters
**File:** `delivery/cover_letter.py:232-233`
**Problem:** The fallback PDF generator stops writing at `y < 72`. A 300+ word letter at 14pt line spacing will exceed one page, causing the closing paragraphs to be silently dropped. Amane could send a half-finished cover letter.
**Fix:** Either add page breaks in the minimal generator, or log a warning and return empty string so the pipeline sends the text version instead:
```python
if y < 72:
    log.warning("Cover letter truncated in minimal PDF — skipping PDF")
    filepath.unlink(missing_ok=True)
    return ""
```

---

## Medium Priority Improvements

### M1: `cleanup_duplicates()` runs every invocation with no guard
**File:** `main.py:107-109`
**Problem:** The window function query scans the entire jobs table every run. As the database grows, this wastes time when there are usually zero duplicates.
**Fix:** Check for duplicates first with a cheap COUNT query, only run cleanup if duplicates exist.

### M2: Email digest subject could include "needs attention" count
**File:** `delivery/email.py:42`
**Current:** `"Amane's Jobs — Mar 24, 2026 — 2 Top, 3 Auto-Applied"`
**Improvement:** Include the count of jobs needing manual action: `"Amane's Jobs — Mar 24 — 2 Top, 3 Applied, 1 Needs Action"`. This tells Amane at a glance whether she needs to open the email.

### M3: Adzuna collector makes up to 30 API calls per run
**File:** `collectors/adzuna.py:48-50`
**Problem:** 15 queries × 2 pages = 30 requests. Many return overlapping results.
**Fix:** Track seen URLs across queries; if a query's page returns >80% already-seen URLs, skip remaining pages.

### M4: No idempotency guard on concurrent pipeline runs
**File:** `delivery/apply_dispatcher.py`
**Problem:** If the pipeline runs twice simultaneously (cron overlap), both runs could apply to the same job. There's no file lock or DB advisory lock.
**Fix:** Add a simple file lock at pipeline start:
```python
lock_file = Path(__file__).parent / "data" / ".pipeline.lock"
# Use fcntl.flock or a cross-platform lockfile library
```

### M5: Browser stealth is minimal — only removes `navigator.webdriver`
**File:** `delivery/browser/engine.py:50-52`
**Problem:** Most modern bot detection (Cloudflare, PerimeterX) checks for many more signals: Chrome headless user agent hints, `navigator.plugins`, `window.chrome`, WebGL renderer, etc.
**Fix:** Add 5-10 additional stealth patches from established libraries like `playwright-stealth` or manually:
```javascript
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
window.chrome = { runtime: {} };
```

### M6: No visibility into cover letter generation in digest
**Problem:** The email shows "📝 Draft Cover Letter" or "📝 Letter Sent" but doesn't indicate which style was generated (Flink-style body vs. fallback). If GPT generation failed and fell back to empty, the apply dispatcher skips the job — but there's no way for Amane to know WHY a job was skipped.
**Fix:** Add a small note to the "NEEDS ATTENTION" section: "Cover letter generation failed" when `apply_error` contains that info.

---

## Creative Ideas (nice to have)

### I1: Application follow-up tracker in digest
Add a "📬 Follow-Up Needed" section for jobs applied 7+ days ago with no response update. Query `applications` for `submitted_at < date('now', '-7 days')`. Include company name, role, and days since application. This is a proven strategy to increase response rates — most applicants don't follow up.

### I2: Company quick-research snippet for TOP jobs
For top 2-3 HIGH-tier jobs, extract the "About Us" paragraph from the job description (usually near the top) and include a 2-3 sentence summary. Add company size and ESG/sustainability angle if detectable. Helps Amane decide where to invest her customization effort.

### I3: Interview prep notes for auto-applied jobs
After auto-applying, generate 3-4 bullet points connecting Amane's specific experience to the job requirements. Store in DB and include in digest. When she gets an interview call (often days later), she has instant prep material without re-reading the full posting.

### I4: Weekly statistics summary
Once per week, include a stats block: total jobs seen, applications sent (by method), response rate (if trackable), top companies. Gives Amane visibility into pipeline throughput and helps identify if coverage or quality is declining.

### I5: "Already Applied" badge in digest
When a new job appears at the same company as a previous auto-application, show a badge: "📌 Applied to CompanyX on Mar 17 for WS Finance". Prevents Amane from manually double-applying and shows the system is working.
