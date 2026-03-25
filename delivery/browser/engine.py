"""
Playwright-based browser automation engine for form filling.

Uses GPT-4o-mini to interpret form fields and map candidate data.
Handles multi-step forms, file uploads, and CAPTCHA detection.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from datetime import datetime

from core.models import Job
from delivery.ats.base import ApplicationResult
from delivery.browser.captcha_detector import has_captcha
from delivery.browser.form_analyzer import extract_and_map_fields, FormField

log = logging.getLogger(__name__)


SCREENSHOTS_DIR = Path(__file__).parent.parent.parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)


async def _apply_with_browser(job: Job, candidate: dict, cover_letter: str,
                               resume_path: str, dry_run: bool,
                               cover_letter_pdf: str = "") -> ApplicationResult:
    """Core async browser apply logic."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return ApplicationResult(
            success=False, method="browser",
            message="Playwright not installed — run: pip install playwright && playwright install chromium",
            response_data={},
        )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )

        # Basic stealth: remove webdriver flag
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        page = await context.new_page()

        try:
            # Navigate to job page
            log.info("Navigating to %s", job.url)
            await page.goto(job.url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)  # Let JS render

            # Check for CAPTCHA
            if await has_captcha(page):
                screenshot_path = _screenshot_path(job, "captcha")
                await page.screenshot(path=str(screenshot_path))
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser",
                    message=f"CAPTCHA detected — screenshot saved to {screenshot_path.name}",
                    response_data={"screenshot": str(screenshot_path)},
                )

            # Try to find and click "Apply" button
            apply_clicked = await _click_apply_button(page)
            if apply_clicked:
                await page.wait_for_timeout(2000)

            # Check for CAPTCHA again after clicking apply
            if await has_captcha(page):
                screenshot_path = _screenshot_path(job, "captcha")
                await page.screenshot(path=str(screenshot_path))
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser",
                    message=f"CAPTCHA detected on apply form — screenshot saved",
                    response_data={"screenshot": str(screenshot_path)},
                )

            # Extract form fields and map to candidate data
            fields = await extract_and_map_fields(page, candidate, cover_letter, job)
            if not fields:
                screenshot_path = _screenshot_path(job, "no_form")
                await page.screenshot(path=str(screenshot_path))
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser",
                    message="No fillable form found on page",
                    response_data={"screenshot": str(screenshot_path)},
                )

            # Fill the form fields
            filled_count = await _fill_fields(page, fields, resume_path, cover_letter_pdf)
            log.info("Filled %d/%d fields", filled_count, len(fields))

            # Handle multi-step forms (up to 5 steps)
            for step in range(5):
                next_button = await _find_next_button(page)
                if not next_button:
                    break

                # Screenshot before clicking next
                if dry_run:
                    screenshot_path = _screenshot_path(job, f"step_{step}")
                    await page.screenshot(path=str(screenshot_path))

                await next_button.click()
                await page.wait_for_timeout(2000)

                # Check for new fields on the next step
                new_fields = await extract_and_map_fields(page, candidate, cover_letter, job)
                if new_fields:
                    filled = await _fill_fields(page, new_fields, resume_path, cover_letter_pdf)
                    log.info("Step %d: filled %d more fields", step + 2, filled)

            # Take pre-submit screenshot
            screenshot_path = _screenshot_path(job, "pre_submit")
            await page.screenshot(path=str(screenshot_path), full_page=True)

            if dry_run:
                await browser.close()
                return ApplicationResult(
                    success=True, method="browser",
                    message=f"DRY RUN — form filled, screenshot saved ({screenshot_path.name})",
                    response_data={"dry_run": True, "screenshot": str(screenshot_path), "fields_filled": filled_count},
                )

            # Find and click submit button
            submitted = await _click_submit_button(page)
            if submitted:
                await page.wait_for_timeout(3000)
                # Screenshot after submit
                screenshot_path = _screenshot_path(job, "post_submit")
                await page.screenshot(path=str(screenshot_path))

                # Verify submission result
                from delivery.browser.common import verify_submission
                has_error, error_msg = await verify_submission(page)
                if has_error:
                    await browser.close()
                    return ApplicationResult(
                        success=False, method="browser",
                        message=error_msg,
                        response_data={"screenshot": str(screenshot_path), "fields_filled": filled_count},
                    )

                await browser.close()
                return ApplicationResult(
                    success=True, method="browser",
                    message=f"Application submitted via browser to {job.company}",
                    response_data={"screenshot": str(screenshot_path), "fields_filled": filled_count},
                )
            else:
                await browser.close()
                return ApplicationResult(
                    success=False, method="browser",
                    message="Could not find submit button",
                    response_data={"screenshot": str(screenshot_path)},
                )

        except Exception as e:
            try:
                screenshot_path = _screenshot_path(job, "error")
                await page.screenshot(path=str(screenshot_path))
            except Exception:
                screenshot_path = None
            await browser.close()
            return ApplicationResult(
                success=False, method="browser",
                message=f"Browser error: {e}",
                response_data={"error": str(e), "screenshot": str(screenshot_path) if screenshot_path else ""},
            )


