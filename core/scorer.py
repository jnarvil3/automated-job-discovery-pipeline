import json
import logging
import os
from openai import OpenAI
from core.models import Job

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a strict job matching assistant. Score jobs for a specific candidate. Return valid JSON only.

CANDIDATE:
- International master's student in Germany (Brazilian, finishing thesis this semester)
- 8+ years professional experience in finance, impact investing, program management, and consulting. She has more experience than most working students — roles requiring 2-3 years experience are fine.
- German level: A1 only. She CANNOT work in roles requiring German.
- Visa requires student-compatible employment

WHAT SHE IS LOOKING FOR (role type — MUST match one):
- Working student / Werkstudent (15-20h/week)
- Internship / Praktikum (full-time but temporary)
- She is NOT looking for: full-time permanent roles, senior roles, manager roles, freelance, or any role that assumes years of professional experience

FIELDS SHE WANTS (topic — MUST match one):
- Finance (corporate finance, financial analysis, accounting support)
- FP&A / Financial Planning & Analysis
- Controlling (German corporate controlling/reporting)
- Sustainability / ESG / Climate Change
- Renewable Energy / Clean Energy
- Back-office / administrative support for companies
- Marketing (secondary preference — acceptable but not her top choice)

SCORING RULES — be strict:
- HIGH = BOTH conditions met: (1) role type is working student OR internship, AND (2) field clearly matches one of her target fields, AND (3) no German language requirement
- MEDIUM = ONE condition partially met: right role type but adjacent/unclear field, OR right field but role type is slightly off (e.g. "junior" entry-level that could work), OR unclear language requirements
- LOW = ANY of these: requires German, senior/manager/lead role, wrong field entirely, requires years of experience, full-time permanent position

CRITICAL — GERMAN LANGUAGE RULE (ZERO TOLERANCE):
- ANY mention of German being required, expected, or preferred = AUTOMATIC LOW. No exceptions.
- This includes: "German B1/B2/C1", "Deutschkenntnisse", "fließend Deutsch", "gute Deutschkenntnisse", "German is a plus" (if framed as expected)
- Job title in German (e.g. "Finanzbuchhalter") = almost always requires German = LOW
- Description written entirely in German = LOW
- Even "German is a plus/nice to have" in combination with other German indicators = LOW
- When in doubt about language requirements, score LOW — never let a German-required job through as HIGH or MEDIUM

OTHER MISTAKES TO AVOID:
- "Senior", "Manager", "Lead", "Director", "Head of" = NOT suitable for a student — score LOW
- "Junior" roles MIGHT work if they're entry-level — score MEDIUM at best
- A role at a sustainability company but in an unrelated function (e.g. software engineer at a solar company) = LOW

Also return a fit_score from 1-10 indicating how strong the match is.
10 = perfect match (e.g. "Working Student FP&A" at a renewable energy company, English-only)
7-9 = strong match (right role + right field, no German)
4-6 = partial match
1-3 = poor match

Return: {"score": "HIGH|MEDIUM|LOW", "fit_score": 8, "reason": "one sentence"}"""


def score_jobs(jobs: list[Job]) -> list[Job]:
    if not jobs:
        return jobs

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"), timeout=30)

    for job in jobs:
        try:
            user_msg = f"Job: {job.title} at {job.company} ({job.location})\n\nDescription:\n{job.description[:1500]}"

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=150,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )

            text = response.choices[0].message.content.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            job.score = result.get("score", "LOW").upper()
            job.fit_score = int(float(result.get("fit_score", 0)))
            job.score_reason = result.get("reason", "")
            log.info("[%s %d/10] %s at %s — %s", job.score, job.fit_score, job.title, job.company, job.score_reason)

        except (json.JSONDecodeError, KeyError, IndexError) as e:
            log.error("Failed to parse score for %s: %s", job.title, e)
            job.score = "LOW"
            job.score_reason = "Scoring failed — could not parse response"
        except Exception as e:
            log.error("API error scoring %s: %s", job.title, e)
            job.score = "LOW"
            job.score_reason = "Scoring failed — API error"

    # Post-scoring: cap marketing-only roles at MEDIUM
    _marketing_kw = {"marketing", "social media", "content creator", "brand manager", "seo", "sem"}
    _core_fields = {"finance", "fp&a", "financial", "controlling", "sustainability", "esg",
                    "renewable", "energy", "climate", "back office", "backoffice", "accounting"}
    for job in jobs:
        if job.score != "HIGH":
            continue
        title_lower = job.title.lower()
        is_marketing = any(kw in title_lower for kw in _marketing_kw)
        is_core = any(kw in f"{job.title} {job.description[:500]}".lower() for kw in _core_fields)
        if is_marketing and not is_core:
            job.score = "MEDIUM"
            job.score_reason = f"(Marketing-only — capped at MEDIUM) {job.score_reason}"

    return jobs
