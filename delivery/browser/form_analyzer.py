"""
LLM-powered form field extraction and mapping.

Extracts form fields from the page DOM, then uses GPT-4o-mini
to map candidate data to each field.
"""

import json
import logging
import os
from dataclasses import dataclass

from core.models import Job

log = logging.getLogger(__name__)


@dataclass
class FormField:
    selector: str       # CSS selector
    field_type: str     # text, email, tel, file, select, radio, checkbox, textarea
    label: str          # human-readable label
    name: str           # HTML name attribute
    required: bool
    options: list[str]  # for select/radio/checkbox
    value_to_fill: str  # determined by LLM mapping


async def _extract_fields_from_dom(page) -> list[dict]:
    """Extract all form fields from the page using JavaScript."""
    return await page.evaluate("""() => {
        const fields = [];
        const inputs = document.querySelectorAll('input, select, textarea');

        for (const el of inputs) {
            // Skip hidden and submit inputs
            if (el.type === 'hidden' || el.type === 'submit' || el.type === 'button') continue;
            if (el.offsetParent === null && el.type !== 'file') continue;  // not visible (except file inputs)

            // Find label
            let label = '';
            if (el.id) {
                const labelEl = document.querySelector(`label[for="${el.id}"]`);
                if (labelEl) label = labelEl.textContent.trim();
            }
            if (!label && el.closest('label')) {
                label = el.closest('label').textContent.trim();
            }
            if (!label) label = el.getAttribute('aria-label') || el.placeholder || el.name || '';

            // Get options for select/radio
            let options = [];
            if (el.tagName === 'SELECT') {
                options = Array.from(el.options).map(o => o.text.trim()).filter(Boolean);
            }

            // Build a reliable CSS selector
            let selector = '';
            if (el.id) {
                selector = '#' + CSS.escape(el.id);
            } else if (el.name) {
                selector = `${el.tagName.toLowerCase()}[name="${el.name}"]`;
            } else {
                // Fallback: use nth-of-type
                const parent = el.parentElement;
                if (parent) {
                    const siblings = Array.from(parent.querySelectorAll(el.tagName));
                    const idx = siblings.indexOf(el);
                    selector = `${el.tagName.toLowerCase()}:nth-of-type(${idx + 1})`;
                }
            }

            fields.push({
                selector: selector,
                type: el.type || el.tagName.toLowerCase(),
                label: label.substring(0, 100),
                name: el.name || '',
                required: el.required || el.getAttribute('aria-required') === 'true',
                options: options,
            });
        }

        // Also look for radio button groups
        const radioGroups = {};
        document.querySelectorAll('input[type="radio"]').forEach(r => {
            if (!radioGroups[r.name]) {
                const label = r.closest('fieldset')?.querySelector('legend')?.textContent?.trim() || r.name;
                const options = Array.from(document.querySelectorAll(`input[name="${r.name}"]`))
                    .map(o => {
                        const lbl = document.querySelector(`label[for="${o.id}"]`);
                        return lbl ? lbl.textContent.trim() : o.value;
                    });
                radioGroups[r.name] = {
                    selector: `input[name="${r.name}"]`,
                    type: 'radio',
                    label: label,
                    name: r.name,
                    required: r.required,
                    options: options,
                };
            }
        });

        // Add radio groups (avoiding duplicates)
        for (const group of Object.values(radioGroups)) {
            // Remove individual radio inputs already added
            const idx = fields.findIndex(f => f.name === group.name && f.type === 'radio');
            if (idx >= 0) fields.splice(idx, 1);
            fields.push(group);
        }

        return fields;
    }""")


async def extract_and_map_fields(page, candidate: dict, cover_letter: str,
                                  job: Job) -> list[FormField]:
    """
    Extract form fields from the page and use GPT to map candidate data to them.
    """
    raw_fields = await _extract_fields_from_dom(page)
    if not raw_fields:
        return []

    # Try GPT mapping first, fall back to keyword matching
    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key:
        mapped = _map_with_gpt(raw_fields, candidate, cover_letter, job, api_key)
    else:
        mapped = _map_with_keywords(raw_fields, candidate, cover_letter)

    return mapped


