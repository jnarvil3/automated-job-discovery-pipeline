# Changelog

## 2026-03-26

### Added — ATS career page probing
- When aggregator URLs (Adzuna) return 403/404, probe known ATS URL patterns using company name
- Supports Greenhouse, Lever, Workable, Personio, Recruitee, BambooHR
- False-positive filtering for generic marketing pages
- 13/13 companies from digest now resolve to real career pages
- Existing unapplied HIGH/MEDIUM jobs are re-enriched each run for ATS detection
- HIGH tier enabled for auto-apply alongside MEDIUM

## 2026-03-24 — Overnight Build-Review Experiment

Ran a two-agent autonomous loop (builder + reviewer) for 12 cycles, producing 37 commits.
Checkpoint: `788bbf0`. Safe to revert with `git reset --hard 788bbf0`.

### Added — Profile & Scoring (Cycle 1)
- Populated Amane's real profile data (CV, cover letter, phone, 8+ years experience, languages)
- Fixed duplicate job detection across sources
- Marketing roles capped at MEDIUM (was leaking into HIGH)
- Cover letter updated to Flink style with score reasons for personalization

### Fixed — Critical Issues (Cycles 1-2)
- Experience screening answer: "0-1 years" corrected to "8+"
- HIGH-tier jobs now get cover letters before auto-apply
- Hardcoded sender email removed
- Indeed RSS collector wired up (was implemented but never imported)
- Resume encoding fixed for email attachments
- HTML escaping fixed throughout email digest

### Added — New Features (Cycles 3-5)
- Personio ATS browser handler
- Parallel enricher & scorer (5 threads each)
- Retry mechanism for failed applications
- Job freshness tracking with badges in digest
- URL normalization (strips tracking params before dedup)
- Pause mechanism (create `data/.pause` to stop pipeline)
- Collector stats tracking in digest footer
- Expanded English-signals detection (7 to 17 phrases)
- Server-side filtering for Arbeitnow collector
- Connection pooling across all collectors

### Improved — Code Quality (Cycles 2-5)
- Migrated all print() to proper logging module (zero print calls remain)
- Batch DB commits instead of per-row
- API timeouts on all OpenAI calls
- DB indexes on jobs table
- UPSERT logic that never downgrades score/status
- LRU cache on profile.yaml reads
- Score reasons flow into cover letters

### Added — Test Coverage (Cycles 1-5)
- 0 to 77 tests, all passing
- End-to-end pipeline tests
- Apply dispatcher tests (dedup, rate limit, dry run)
- Collector tests (Adzuna, Arbeitnow, Himalayas, Indeed RSS)
- Email digest tests
- Enricher tests
