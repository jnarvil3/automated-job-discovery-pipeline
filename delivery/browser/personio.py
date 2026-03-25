"""
Personio-specific browser handler.

Personio career pages ({company}.jobs.personio.de/job/{id}) have a consistent
form structure that this handler exploits for higher fill-rate than the
generic browser engine.

Typical Personio form structure:
- Personal info: first name, last name, email, phone
- Documents section: CV upload, cover letter upload
- Optional custom questions
- GDPR / privacy consent checkbox
- Submit button
"""

import asyncio
from pathlib import Path

from core.models import Job
from delivery.ats.base import ApplicationResult
from delivery.browser.captcha_detector import has_captcha


# Personio-specific selectors (based on their standard career page templates)
APPLY_BUTTON_SELECTORS = [
    'a[data-test-id="apply-button"]',
    'button[data-test-id="apply-button"]',
    'a:has-text("Apply for this position")',
    'a:has-text("Apply now")',
    'button:has-text("Apply for this position")',
    'button:has-text("Apply now")',
    'a:has-text("Jetzt bewerben")',
    'a:has-text("Auf diese Stelle bewerben")',
    'a[href*="#apply"]',
    '.job-ad-display-apply-button',
]

FIELD_SELECTORS = {
    "first_name": [
        'input[name="first_name"]',
        'input[name="firstName"]',
        'input[data-test-id="first-name"]',
        'input[autocomplete="given-name"]',
        'input[placeholder*="First"]',
        'input[placeholder*="Vorname"]',
    ],
    "last_name": [
        'input[name="last_name"]',
        'input[name="lastName"]',
        'input[data-test-id="last-name"]',
        'input[autocomplete="family-name"]',
        'input[placeholder*="Last"]',
        'input[placeholder*="Nachname"]',
    ],
    "email": [
        'input[name="email"]',
        'input[type="email"]',
        'input[data-test-id="email"]',
        'input[autocomplete="email"]',
    ],
    "phone": [
        'input[name="phone"]',
        'input[type="tel"]',
        'input[data-test-id="phone"]',
        'input[autocomplete="tel"]',
    ],
}

RESUME_UPLOAD_SELECTORS = [
    'input[type="file"][name*="resume"]',
    'input[type="file"][name*="cv"]',
    'input[type="file"][name*="document"]',
    'input[type="file"][data-test-id*="resume"]',
    'input[type="file"][data-test-id*="cv"]',
    # Personio often uses a generic file input; grab the first one as resume
    'input[type="file"]',
]

COVER_LETTER_UPLOAD_SELECTORS = [
    'input[type="file"][name*="cover"]',
    'input[type="file"][name*="letter"]',
    'input[type="file"][name*="anschreiben"]',
    'input[type="file"][name*="motivation"]',
    'input[type="file"][data-test-id*="cover"]',
]

CONSENT_SELECTORS = [
    'input[type="checkbox"][name*="privacy"]',
    'input[type="checkbox"][name*="consent"]',
    'input[type="checkbox"][name*="gdpr"]',
    'input[type="checkbox"][name*="datenschutz"]',
    'input[type="checkbox"][data-test-id*="privacy"]',
    'input[type="checkbox"][data-test-id*="consent"]',
]

SUBMIT_SELECTORS = [
    'button[type="submit"]',
    'button[data-test-id="submit"]',
    'button:has-text("Submit application")',
    'button:has-text("Send application")',
    'button:has-text("Bewerbung absenden")',
    'button:has-text("Submit")',
    'button:has-text("Apply")',
    'input[type="submit"]',
]


async def _find_and_click(page, selectors: list[str], timeout: int = 2000) -> bool:
    """Try each selector in order; click the first visible one."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=timeout):
                await el.click()
                return True
        except Exception:
            continue
    return False


async def _fill_field(page, selectors: list[str], value: str, timeout: int = 1000) -> bool:
    """Try each selector in order; fill the first visible one."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=timeout):
                await el.fill(value)
                return True
        except Exception:
            continue
    return False


async def _upload_file(page, selectors: list[str], file_path: str,
                       exclude_selectors: list[str] | None = None,
                       timeout: int = 1000) -> bool:
    """Upload a file to the first matching visible file input."""
    exclude = set(exclude_selectors or [])
    for sel in selectors:
        if sel in exclude:
            continue
        try:
            el = page.locator(sel).first
            # File inputs may be hidden — check existence rather than visibility
            if await el.count() > 0:
                await el.set_input_files(file_path)
                return True
        except Exception:
            continue
    return False


async def _check_consent(page, timeout: int = 1000) -> bool:
    """Check the GDPR / privacy consent checkbox if present."""
    for sel in CONSENT_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=timeout):
                if not await el.is_checked():
                    await el.check()
                return True
        except Exception:
            continue
    return False


