"""
tools.py — LangChain tool wrappers the agent can call.
Each tool wraps existing scraper/tailor/db logic — those modules are unchanged.
"""
import os
import re
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from groq import Groq
from langchain_core.tools import tool
from rich.console import Console
from rich.prompt import Confirm

load_dotenv(Path(__file__).parent.parent / ".env")

from . import db
from . import scraper as scraper_mod
from . import tailor as tailor_mod
from .applier import try_auto_apply, PORTAL_APPLY_SUPPORT
from resume.builder import render_pdf, resume_as_text

console = Console()
OUTPUTS_DIR = Path(__file__).parent.parent / "resume" / "outputs"


def _groq() -> Groq:
    return Groq(api_key=os.environ["GROQ_API_KEY"])


@tool
def search_jobs(
    query: str,
    location: str = "India",
    portals: Optional[List[str]] = None,
    limit: int = 10,
    min_score: int = 60,
    remote: bool = False,
) -> str:
    """
    Search for jobs across portals, score each with AI, and store matches in the database.
    portals choices: naukri, linkedin, indeed, ziprecruiter, dice. Defaults to naukri + linkedin.
    min_score filters out weak matches (0-100). Returns a summary of what was stored.
    """
    if portals is None:
        portals = ["naukri", "linkedin"]

    db.init_db()
    client = _groq()
    total = 0
    lines = []

    for portal in portals:
        if portal not in scraper_mod.PORTAL_FNS:
            lines.append(f"{portal}: unknown portal, skipped")
            continue
        n = scraper_mod.run_portal(
            portal, scraper_mod.PORTAL_FNS[portal],
            query, location, limit, remote, client, min_score,
        )
        total += n
        lines.append(f"{portal}: {n} job(s) stored (score >= {min_score})")

    return f"Search complete. {total} total job(s) stored above score {min_score}.\n" + "\n".join(lines)


@tool
def list_jobs(min_score: int = 60, status: str = "new") -> str:
    """
    List jobs stored in the database. status can be 'new', 'reviewed', or 'skipped'.
    Returns job IDs, titles, companies, relevance scores, and match reasons.
    Use job IDs from this output when calling tailor_resume.
    """
    db.init_db()
    with db.get_db() as conn:
        jobs = [dict(j) for j in db.get_jobs(conn, status=status, min_score=min_score)]

    if not jobs:
        return f"No jobs found with status='{status}' and score >= {min_score}."

    lines = [f"{len(jobs)} job(s) found (score >= {min_score}, status='{status}'):\n"]
    for j in jobs:
        lines.append(
            f"  ID={j['id']} | {j['title']} @ {j['company']} "
            f"| Score={j['relevance_score']} | {j['portal']} | {j['location']}\n"
            f"    Reason: {j['relevance_reason']}"
        )
    return "\n".join(lines)


def _do_tailor(job_id: str) -> str:
    """
    Run the full tailoring pipeline without a confirmation prompt.
    Used by the dashboard (button click = confirmation) and the CLI @tool (after Confirm.ask).
    """
    db.init_db()
    with db.get_db() as conn:
        job = db.get_job(conn, job_id)
    if not job:
        return f"Job ID '{job_id}' not found. Use list_jobs to get valid IDs."
    job = dict(job)

    try:
        client = _groq()
        resume_text = resume_as_text()
        gap = tailor_mod.analyse_gap(job["jd_text"] or "", resume_text, client)
        patched = tailor_mod.apply_patches(gap)
        tailor_mod.print_diff(gap, patched)

        company_slug = re.sub(r"[^a-zA-Z0-9]", "_", job["company"])
        today = date.today().strftime("%Y%m%d")
        pdf_path = str(OUTPUTS_DIR / f"Nehal_Mehta_{company_slug}_{today}.pdf")

        render_pdf(pdf_path, **patched)
        console.print(f"  [green]✓[/green] PDF: {pdf_path}")

        keywords = gap.get("missing_keywords", [])
        ats_passed, missing_kw = True, []
        if keywords:
            ats_passed, _, missing_kw = tailor_mod.verify_ats(pdf_path, keywords)
            if ats_passed:
                console.print("  [green]ATS: all keywords verified.[/green]")
            else:
                console.print(f"  [yellow]ATS: {len(missing_kw)} keyword(s) missing: {missing_kw}[/yellow]")

        app_id = str(uuid.uuid4())[:8]
        with db.get_db() as conn:
            db.insert_application(conn, {
                "id": app_id,
                "job_id": job["id"],
                "company": job["company"],
                "role": job["title"],
                "portal": job["portal"],
                "applied_date": today,
                "status": "applied",
                "resume_file": pdf_path,
                "jd_url": job["jd_url"],
                "last_email_update": None,
                "notes": f"ATS {'passed' if ats_passed else f'partial — missing: {missing_kw}'}",
                "created_at": db.now_iso(),
                "apply_method": "manual",
            })
            db.log_tailor(conn, job["id"], job["company"], pdf_path, ats_passed)

        return (
            f"Resume tailored for {job['company']} — {job['title']}.\n"
            f"PDF: {pdf_path}\n"
            f"Application ID: {app_id}\n"
            f"ATS: {'PASSED' if ats_passed else f'PARTIAL — missing {missing_kw}'}\n"
            f"Apply at: {job['jd_url'] or 'URL not available'}"
        )

    except Exception as e:
        return f"Tailoring failed for {job.get('company', '?')}: {e}"


