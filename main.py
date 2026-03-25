#!/usr/bin/env python3
"""
Amane's Job Discovery Pipeline

Daily automated pipeline that:
1. Collects job listings from Arbeitnow, Adzuna, and Himalayas
2. Fetches full descriptions and filters out German-required jobs
3. Scores remaining jobs using GPT-4o-mini
4. Sends a ranked email digest
"""

import logging
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import yaml
from langdetect import detect, DetectorFactory, LangDetectException

DetectorFactory.seed = 0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
log = logging.getLogger("pipeline")

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from collectors.arbeitnow import ArbeitnowCollector
from collectors.adzuna import AdzunaCollector
from collectors.himalayas import HimalayasCollector
from collectors.indeed_rss import IndeedRSSCollector
from core.database import get_connection, job_exists, job_exists_by_title_company, save_job, get_retry_candidates
from core.enricher import enrich_jobs, requires_german
from core.scorer import score_jobs
from delivery.apply_dispatcher import apply_to_jobs
from delivery.cover_letter import generate_cover_letter
from delivery.email import send_digest


def load_profile() -> dict:
    config_path = Path(__file__).parent / "config" / "profile.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def validate_startup(profile: dict):
    """Check critical config before running the pipeline. Exit early on fatal issues."""
    project_root = Path(__file__).parent
    errors = []
    warnings = []

    # Check resume file exists (resolve relative to project root)
    resume_path = profile.get("resume_path", "")
    if resume_path:
        resolved = Path(resume_path) if Path(resume_path).is_absolute() else project_root / resume_path
        if not resolved.exists():
            errors.append(f"Resume file not found: {resolved}")
        else:
            # Store resolved absolute path back for use by the pipeline
            profile["resume_path"] = str(resolved)
    else:
        errors.append("resume_path is not set in config/profile.yaml")

    # Check OPENAI_API_KEY
    if not os.environ.get("OPENAI_API_KEY"):
        errors.append("OPENAI_API_KEY environment variable is not set (required for scoring and cover letters)")

    # Warn if sender_email is empty
    if not profile.get("sender_email"):
        warnings.append("sender_email is empty in profile.yaml — auto-apply emails will use the Resend test domain")

    for w in warnings:
        log.warning(w)
    if errors:
        for e in errors:
            log.error(e)
        log.error("Fix the above errors and re-run.")
        sys.exit(1)