async def _personio_apply(job: Job, candidate: dict, cover_letter: str,
                          resume_path: str, dry_run: bool,
                          cover_letter_pdf: str = "") -> ApplicationResult:
    """Apply to a Personio-hosted job using Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ApplicationResult(
            success=False, method="browser_personio",
            message="Playwright not installed",
            response_data={},
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => false });"
        )
        page = await context.new_page()

        try:
            # Navigate to job page
            print(f"    [personio] Navigating to {job.url}")
            await page.goto(job.url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)

            # CAPTCHA check
            if await has_captcha(page):
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser_personio",
                    message="CAPTCHA detected on Personio page",
                    response_data={},
                )

            # Click the Apply button to open the form
            clicked = await _find_and_click(page, APPLY_BUTTON_SELECTORS)
            if clicked:
                await page.wait_for_timeout(2000)
            else:
                # Some Personio pages show the form inline — continue anyway
                print("    [personio] No apply button found — form may be inline")

            # CAPTCHA check after clicking apply
            if await has_captcha(page):
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser_personio",
                    message="CAPTCHA detected on apply form",
                    response_data={},
                )

            # Fill personal info fields
            filled = 0
            first = candidate.get("first_name", "")
            last = candidate.get("last_name", "")
            email = candidate.get("email", "")
            phone = candidate.get("phone", "")

            if first and await _fill_field(page, FIELD_SELECTORS["first_name"], first):
                filled += 1
            if last and await _fill_field(page, FIELD_SELECTORS["last_name"], last):
                filled += 1
            if email and await _fill_field(page, FIELD_SELECTORS["email"], email):
                filled += 1
            if phone and await _fill_field(page, FIELD_SELECTORS["phone"], phone):
                filled += 1

            # Upload resume
            resume = Path(resume_path) if resume_path else None
            resume_uploaded = False
            if resume and resume.exists():
                resume_uploaded = await _upload_file(page, RESUME_UPLOAD_SELECTORS, str(resume))
                if resume_uploaded:
                    filled += 1

            # Upload cover letter PDF (use a different file input than resume)
            cl_pdf = Path(cover_letter_pdf) if cover_letter_pdf else None
            if cl_pdf and cl_pdf.exists():
                # Try dedicated cover letter inputs first; fall back to second file input
                uploaded = await _upload_file(
                    page, COVER_LETTER_UPLOAD_SELECTORS, str(cl_pdf),
                )
                if uploaded:
                    filled += 1

            # Fill any cover letter text area (some Personio forms have one)
            if cover_letter:
                cover_letter_textareas = [
                    'textarea[name*="cover"]',
                    'textarea[name*="letter"]',
                    'textarea[name*="message"]',
                    'textarea[name*="motivation"]',
                    'textarea[placeholder*="cover"]',
                    'textarea[placeholder*="message"]',
                ]
                if await _fill_field(page, cover_letter_textareas, cover_letter):
                    filled += 1

            # Handle custom questions via the generic form analyzer (if GPT available)
            try:
                from delivery.browser.form_analyzer import extract_and_map_fields
                extra_fields = await extract_and_map_fields(page, candidate, cover_letter, job)
                if extra_fields:
                    from delivery.browser.engine import _fill_fields
                    extra_filled = await _fill_fields(
                        page, extra_fields, str(resume) if resume else "",
                        cover_letter_pdf,
                    )
                    filled += extra_filled
            except Exception:
                pass  # Not critical — the key fields are already filled

            # Check GDPR consent
            await _check_consent(page)

            print(f"    [personio] Filled {filled} fields")

            if filled == 0:
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser_personio",
                    message="Could not fill any form fields on Personio page",
                    response_data={"filled": 0},
                )

            if dry_run:
                await browser.close()
                return ApplicationResult(
                    success=True, method="browser_personio",
                    message=f"DRY RUN — Personio form filled ({filled} fields)",
                    response_data={"dry_run": True, "fields_filled": filled},
                )

            # Submit
            submitted = await _find_and_click(page, SUBMIT_SELECTORS)
            if submitted:
                await page.wait_for_timeout(3000)

                # Verify submission result
                body_text = (await page.inner_text("body")).lower()
                success_phrases = ["thank you", "received", "successfully", "submitted", "application has been"]
                error_phrases = ["required", "error", "invalid", "please fill", "mandatory"]
                has_success = any(p in body_text for p in success_phrases)
                has_error = any(p in body_text for p in error_phrases)

                if has_error and not has_success:
                    await browser.close()
                    return ApplicationResult(
                        success=False, method="browser_personio",
                        message="Form submit may have failed — error indicators found on page",
                        response_data={"fields_filled": filled},
                    )

                await browser.close()
                return ApplicationResult(
                    success=True, method="browser_personio",
                    message=f"Application submitted to {job.company} via Personio",
                    response_data={"fields_filled": filled},
                )
            else:
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser_personio",
                    message="Could not find submit button on Personio form",
                    response_data={"fields_filled": filled},
                )

        except Exception as e:
            await browser.close()
            return ApplicationResult(
                success=False, method="browser_personio",
                message=f"Personio browser error: {e}",
                response_data={"error": str(e)},
            )


def personio_apply(job: Job, candidate: dict, cover_letter: str,
                   resume_path: str, dry_run: bool = True,
                   cover_letter_pdf: str = "") -> ApplicationResult:
    """Sync wrapper for async Personio apply."""
    return asyncio.run(
        _personio_apply(job, candidate, cover_letter, resume_path, dry_run, cover_letter_pdf)
    )