def _screenshot_path(job: Job, label: str) -> Path:
    """Generate a screenshot file path."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_company = "".join(c for c in job.company if c.isalnum() or c in " -_")[:30]
    return SCREENSHOTS_DIR / f"{safe_company}_{label}_{timestamp}.png"


async def _click_apply_button(page) -> bool:
    """Try to find and click an Apply button on the page."""
    selectors = [
        'button:has-text("Apply")',
        'a:has-text("Apply")',
        'button:has-text("Bewerben")',
        'a:has-text("Bewerben")',
        '[data-qa="apply-button"]',
        '.apply-button',
        '#apply-button',
        'a[href*="apply"]',
    ]
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=1000):
                await element.click()
                return True
        except Exception:
            continue
    return False


async def _find_next_button(page):
    """Find a Next/Continue button (for multi-step forms)."""
    selectors = [
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button:has-text("Weiter")',
        'button[type="button"]:has-text("Next")',
        '.btn-next',
    ]
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=1000):
                return element
        except Exception:
            continue
    return None


async def _click_submit_button(page) -> bool:
    """Find and click the final Submit button."""
    selectors = [
        'button:has-text("Submit")',
        'button:has-text("Apply")',
        'button:has-text("Send")',
        'button:has-text("Absenden")',
        'button:has-text("Bewerben")',
        'button[type="submit"]',
        'input[type="submit"]',
    ]
    for selector in selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=1000):
                await element.click()
                return True
        except Exception:
            continue
    return False


async def _fill_fields(page, fields: list[FormField], resume_path: str,
                       cover_letter_pdf: str = "") -> int:
    """Fill form fields on the page. Returns count of successfully filled fields."""
    filled = 0
    for field in fields:
        if not field.value_to_fill and field.field_type != "file":
            continue
        try:
            element = page.locator(field.selector).first
            if not await element.is_visible(timeout=1000):
                continue

            if field.field_type == "file":
                label_lower = field.label.lower()
                # Determine if this is a cover letter or resume upload
                is_cover_letter_field = any(kw in label_lower for kw in
                    ["cover letter", "anschreiben", "motivation", "letter"])
                if is_cover_letter_field and cover_letter_pdf:
                    cl_path = Path(cover_letter_pdf)
                    if cl_path.exists():
                        await element.set_input_files(str(cl_path))
                        filled += 1
                        continue
                # Default: upload resume
                resume = Path(resume_path) if resume_path else None
                if resume and resume.exists():
                    await element.set_input_files(str(resume))
                    filled += 1
            elif field.field_type == "select":
                await element.select_option(label=field.value_to_fill)
                filled += 1
            elif field.field_type in ("radio", "checkbox"):
                # Click the option that matches
                option = page.locator(f'{field.selector} >> text="{field.value_to_fill}"').first
                if await option.is_visible(timeout=500):
                    await option.click()
                    filled += 1
            elif field.field_type == "textarea":
                await element.fill(field.value_to_fill)
                filled += 1
            else:  # text, email, tel, etc.
                await element.fill(field.value_to_fill)
                filled += 1

            # Small delay between fields to look human
            await page.wait_for_timeout(300)

        except Exception:
            continue

    return filled


def _cleanup_old_screenshots():
    """Delete screenshots older than 7 days."""
    cutoff = time.time() - 7 * 86400
    for f in SCREENSHOTS_DIR.glob("*.png"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass


def browser_apply(job: Job, candidate: dict, cover_letter: str,
                  resume_path: str, dry_run: bool = True,
                  cover_letter_pdf: str = "") -> ApplicationResult:
    """Sync wrapper for async browser apply."""
    _cleanup_old_screenshots()
    return asyncio.run(_apply_with_browser(job, candidate, cover_letter, resume_path, dry_run, cover_letter_pdf))
