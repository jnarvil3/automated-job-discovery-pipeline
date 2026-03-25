"""
GPT-powered screening question answerer.
Maps candidate profile data to custom ATS questions honestly.
"""

import json
import logging
import os
from openai import OpenAI
from core.models import Job

log = logging.getLogger(__name__)


# IMPORTANT: Hand-tuned to Amane's profile as of March 2026. Update when her CV changes.
SYSTEM_PROMPT = """You answer job application screening questions for a specific candidate. You must answer HONESTLY — never fabricate qualifications or experience.

CANDIDATE PROFILE:
- Name: Amane Aguiar Dias de Azevedo
- Nationality: Brazilian
- Status: International master's student in Germany, finishing thesis this semester
- Work authorization: Student visa — eligible for working student (15-20h/week) and internship
- Languages: English (fluent), Portuguese (native), German (A1 — basic only)
- Looking for: Working student or internship roles in Finance, FP&A, Controlling, Sustainability, Renewable Energy, Back Office, Marketing

RULES:
- Answer each question concisely and professionally
- For yes/no questions: answer honestly based on the profile
- For multiple choice: pick the most accurate option
- For "years of experience": answer honestly — "8+ years in finance, impact investing, and consulting"
- For German proficiency: always be honest — "A1 (Basic)" or "Beginner"
- For work authorization: "Student visa with work permission"
- For salary: "Negotiable" or the market rate for working students in Germany
- NEVER claim skills, experience, or qualifications the candidate doesn't have
- If unsure, give the most conservative honest answer

Return a JSON object mapping question IDs to answers.
"""


def answer_questions(questions: list[dict], candidate: dict, job: Job) -> dict:
    """
    Use GPT-4o-mini to answer screening questions based on candidate profile.

    Args:
        questions: List of question dicts from ATS (format varies by platform)
        candidate: Candidate data from profile.yaml
        job: The job being applied to (for context)

    Returns:
        Dict mapping question_id -> answer
    """
    if not questions:
        return {}

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        return _answer_from_config(questions, candidate)

    client = OpenAI(api_key=api_key, timeout=30)

    # Format questions for GPT
    q_text = f"Job: {job.title} at {job.company} ({job.location})\n\nScreening questions:\n"
    for i, q in enumerate(questions):
        q_id = q.get("id", q.get("name", str(i)))
        label = q.get("label", q.get("text", q.get("question", "")))
        q_type = q.get("type", "text")
        required = q.get("required", False)
        options = q.get("options", q.get("choices", []))

        q_text += f"\n{q_id}. [{q_type}] {'(required)' if required else ''} {label}"
        if options:
            opt_labels = [o.get("label", o.get("text", str(o))) if isinstance(o, dict) else str(o) for o in options]
            q_text += f"\n   Options: {', '.join(opt_labels)}"

    q_text += "\n\nReturn a JSON object mapping question ID to answer. For multiple choice, return the option value/ID."

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=500,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": q_text},
            ],
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except Exception as e:
        log.warning("GPT failed: %s — falling back to config answers", e)
        return _answer_from_config(questions, candidate)


def _answer_from_config(questions: list[dict], candidate: dict) -> dict:
    """Fallback: match questions to pre-configured answers by keyword."""
    screening = candidate.get("screening_answers", {})
    answers = {}

    keyword_map = {
        "salary": "salary_expectation",
        "compensation": "salary_expectation",
        "start": "earliest_start_date",
        "available": "earliest_start_date",
        "relocat": "willing_to_relocate",
        "authorization": "work_authorization",
        "visa": "work_authorization",
        "permit": "work_authorization",
        "hours": "hours_per_week",
        "german": "german_fluency",
        "deutsch": "german_fluency",
        "english": "english_fluency",
        "notice": "notice_period",
        "experience": "years_of_experience",
        "hear": "how_did_you_hear",
        "source": "how_did_you_hear",
    }

    for q in questions:
        q_id = str(q.get("id", q.get("name", "")))
        label = (q.get("label", "") or q.get("text", "") or q.get("question", "")).lower()

        for keyword, config_key in keyword_map.items():
            if keyword in label and config_key in screening:
                answers[q_id] = screening[config_key]
                break

    return answers