def run():
    log.info("=" * 50)
    log.info("Amane's Job Pipeline — %s", date.today().isoformat())
    log.info("=" * 50)

    # Check for pause file
    pause_file = Path(__file__).parent / "data" / ".pause"
    if pause_file.exists():
        log.info("Pipeline paused (data/.pause exists — delete to resume)")
        sys.exit(0)

    profile = load_profile()
    validate_startup(profile)
    conn = get_connection()
    try:
        # --- Step 1: Collect from all sources ---
        log.info("Collecting jobs...")

        collectors = [
            ArbeitnowCollector(),
            AdzunaCollector(),
            HimalayasCollector(),
            IndeedRSSCollector(profile.get("indeed_searches", [])),
        ]

        all_jobs = []
        collector_stats = {}
        for collector in collectors:
            name = collector.__class__.__name__
            try:
                collected = collector.collect()
                all_jobs.extend(collected)
                collector_stats[name] = len(collected)
            except Exception as e:
                log.error("%s failed: %s", name, e)
                collector_stats[name] = "FAILED"

        stats_str = ", ".join(f"{k}={v}" for k, v in collector_stats.items())
        log.info("Collector summary: %s", stats_str)
        log.info("Total collected: %d", len(all_jobs))

        # --- Step 2: Deduplicate ---
        log.info("Deduplicating...")
        new_jobs = []
        seen_title_company: set[tuple[str, str]] = set()
        for job in all_jobs:
            key = (job.title.strip().lower(), job.company.strip().lower())
            if job_exists(conn, job):
                continue
            if job_exists_by_title_company(conn, job.title, job.company):
                continue
            if key in seen_title_company:
                continue
            seen_title_company.add(key)
            new_jobs.append(job)

        log.info("New jobs: %d (skipped %d duplicates)", len(new_jobs), len(all_jobs) - len(new_jobs))

        if not new_jobs:
            log.info("No new jobs today. Done.")
            return

        # --- Step 2.5: Quick filter — remove posts written entirely in German ---
        log.info("Quick filter: removing German-language postings...")
        english_jobs = []
        german_count = 0
        for job in new_jobs:
            text = f"{job.title} {job.description}"
            try:
                lang = detect(text)
            except LangDetectException:
                lang = "unknown"
            if lang == "de":
                german_count += 1
                job.score = "LOW"
                job.score_reason = "Auto-rejected: job posting is in German"
                save_job(conn, job)
            else:
                english_jobs.append(job)
        log.info("Removed %d German-language jobs, %d remaining", german_count, len(english_jobs))

        if not english_jobs:
            log.info("No English-language jobs today. Done.")
            return

        # --- Step 3: Fetch full descriptions + check for German requirements ---
        log.info("Fetching full job descriptions & checking German requirements...")
        enriched_jobs, german_required = enrich_jobs(english_jobs)

        # Save German-required jobs as LOW
        for job in german_required:
            save_job(conn, job)
        log.info("Passed: %d | Rejected (German required): %d", len(enriched_jobs), len(german_required))

        if not enriched_jobs:
            log.info("No jobs passed language filter. Done.")
            return

        # --- Step 4: Score ---
        log.info("Scoring %d jobs with GPT-4o-mini...", len(enriched_jobs))
        scored_jobs = score_jobs(enriched_jobs)

        # --- Step 4.5: Hard German filter (safety net after AI scoring) ---
        log.info("Post-scoring German filter...")
        german_caught = 0
        for job in scored_jobs:
            if job.score in ("HIGH", "MEDIUM"):
                is_german, reason = requires_german(f"{job.title} {job.description}")
                if is_german:
                    job.score = "LOW"
                    job.score_reason = f"Post-score rejection: {reason}"
                    german_caught += 1
        if german_caught:
            log.info("Caught %d German-requirement jobs that slipped through scoring", german_caught)

        # --- Step 4.6: German-title heuristic (Werkstudent/Praktikum without English signals) ---
        ENGLISH_SIGNALS = ("english-speaking", "english working environment", "no german required",
                           "english is the working language", "team language is english",
                           "working language is english", "english only")
        german_title_demoted = 0
        for job in scored_jobs:
            if job.score != "HIGH":
                continue
            title_lower = job.title.lower()
            if "werkstudent" in title_lower or "praktikum" in title_lower:
                desc_lower = job.description.lower()
                if not any(signal in desc_lower for signal in ENGLISH_SIGNALS):
                    job.score = "MEDIUM"
                    job.score_reason = f"(German-phrased title — verify language requirements) {job.score_reason}"
                    german_title_demoted += 1
        if german_title_demoted:
            log.info("Demoted %d jobs with German-phrased titles to MEDIUM", german_title_demoted)

        # --- Step 5: Tier assignment ---
        # TOP tier: best 2-3 HIGH jobs with fit_score >= 7
        MIN_HIGH_FIT = 7
        TOP_N = 3
        high = [j for j in scored_jobs if j.score == "HIGH" and j.fit_score >= MIN_HIGH_FIT]
        high.sort(key=lambda j: j.fit_score, reverse=True)

        # Demote HIGH jobs beyond top N, or those below fit threshold
        weak_highs = [j for j in scored_jobs if j.score == "HIGH" and j.fit_score < MIN_HIGH_FIT]
        for job in weak_highs:
            job.score = "MEDIUM"
            job.score_reason = f"(Fit {job.fit_score}/10 — below threshold) {job.score_reason}"
        for job in high[TOP_N:]:
            job.score = "MEDIUM"
            job.score_reason = f"(Demoted from TOP — fit {job.fit_score}/10) {job.score_reason}"

        high = high[:TOP_N]
        medium = [j for j in scored_jobs if j.score == "MEDIUM"]
        low = [j for j in scored_jobs if j.score == "LOW"]
        log.info("TOP %d | MEDIUM: %d | LOW: %d", len(high), len(medium), len(low))

        # --- Step 5.1: Generate cover letters for HIGH-tier jobs ---
        if high:
            log.info("Generating cover letters for %d TOP jobs...", len(high))
            for job in high:
                if not job.cover_letter:
                    job.cover_letter = generate_cover_letter(job)
                    if job.cover_letter:
                        log.info("  Cover letter: %s at %s", job.title, job.company)

        # --- Step 5.5: Retry previously failed applications ---
        retry_jobs = get_retry_candidates(conn)
        if retry_jobs:
            log.info("Found %d failed jobs eligible for retry", len(retry_jobs))
            # Add retry candidates to scored_jobs so they get re-attempted
            existing_ids = {j.id for j in scored_jobs}
            for rj in retry_jobs:
                if rj.id not in existing_ids:
                    scored_jobs.append(rj)

        # --- Step 5.6: Auto-apply ---
        log.info("Auto-applying...")
        dry_run = "--send" not in sys.argv
        scored_jobs = apply_to_jobs(scored_jobs, profile, conn, dry_run=dry_run)

        # --- Step 6: Save ---
        log.info("Saving to database...")
        for job in scored_jobs:
            try:
                save_job(conn, job)
            except sqlite3.IntegrityError as e:
                log.warning("Failed to save %s at %s: %s", job.title, job.company, e)

        # --- Step 7: Send digest ---
        log.info("Sending digest...")
        recipient = profile.get("email") or os.environ.get("AMANE_EMAIL", "")

        relevant_jobs = [j for j in scored_jobs if j.score in ("HIGH", "MEDIUM")]

        if not relevant_jobs:
            log.info("No HIGH or MEDIUM jobs today — skipping email.")
        elif not recipient:
            log.warning("No recipient email configured — printing to stdout")
            send_digest(relevant_jobs, "", collector_stats=collector_stats)
        elif "--send" in sys.argv:
            send_digest(relevant_jobs, recipient, collector_stats=collector_stats)
        else:
            log.info("[DRY RUN] Use --send to actually email. Printing to stdout:")
            send_digest(relevant_jobs, "", collector_stats=collector_stats)

        log.info("Done.")
    finally:
        conn.close()


if __name__ == "__main__":
    run()
