"""
applier.py — Playwright-based auto-apply dispatcher.

Portal support:
  naukri        → full auto-apply (saved session + form automation)
  linkedin      → conditional (Easy Apply jobs only; detected at runtime)
  indeed        → conditional (Indeed Apply jobs only; detected at runtime)
  ziprecruiter  → none (manual redirect only)
  dice          → none (manual redirect only)

Session setup (one-time per portal):
  python -m job_agent.applier setup naukri
  python -m job_agent.applier setup linkedin
  python -m job_agent.applier setup indeed
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # playwright not installed in this environment
    sync_playwright = None  # type: ignore[assignment]

SESSIONS_DIR = Path(__file__).parent.parent / "data" / "sessions"

PORTAL_APPLY_SUPPORT: dict[str, str] = {
    "naukri":       "full",         # Always auto-applied via Playwright
    "linkedin":     "conditional",  # Only if Easy Apply button found
    "indeed":       "conditional",  # Only if Indeed Apply button found
    "ziprecruiter": "none",         # No auto-apply
    "dice":         "none",         # No auto-apply
}

# DOM selectors — stable anchors preferred (IDs, data-* attrs) over class names
_NAUKRI_APPLY     = "button#apply-button, [data-ga-track='Apply'], button.styles_apply-button__N9pPS"
_NAUKRI_UPLOAD    = "input[type='file']"
_NAUKRI_SUBMIT    = "button[type='submit']"

_LINKEDIN_EASY    = "button.jobs-apply-button, button[aria-label*='Easy Apply']"
_LINKEDIN_NEXT    = "button[aria-label='Continue to next step'], footer button.artdeco-button--primary"
_LINKEDIN_SUBMIT  = "button[aria-label='Submit application']"
_LINKEDIN_LOGIN   = "input#session_key"

_INDEED_APPLY_BTN = "[data-indeed-apply-buttontext], button#indeedApplyButton"
_INDEED_CONTINUE  = "button[data-testid='ia-continueButton'], button[type='submit']"
_INDEED_RESUME    = "input[type='radio'][data-testid='FileResumeCard-radio']"

_LOGIN_URLS = {
    "naukri":   "https://www.naukri.com/nlogin/login",
    "linkedin": "https://www.linkedin.com/login",
    "indeed":   "https://secure.indeed.com/account/login",
}


@dataclass
class ApplyResult:
    success: bool
    method: str    # "auto" | "manual_fallback" | "unsupported"
    message: str
    jd_url: str

    def to_dict(self) -> dict:
        return asdict(self)


# ── Session helpers ───────────────────────────────────────────────────────────

def _session_path(portal: str) -> Path:
    return SESSIONS_DIR / portal / "state.json"


def _session_exists(portal: str) -> bool:
    return _session_path(portal).is_file()


def _save_session(context, portal: str) -> None:
    path = _session_path(portal)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(context.storage_state(), indent=2))


def _load_session_kwargs(portal: str) -> dict:
    p = _session_path(portal)
    return {"storage_state": str(p)} if p.is_file() else {}


# ── Per-portal apply functions ────────────────────────────────────────────────

def _apply_naukri(page, job: dict, resume_path: str) -> ApplyResult:
    jd_url = job.get("jd_url", "")
    try:
        page.goto(jd_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2_000)

        if page.locator("a[href*='nlogin']").first.is_visible():
            return ApplyResult(False, "manual_fallback",
                "Naukri session expired. Run: python -m job_agent.applier setup naukri", jd_url)

        btn = page.locator(_NAUKRI_APPLY).first
        if not btn.is_visible():
            return ApplyResult(False, "manual_fallback", "Apply button not found on Naukri page.", jd_url)

        btn.click()
        page.wait_for_timeout(2_000)

        upload = page.locator(_NAUKRI_UPLOAD).first
        if upload.is_visible():
            upload.set_input_files(resume_path)
            page.wait_for_timeout(1_500)

        submit = page.locator(_NAUKRI_SUBMIT).first
        if submit.is_visible():
            submit.click()
            page.wait_for_timeout(2_500)

        body = page.inner_text("body").lower()
        if any(t in body for t in ["applied successfully", "application submitted", "you have applied"]):
            return ApplyResult(True, "auto", f"Applied to {job['company']} on Naukri.", jd_url)

        # Steps completed but no explicit confirmation — likely succeeded
        return ApplyResult(True, "auto",
            f"Naukri apply steps completed for {job['company']} — verify in your Naukri account.", jd_url)

    except Exception as e:
        return ApplyResult(False, "manual_fallback", f"Naukri apply error: {e}", jd_url)


def _apply_linkedin(page, job: dict, resume_path: str) -> ApplyResult:
    jd_url = job.get("jd_url", "")
    try:
        page.goto(jd_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2_000)

        if page.locator(_LINKEDIN_LOGIN).is_visible():
            return ApplyResult(False, "manual_fallback",
                "LinkedIn session expired. Run: python -m job_agent.applier setup linkedin", jd_url)

        easy_apply = page.locator(_LINKEDIN_EASY).first
        if not easy_apply.is_visible():
            return ApplyResult(False, "manual_fallback",
                "LinkedIn job has no Easy Apply — redirects to company site.", jd_url)

        easy_apply.click()
        page.wait_for_timeout(2_000)

        for _ in range(8):
            file_input = page.locator("input[type='file']").first
            if file_input.is_visible():
                file_input.set_input_files(resume_path)
                page.wait_for_timeout(1_000)

            submit = page.locator(_LINKEDIN_SUBMIT).first
            if submit.is_visible():
                submit.click()
                page.wait_for_timeout(2_500)
                return ApplyResult(True, "auto", f"Applied to {job['company']} via LinkedIn Easy Apply.", jd_url)

            next_btn = page.locator(_LINKEDIN_NEXT).first
            if next_btn.is_visible():
                next_btn.click()
                page.wait_for_timeout(1_500)
                continue
            break

        return ApplyResult(False, "manual_fallback",
            "LinkedIn Easy Apply modal did not reach Submit — complete manually.", jd_url)

    except Exception as e:
        return ApplyResult(False, "manual_fallback", f"LinkedIn apply error: {e}", jd_url)


def _apply_indeed(page, job: dict, resume_path: str) -> ApplyResult:
    jd_url = job.get("jd_url", "")
    try:
        page.goto(jd_url, wait_until="domcontentloaded", timeout=30_000)
        page.wait_for_timeout(2_000)

        ia_btn = page.locator(_INDEED_APPLY_BTN).first
        if not ia_btn.is_visible():
            return ApplyResult(False, "manual_fallback",
                "Indeed job uses external company application — apply manually.", jd_url)

        ia_btn.click()
        page.wait_for_timeout(2_000)

        radio = page.locator(_INDEED_RESUME).first
        if radio.is_visible():
            radio.click()
            page.wait_for_timeout(500)

        for _ in range(6):
            upload = page.locator("input[type='file']").first
            if upload.is_visible():
                upload.set_input_files(resume_path)
                page.wait_for_timeout(1_000)

            cont = page.locator(_INDEED_CONTINUE).first
            if cont.is_visible():
                label = cont.inner_text().lower()
                cont.click()
                page.wait_for_timeout(2_000)
                if "submit" in label or "apply" in label:
                    return ApplyResult(True, "auto", f"Applied to {job['company']} via Indeed Apply.", jd_url)
                continue
            break

        body = page.inner_text("body").lower()
        if "application submitted" in body or "you applied" in body:
            return ApplyResult(True, "auto", f"Applied to {job['company']} via Indeed (confirmed).", jd_url)

        return ApplyResult(False, "manual_fallback",
            "Indeed Apply steps did not complete — finish manually.", jd_url)

    except Exception as e:
        return ApplyResult(False, "manual_fallback", f"Indeed apply error: {e}", jd_url)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def try_auto_apply(job: dict, resume_path: str) -> ApplyResult:
    """
    Public entry point. Checks portal support, loads saved session,
    launches headless Playwright, and delegates to the per-portal function.

    Args:
        job:         Dict with keys: portal, jd_url, company, title.
        resume_path: Absolute path to the tailored PDF.

    Returns:
        ApplyResult(success, method, message, jd_url).
    """
    portal  = str(job.get("portal") or "").lower()
    jd_url  = str(job.get("jd_url") or "")
    support = PORTAL_APPLY_SUPPORT.get(portal, "none")

    if support == "none":
        return ApplyResult(False, "unsupported",
            f"Auto-apply not supported for {portal}. Apply manually at: {jd_url}", jd_url)

    if not Path(resume_path).is_file():
        return ApplyResult(False, "manual_fallback",
            f"Resume PDF not found at '{resume_path}'. Run tailor_resume first.", jd_url)

    if sync_playwright is None:
        return ApplyResult(False, "manual_fallback",
            "playwright is not installed. Run: pip install playwright && playwright install chromium", jd_url)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            **_load_session_kwargs(portal),
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        try:
            if portal == "naukri":
                result = _apply_naukri(page, job, resume_path)
            elif portal == "linkedin":
                result = _apply_linkedin(page, job, resume_path)
            elif portal == "indeed":
                result = _apply_indeed(page, job, resume_path)
            else:
                result = ApplyResult(False, "unsupported", f"No apply function for {portal}.", jd_url)

            if result.method != "unsupported":
                try:
                    _save_session(context, portal)  # refresh saved cookies
                except Exception as _e:
                    print(f"[applier] Warning: could not save session for {portal}: {_e}")
        finally:
            context.close()
            browser.close()

    return result


# ── Session setup CLI ─────────────────────────────────────────────────────────

def setup_session(portal: str) -> None:
    if portal not in _LOGIN_URLS:
        print(f"'{portal}' does not support session-based auto-apply.")
        sys.exit(1)

    if sync_playwright is None:
        print("playwright is not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    print(f"\n[applier] Opening browser for {portal}. Log in, then press Enter here.")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        context = browser.new_context()
        context.new_page().goto(_LOGIN_URLS[portal])
        input("\n[applier] Press Enter after logging in...")
        _save_session(context, portal)
        context.close()
        browser.close()
    print(f"[applier] Session saved → {_session_path(portal)}")


def main():
    parser = argparse.ArgumentParser(description="Job Agent — auto-apply session manager")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("setup", help="Save a login session for a portal")
    p.add_argument("portal", choices=list(_LOGIN_URLS.keys()))
    args = parser.parse_args()
    if args.command == "setup":
        setup_session(args.portal)


if __name__ == "__main__":
    main()