@tool
def tailor_resume(job_id: str) -> str:
    """
    Generate a tailored resume PDF for a specific job.
    Runs Groq gap analysis, patches resume content in memory, renders PDF, and checks ATS keywords.
    Always asks the user for confirmation before generating the PDF.
    Logs the application to the database after tailoring.
    """
    db.init_db()
    with db.get_db() as conn:
        job = db.get_job(conn, job_id)
    if not job:
        return f"Job ID '{job_id}' not found. Use list_jobs to get valid IDs."
    job = dict(job)

    console.print(
        f"\n[bold cyan]Tailor resume for:[/bold cyan] {job['title']} @ {job['company']} "
        f"[Score: {job['relevance_score']}]"
    )
    console.print(f"  [dim]{job['relevance_reason']}[/dim]")
    console.print(f"  [dim]Portal: {job['portal']} | {job['location']}[/dim]")

    if not Confirm.ask("  Proceed with tailoring?", default=True):
        return f"Tailoring declined by user for {job['company']} — {job['title']}."

    return _do_tailor(job_id)


@tool
def update_application(app_id: str, status: Optional[str] = None, notes: Optional[str] = None) -> str:
    """
    Update the status or notes for a job application by its application ID.
    Valid statuses: applied, screening, interview, offer, rejected, ghosted.
    """
    valid = ["applied", "screening", "interview", "offer", "rejected", "ghosted"]
    if status and status not in valid:
        return f"Invalid status '{status}'. Valid options: {', '.join(valid)}"

    fields: dict = {}
    if status:
        fields["status"] = status
    if notes:
        fields["notes"] = notes
    if not fields:
        return "Nothing to update — provide status and/or notes."

    db.init_db()
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM applications WHERE id=?", (app_id,)).fetchone()
        if not row:
            return f"Application ID '{app_id}' not found."
        db.update_application(conn, app_id, **fields)

    return f"Application {app_id} updated: {fields}"


@tool
def get_stats() -> str:
    """
    Get pipeline statistics: total applications by status, jobs in database, scrape run count.
    Use this to get an overview of the job search progress.
    """
    db.init_db()
    with db.get_db() as conn:
        apps = [dict(a) for a in db.get_applications(conn)]
        jobs_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        scrape_runs = conn.execute(
            "SELECT COUNT(*) FROM scrape_logs WHERE error IS NULL"
        ).fetchone()[0]

    if not apps:
        return f"No applications yet. {jobs_count} job(s) in DB. {scrape_runs} scrape run(s)."

    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1

    lines = [f"Pipeline ({len(apps)} total applications, {jobs_count} jobs in DB, {scrape_runs} scrape run(s)):"]
    for s, n in sorted(by_status.items(), key=lambda x: -x[1]):
        lines.append(f"  {s}: {n}")
    return "\n".join(lines)


@tool
def apply_job(job_id: str) -> str:
    """
    Attempt to auto-apply to a job that has already been tailored.
    Supported portals: naukri (full), linkedin/indeed (Easy Apply only).
    For unsupported portals or automation failures, returns the manual apply URL.
    Requires a saved session: run `python -m job_agent.applier setup <portal>` first.
    """
    db.init_db()
    with db.get_db() as conn:
        job = db.get_job(conn, job_id)
        if not job:
            return f"Job '{job_id}' not found. Use list_jobs to get valid IDs."
        job = dict(job)
        app_row = db.get_application_by_job(conn, job_id)

    portal  = job.get("portal", "").lower()
    support = PORTAL_APPLY_SUPPORT.get(portal, "none")

    if support == "none":
        return (
            f"Auto-apply not supported for {portal}. "
            f"Apply manually at: {job.get('jd_url') or 'URL not available'}"
        )

    if not app_row:
        return f"No tailored resume found for job '{job_id}'. Run tailor_resume first, then retry apply_job."

    resume_path = dict(app_row).get("resume_file", "")
    if not resume_path or not Path(resume_path).is_file():
        return f"Resume PDF not found at '{resume_path}'. Re-run tailor_resume to regenerate it."

    console.print(f"[cyan]Auto-applying to {job['company']} via {portal}…[/cyan]")
    result = try_auto_apply(job, resume_path)

    if result.success:
        with db.get_db() as conn:
            db.update_application(conn, dict(app_row)["id"], apply_method="auto")
        return (
            f"Auto-apply succeeded for {job['company']} — {job['title']}.\n"
            f"Method: {result.method} | {result.message}"
        )
    return (
        f"Auto-apply could not complete for {job['company']} — {job['title']}.\n"
        f"Reason: {result.message}\n"
        f"Apply manually at: {result.jd_url or 'URL not available'}"
    )


ALL_TOOLS = [search_jobs, list_jobs, tailor_resume, update_application, get_stats, apply_job]
