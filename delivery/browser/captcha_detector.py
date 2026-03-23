"""
CAPTCHA detection — bail gracefully, never try to solve.
"""


async def has_captcha(page) -> bool:
    """Check if the current page has a CAPTCHA challenge."""
    captcha_selectors = [
        'iframe[src*="recaptcha"]',
        'iframe[src*="hcaptcha"]',
        'iframe[src*="turnstile"]',
        '.g-recaptcha',
        '.h-captcha',
        '#captcha',
        '[data-captcha]',
        'iframe[title*="reCAPTCHA"]',
        'iframe[title*="hCaptcha"]',
    ]

    for selector in captcha_selectors:
        try:
            element = page.locator(selector).first
            if await element.is_visible(timeout=500):
                return True
        except Exception:
            continue

    # Also check page text for CAPTCHA indicators
    try:
        text = await page.inner_text("body")
        captcha_phrases = [
            "verify you are human",
            "i'm not a robot",
            "complete the captcha",
            "security check",
        ]
        text_lower = text.lower()
        for phrase in captcha_phrases:
            if phrase in text_lower:
                return True
    except Exception:
        pass

    return False
