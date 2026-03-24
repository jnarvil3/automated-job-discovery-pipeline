#!/bin/bash
# ============================================================
# Overnight Build-Review Loop
# Two-agent system: BUILDER implements, REVIEWER critiques
# Communicates via feedback files, loops until morning
# ============================================================

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
FEEDBACK_DIR="$PROJECT_DIR/.overnight"
LOG_DIR="$FEEDBACK_DIR/logs"
TOOLS="Bash,Read,Edit,Write,Glob,Grep,Agent"

MAX_CYCLES=12            # Max build-review cycles (each ~30-45 min)
STARTED_AT=$(date +%s)
MAX_HOURS=8              # Stop after this many hours

mkdir -p "$FEEDBACK_DIR" "$LOG_DIR"

# ── Initial task seed ──
cat > "$FEEDBACK_DIR/current-task.md" << 'SEED'
# Current Task Queue

## Priority 1: Profile & Data Fixes
- Update config/profile.yaml with Amane's real data from docs_from_amane/
  - Full name: Amane Aguiar Dias de Azevedo
  - Education: MA in International and Development Economics, HTW Berlin, graduation Dec 2026
  - Phone: +49 17631120037
  - Languages: Portuguese (native), English (C2), Spanish (C2), German (A1)
  - 8+ years experience in finance, impact investing, consulting
  - Fix screening_answers.years_of_experience from "0-1" to "8+"
- Fix duplicate job detection (Mast-Jägermeister appeared twice in digest)
- Tighten scoring: marketing roles should be MEDIUM at best (secondary field)

## Priority 2: Core Improvements
- Update cover letter generator to use Flink letter style from docs_from_amane/
- Add Personio ATS browser handler (delivery/browser/personio.py)
- Add end-to-end tests with mock data

## Priority 3: Review Feedback
(Populated by reviewer agent each cycle)
SEED

echo "============================================================"
echo "  OVERNIGHT BUILD-REVIEW LOOP"
echo "  Started: $(date)"
echo "  Max cycles: $MAX_CYCLES | Max hours: $MAX_HOURS"
echo "  Project: $PROJECT_DIR"
echo "============================================================"

for cycle in $(seq 1 $MAX_CYCLES); do
  elapsed=$(( ($(date +%s) - STARTED_AT) / 3600 ))
  if [ "$elapsed" -ge "$MAX_HOURS" ]; then
    echo "[$(date)] Time limit reached ($MAX_HOURS hours). Stopping."
    break
  fi

  echo ""
  echo "════════════════════════════════════════════════════════════"
  echo "  CYCLE $cycle / $MAX_CYCLES  |  Elapsed: ${elapsed}h"
  echo "════════════════════════════════════════════════════════════"

  # ── PHASE 1: BUILDER ──
  echo "[$(date)] BUILDER starting..."

  BUILDER_PROMPT="You are the BUILDER agent for an automated job discovery pipeline.

Your working directory is: $PROJECT_DIR

## Your Task
Read $FEEDBACK_DIR/current-task.md for your task queue. Work through items in priority order.

## Rules
- Read docs_from_amane/ for Amane's CV, cover letter, and context BEFORE making changes
- Read existing code before modifying it
- Make focused, incremental changes — one concern per commit
- Commit each meaningful change with a clear message
- After finishing a task, remove it from current-task.md and add a note to $FEEDBACK_DIR/completed.md
- If you get stuck on something, skip it and move to the next task
- Do NOT push to remote — just commit locally
- Work for as long as needed to make real progress, but don't loop forever on one issue

## What cycle is this?
Cycle $cycle of $MAX_CYCLES. $([ -f "$FEEDBACK_DIR/review-feedback.md" ] && echo 'IMPORTANT: Read review-feedback.md for the reviewer agent suggestions from last cycle — address these.' || echo 'This is the first cycle — start with Priority 1 tasks.')"

  claude -p "$BUILDER_PROMPT" \
    --allowedTools "$TOOLS" \
    2>&1 | tee "$LOG_DIR/builder-cycle-$cycle.log"

  echo "[$(date)] BUILDER finished cycle $cycle"

  # ── PHASE 2: REVIEWER ──
  echo "[$(date)] REVIEWER starting..."

  REVIEWER_PROMPT="You are the REVIEWER agent — a senior engineering critic reviewing work done on an automated job discovery pipeline.

Your working directory is: $PROJECT_DIR

## Your Job
1. Run 'git log --oneline -20' to see recent commits
2. Read the changed files (use git diff HEAD~5 or similar to see what was modified)
3. Read the full codebase structure and key files
4. Read docs_from_amane/ to understand the end user (Amane — international student job hunting in Germany)

## Review Dimensions
Evaluate the CURRENT state of the project across ALL of these dimensions. Be specific — name files, line numbers, and concrete suggestions:

### 1. TECHNICAL QUALITY
- Code bugs, edge cases, error handling gaps
- Performance issues, unnecessary complexity
- Test coverage gaps, untested paths
- Security concerns (API keys, data exposure)

### 2. FUNCTIONALITY
- Missing features that would make the pipeline more effective
- Broken or incomplete flows
- Edge cases in job matching, scoring, or applying
- Integration issues between components

### 3. USER EMPATHY (Amane's perspective)
- Is the digest email clear, actionable, and well-formatted?
- Are cover letters personalized enough based on her real experience?
- Are scoring criteria actually reflecting what she'd want to apply to?
- Is anything confusing or anxiety-inducing for a job seeker?
- Would she trust this system to apply on her behalf?

### 4. DESIGN & ARCHITECTURE
- Code organization, separation of concerns
- Config vs hardcoded values
- Extensibility (easy to add new job sources, ATS platforms?)
- Error recovery and resilience

### 5. CREATIVE IMPROVEMENTS
- Novel features that would give her an edge (e.g., company research, interview prep notes)
- Smart follow-up strategies
- Better matching signals beyond keyword scoring
- Ways to stand out from other applicants

### 6. UI & OUTPUT QUALITY
- Email digest formatting, readability, mobile-friendliness
- Log output clarity for debugging
- Cover letter quality and professionalism
- Any user-facing output that could be improved

## Output
Write your review to $FEEDBACK_DIR/review-feedback.md in this format:

# Review — Cycle $cycle
Date: $(date)

## Critical Issues (fix immediately)
- ...

## High Priority Improvements
- ...

## Medium Priority Improvements
- ...

## Creative Ideas (nice to have)
- ...

Then update $FEEDBACK_DIR/current-task.md — ADD your top suggestions as new Priority 3 items (keep existing unfinished tasks). Be specific enough that the builder agent can act on each item without asking questions."

  claude -p "$REVIEWER_PROMPT" \
    --allowedTools "$TOOLS" \
    2>&1 | tee "$LOG_DIR/reviewer-cycle-$cycle.log"

  echo "[$(date)] REVIEWER finished cycle $cycle"
  echo "[$(date)] Review written to $FEEDBACK_DIR/review-feedback.md"
done

# ── FINAL SUMMARY ──
echo ""
echo "============================================================"
echo "  OVERNIGHT RUN COMPLETE"
echo "  Finished: $(date)"
echo "  Cycles completed: $cycle"
echo "============================================================"
echo ""
echo "Review the results:"
echo "  git log --oneline -30"
echo "  cat $FEEDBACK_DIR/completed.md"
echo "  cat $FEEDBACK_DIR/review-feedback.md"
echo "  Logs: $LOG_DIR/"
