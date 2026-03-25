"""Shared browser verification logic for post-submit checks."""

SUCCESS_PHRASES = ["thank you", "received", "successfully", "submitted", "application has been"]
ERROR_PHRASES = ["required", "error", "invalid", "please fill", "mandatory"]


async def verify_submission(page) -> tuple[bool, str]:
    """Check the page body for success/error indicators after form submission.

    Returns:
        (has_error, description) — has_error is True when error phrases are found
        without any success phrases, indicating the submission likely failed.
    """
    body_text = (await page.inner_text("body")).lower()
    has_success = any(p in body_text for p in SUCCESS_PHRASES)
    has_error = any(p in body_text for p in ERROR_PHRASES)

    if has_error and not has_success:
        return True, "Form submit may have failed — error indicators found on page"
    return False, ""
