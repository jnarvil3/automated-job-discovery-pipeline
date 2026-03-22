#!/usr/bin/env python3
"""
Amane's Job Discovery Pipeline

Daily automated pipeline that:
1. Collects job listings from Arbeitnow, Adzuna, and Himalayas
2. Deduplicates against previously seen jobs
3. Scores each job using GPT-4o-mini
4. Sends a ranked email digest
"""

import os
import sys
from datetime import date
from pathlib import Path

import yaml

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from collectors.arbeitnow import ArbeitnowCollector
from collectors.adzuna import AdzunaCollector
from collectors.himalayas import HimalayasCollector
from core.database import get_connection, job_exists, save_job, get_todays_jobs
from core.scorer import score_jobs
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
    for job in all_jobs:
        if not job_exists(conn, job):
            new_jobs.append(job)

    print(f"  New jobs: {len(new_jobs)} (skipped {len(all_jobs) - len(new_jobs)} duplicates)")

    if not new_jobs:
        print("\nNo new jobs today. Done.")
        conn.close()
        return

    # --- Step 3: Score ---
    print("\n🎯 Scoring with GPT-4o-mini...")
    scored_jobs = score_jobs(new_jobs)

    # --- Step 4: Save ---
    print("\n💾 Saving to database...")
    for job in scored_jobs:
        save_job(conn, job)

    high = [j for j in scored_jobs if j.score == "HIGH"]
    medium = [j for j in scored_jobs if j.score == "MEDIUM"]
    low = [j for j in scored_jobs if j.score == "LOW"]
    print(f"  HIGH: {len(high)} | MEDIUM: {len(medium)} | LOW: {len(low)}")

    # --- Step 5: Send digest ---
    print("\n📧 Sending digest...")
    recipient = profile.get("email") or os.environ.get("AMANE_EMAIL", "")

    # Only send HIGH + MEDIUM in the email
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
