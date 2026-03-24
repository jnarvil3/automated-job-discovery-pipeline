#!/usr/bin/env python3
"""
Amane's Job Discovery Pipeline

Daily automated pipeline that:
1. Collects job listings from Arbeitnow, Adzuna, and Himalayas
2. Fetches full descriptions and filters out German-required jobs
3. Scores remaining jobs using GPT-4o-mini
4. Sends a ranked email digest
"""

import os
import sys
from datetime import date
from pathlib import Path

import yaml
from langdetect import detect, LangDetectException

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from collectors.arbeitnow import ArbeitnowCollector
from collectors.adzuna import AdzunaCollector
from collectors.himalayas import HimalayasCollector
from core.database import get_connection, job_exists, job_exists_by_title_company, save_job, get_todays_jobs
from core.enricher import enrich_jobs, requires_german
from core.scorer import score_jobs
from delivery.apply_dispatcher import apply_to_jobs
from delivery.email import send_digest


def load_profile() -> dict:
    config_path = Path(__file__).parent / "config" / "profile.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def run():
    print("=" * 50)
    print(f"Amane's Job Pipeline — {date.today().isoformat()}")
    print("=" * 50)

    profile = load_profile()
    conn = get_connection()

    # --- Step 1: Collect from all sources ---
    print("\n📡 Collecting jobs...")

    collectors = [
        ArbeitnowCollector(),
        AdzunaCollector(),
        HimalayasCollector(),
    ]

    all_jobs = []
    for collector in collectors:
        try:
            all_jobs.extend(collector.collect())
        except Exception as e:
            print(f"  [ERROR] {collector.__class__.__name__} failed: {e}")

    print(f"\n  Total collected: {len(all_jobs)}")

    # --- Step 2: Deduplicate ---
    print("\n🔍 Deduplicating...")
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

    print(f"  New jobs: {len(new_jobs)} (skipped {len(all_jobs) - len(new_jobs)} duplicates)")

    if not new_jobs:
        print("\nNo new jobs today. Done.")
        conn.close()
        return

    # --- Step 2.5: Quick filter — remove posts written entirely in German ---
    print("\n🇩🇪 Quick filter: removing German-language postings...")
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
    print(f"  Removed {german_count} German-language jobs, {len(english_jobs)} remaining")

    if not english_jobs:
        print("\nNo English-language jobs today. Done.")
        conn.close()
        return

    # --- Step 3: Fetch full descriptions + check for German requirements ---
    print(f"\n🔎 Fetching full job descriptions & checking German requirements...")
    enriched_jobs, german_required = enrich_jobs(english_jobs)

    # Save German-required jobs as LOW
    for job in german_required:
        save_job(conn, job)
    print(f"\n  Passed: {len(enriched_jobs)} | Rejected (German required): {len(german_required)}")

    if not enriched_jobs:
        print("\nNo jobs passed language filter. Done.")
        conn.close()
        return

    # --- Step 4: Score ---
    print(f"\n🎯 Scoring {len(enriched_jobs)} jobs with GPT-4o-mini...")
    scored_jobs = score_jobs(enriched_jobs)

    # --- Step 4.5: Hard German filter (safety net after AI scoring) ---
    print("\n🛡️  Post-scoring German filter...")
    german_caught = 0
    for job in scored_jobs:
        if job.score in ("HIGH", "MEDIUM"):
            is_german, reason = requires_german(f"{job.title} {job.description}")
            if is_german:
                job.score = "LOW"
                job.score_reason = f"Post-score rejection: {reason}"
                german_caught += 1
    if german_caught:
        print(f"  Caught {german_caught} German-requirement jobs that slipped through scoring")

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
    print(f"  TOP {len(high)} | MEDIUM: {len(medium)} | LOW: {len(low)}")

    # --- Step 5.5: Auto-apply ---
    print("\n🤖 Auto-applying...")
    dry_run = "--send" not in sys.argv
    scored_jobs = apply_to_jobs(scored_jobs, profile, conn, dry_run=dry_run)

    # --- Step 6: Save ---
    print("\n💾 Saving to database...")
    for job in scored_jobs:
        save_job(conn, job)

    # --- Step 7: Send digest ---
    print("\n📧 Sending digest...")
    recipient = profile.get("email") or os.environ.get("AMANE_EMAIL", "")

    relevant_jobs = [j for j in scored_jobs if j.score in ("HIGH", "MEDIUM")]

    if not relevant_jobs:
        print("  No HIGH or MEDIUM jobs today — skipping email.")
    elif not recipient:
        print("  [WARN] No recipient email configured — printing to stdout")
        send_digest(relevant_jobs, "")
    elif "--send" in sys.argv:
        send_digest(relevant_jobs, recipient)
    else:
        print("  [DRY RUN] Use --send to actually email. Printing to stdout:")
        send_digest(relevant_jobs, "")

    conn.close()
    print("\n✅ Done.")


if __name__ == "__main__":
    run()