def _map_with_gpt(raw_fields: list[dict], candidate: dict, cover_letter: str,
                  job: Job, api_key: str) -> list[FormField]:
    """Use GPT-4o-mini to map candidate data to form fields."""
    from openai import OpenAI
    client = OpenAI(api_key=api_key, timeout=30)

    fields_desc = json.dumps(raw_fields, indent=2)
    candidate_desc = json.dumps({
        "first_name": candidate.get("first_name", ""),
        "last_name": candidate.get("last_name", ""),
        "email": candidate.get("email", ""),
        "phone": candidate.get("phone", ""),
        "linkedin": candidate.get("linkedin_url", ""),
        "location": candidate.get("current_location", ""),
        "work_authorization": candidate.get("work_authorization", ""),
        "screening_answers": candidate.get("screening_answers", {}),
    }, indent=2)

    prompt = f"""Map this candidate's data to these form fields. Return a JSON array where each element has:
- "selector": the CSS selector (from the field data)
- "value": the value to fill in

For file inputs (type=file), set value to "RESUME".
For fields you can't map or that should be skipped, set value to "".
For select/radio fields, pick the closest matching option from the available options.
Answer screening questions honestly based on the candidate profile.

Candidate:
{candidate_desc}

Cover letter (use for any cover letter / message field):
{cover_letter[:300]}

Form fields:
{fields_desc}

Job context: {job.title} at {job.company}"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=800,
            messages=[
                {"role": "system", "content": "You map candidate data to form fields. Return valid JSON array only. Be accurate and honest."},
                {"role": "user", "content": prompt},
            ],
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        mappings = json.loads(text)

        # Convert to FormField objects
        result = []
        field_map = {f.get("selector", ""): f for f in raw_fields}
        for m in mappings:
            selector = m.get("selector", "")
            value = m.get("value", "")
            if not selector:
                continue

            raw = field_map.get(selector, {})
            result.append(FormField(
                selector=selector,
                field_type="file" if value == "RESUME" else raw.get("type", "text"),
                label=raw.get("label", ""),
                name=raw.get("name", ""),
                required=raw.get("required", False),
                options=raw.get("options", []),
                value_to_fill="" if value == "RESUME" else value,
            ))
            # Mark file fields properly
            if value == "RESUME":
                result[-1].field_type = "file"

        return result

    except Exception as e:
        log.warning("GPT mapping failed: %s — falling back to keywords", e)
        return _map_with_keywords(raw_fields, candidate, cover_letter)


def _map_with_keywords(raw_fields: list[dict], candidate: dict, cover_letter: str) -> list[FormField]:
    """Fallback: map fields by keyword matching on labels/names."""
    keyword_map = {
        "first_name": ["first name", "first-name", "firstname", "vorname", "given name"],
        "last_name": ["last name", "last-name", "lastname", "nachname", "surname", "family name"],
        "email": ["email", "e-mail", "mail"],
        "phone": ["phone", "telefon", "tel", "mobile", "handy"],
        "linkedin_url": ["linkedin", "profile url"],
    }

    result = []
    for raw in raw_fields:
        label_lower = (raw.get("label", "") + " " + raw.get("name", "")).lower()
        value = ""

        # File input → resume
        if raw.get("type") == "file":
            result.append(FormField(
                selector=raw["selector"], field_type="file",
                label=raw.get("label", ""), name=raw.get("name", ""),
                required=raw.get("required", False), options=[],
                value_to_fill="",
            ))
            continue

        # Match by keywords
        for field_key, keywords in keyword_map.items():
            if any(kw in label_lower for kw in keywords):
                value = candidate.get(field_key, "")
                break

        # Cover letter / message fields
        if any(kw in label_lower for kw in ["cover letter", "message", "anschreiben", "motivation"]):
            value = cover_letter

        if value:
            result.append(FormField(
                selector=raw["selector"], field_type=raw.get("type", "text"),
                label=raw.get("label", ""), name=raw.get("name", ""),
                required=raw.get("required", False), options=raw.get("options", []),
                value_to_fill=value,
            ))

    return result
