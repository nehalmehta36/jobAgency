"""
tailor.py — gap-analyse a JD against the resume, patch content variables,
render a tailored PDF, verify ATS keyword coverage, and log the run.

Usage:
    python tailor.py --job-id <id>            # full run
    python tailor.py --job-id <id> --dry-run  # show diff only, no PDF
"""
import argparse
import copy
import json
import os
import re
import sys
import uuid
from datetime import date
from pathlib import Path

import pdfplumber
from dotenv import load_dotenv
from groq import Groq
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

load_dotenv(Path(__file__).parent.parent / ".env")

from . import db
from resume.builder import (
    SUMMARY, SJ_BULLETS, AC_BULLETS, SKILLS,
    render_pdf, resume_as_text,
)

console = Console()
OUTPUTS_DIR = Path(__file__).parent.parent / "resume" / "outputs"
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")


# ── Groq gap analysis ─────────────────────────────────────────────────────

GAP_PROMPT = """You are a senior technical recruiter helping a candidate tailor their resume.

Compare this JD against the candidate's resume and return ONLY a JSON object with these exact keys:

{{
  "missing_keywords": ["kw1", "kw2", "kw3", "kw4", "kw5"],
  "reorder_skills": ["category1", "category2", "category3"],
  "summary_update": "One complete replacement sentence or null",
  "bullet_tweaks": [
    {{"original": "exact first ~8 words of the bullet to identify it", "revised": "complete new bullet text"}},
    ...
  ]
}}

Rules:
- missing_keywords: max 5 keywords that appear prominently in the JD but are absent or weak in the resume
- reorder_skills: list the 1-3 SKILLS categories that should move to the top to match JD priorities (use exact category names from resume)
- summary_update: a single revised summary sentence to prepend or replace; keep it under 60 words; null if no change needed
- bullet_tweaks: max 2 bullets to rephrase; "original" must be the first 8+ words verbatim so we can match it; keep factual data (numbers, company names, dates) unchanged
- NEVER change job titles, company names, dates, or metrics
- NEVER invent new experience

JD:
{jd_text}

Candidate resume:
{resume_text}"""


def analyse_gap(jd_text: str, resume_text: str, client: Groq) -> dict:
    prompt = GAP_PROMPT.format(jd_text=jd_text, resume_text=resume_text)
    resp = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    raw = resp.choices[0].message.content
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Extract JSON object if wrapped in markdown
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
        raise ValueError(f"Could not parse Groq response as JSON:\n{raw}")


# ── Patch content variables ───────────────────────────────────────────────

def apply_patches(gap: dict) -> dict:
    """Return a dict of patched content variables (deep-copies, originals untouched)."""
    patched = {
        "summary": SUMMARY,
        "sj_bullets": copy.deepcopy(SJ_BULLETS),
        "ac_bullets": copy.deepcopy(AC_BULLETS),
        "skills": copy.deepcopy(SKILLS),
    }

    # Summary
    if gap.get("summary_update"):
        new_sentence = gap["summary_update"].strip()
        # Prepend the new focus sentence to the existing summary
        patched["summary"] = new_sentence + " " + SUMMARY

    # Bullet tweaks — match by first ~8 words
    for tweak in gap.get("bullet_tweaks", []):
        original_start = tweak.get("original", "").strip().lower()
        revised = tweak.get("revised", "").strip()
        if not original_start or not revised:
            continue
        # Search SJ then AC bullets
        for bucket in ("sj_bullets", "ac_bullets"):
            for i, b in enumerate(patched[bucket]):
                if b.lower().startswith(original_start.lower()[:50]):
                    patched[bucket][i] = revised
                    break

    # Inject missing keywords into summary (append a skill-focused clause)
    keywords = gap.get("missing_keywords", [])
    if keywords:
        kw_str = ", ".join(keywords)
        patched["summary"] = patched["summary"].rstrip(".") + (
            f". Additional expertise: {kw_str}."
        )

    # Reorder skills: move priority categories to top
    priority_cats = gap.get("reorder_skills", [])
    if priority_cats:
        reordered = {}
        for cat in priority_cats:
            if cat in patched["skills"]:
                reordered[cat] = patched["skills"][cat]
        for cat, val in patched["skills"].items():
            if cat not in reordered:
                reordered[cat] = val
        patched["skills"] = reordered

    return patched


# ── ATS verification ──────────────────────────────────────────────────────

def verify_ats(pdf_path: str, keywords: list[str]) -> tuple[bool, list[str], list[str]]:
    """Return (passed, found, missing) for the given keyword list."""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = " ".join(p.extract_text() or "" for p in pdf.pages).lower()

    found, missing = [], []
    for kw in keywords:
        if kw.lower() in full_text:
            found.append(kw)
        else:
            missing.append(kw)

    passed = len(missing) == 0
    return passed, found, missing


# ── CLI diff display ──────────────────────────────────────────────────────

def print_diff(gap: dict, patched: dict):
    console.print(Panel("[bold cyan]Gap Analysis Result[/bold cyan]"))

    t = Table(show_header=True, header_style="bold magenta", box=None)
    t.add_column("Field", style="cyan", no_wrap=True)
    t.add_column("Value")
    t.add_row("Missing keywords", ", ".join(gap.get("missing_keywords", [])) or "—")
    t.add_row("Skills to reorder", ", ".join(gap.get("reorder_skills", [])) or "—")
    t.add_row("Summary update", gap.get("summary_update") or "—")
    console.print(t)

    tweaks = gap.get("bullet_tweaks", [])
    if tweaks:
        console.print("\n[bold]Bullet tweaks:[/bold]")
        for tw in tweaks:
            console.print(f"  [red]- {tw.get('original','')[:80]}...[/red]")
            console.print(f"  [green]+ {tw.get('revised','')[:120]}[/green]\n")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Tailor resume to a specific job")
    parser.add_argument("--job-id", required=True, help="Job ID from the jobs table")
    parser.add_argument("--dry-run", action="store_true", help="Show diff without generating PDF")
    args = parser.parse_args()

    db.init_db()
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    with db.get_db() as conn:
        job = db.get_job(conn, args.job_id)

    if not job:
        console.print(f"[red]Job ID '{args.job_id}' not found in database.[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Tailoring for:[/bold] {job['title']} @ {job['company']}")
    console.print(f"[dim]Portal:[/dim] {job['portal']}  |  [dim]Score:[/dim] {job['relevance_score']}/100")

    # Step 1 — Gap analysis
    console.print("\n[yellow]Step 1/5:[/yellow] Analysing JD vs resume...")
    resume_text = resume_as_text()
    gap = analyse_gap(job["jd_text"] or "", resume_text, client)

    # Step 2 — Patch variables
    console.print("[yellow]Step 2/5:[/yellow] Applying patches...")
    patched = apply_patches(gap)

    print_diff(gap, patched)

    if args.dry_run:
        console.print("\n[bold yellow]Dry-run mode — no PDF generated.[/bold yellow]")
        return

    # Step 3 — Export PDF
    console.print("[yellow]Step 3/5:[/yellow] Rendering PDF...")
    company_slug = re.sub(r"[^a-zA-Z0-9]", "_", job["company"])
    today = date.today().strftime("%Y%m%d")
    pdf_path = str(OUTPUTS_DIR / f"Nehal_Mehta_{company_slug}_{today}.pdf")

    render_pdf(
        pdf_path,
        summary=patched["summary"],
        sj_bullets=patched["sj_bullets"],
        ac_bullets=patched["ac_bullets"],
        skills=patched["skills"],
    )
    console.print(f"  [green]✓[/green] Saved: {pdf_path}")

    # Step 4 — ATS verification
    console.print("[yellow]Step 4/5:[/yellow] Verifying ATS coverage...")
    keywords_to_check = (gap.get("missing_keywords") or []) + [
        kw.strip()
        for kw_group in patched["skills"].values()
        for kw in kw_group.split("·")
        if len(kw.strip()) > 3
    ][:15]

    passed, found, missing = verify_ats(pdf_path, keywords_to_check)
    ats_table = Table(show_header=True, header_style="bold", box=None)
    ats_table.add_column("Status", width=8)
    ats_table.add_column("Keyword")
    for kw in found:
        ats_table.add_row("[green]PASS[/green]", kw)
    for kw in missing:
        ats_table.add_row("[red]MISS[/red]", kw)
    console.print(ats_table)
    if passed:
        console.print("[green]ATS check PASSED — all patched keywords found in PDF.[/green]")
    else:
        console.print(f"[yellow]ATS check: {len(missing)} keyword(s) not found in PDF.[/yellow]")

    # Step 5 — Log to tracker
    console.print("[yellow]Step 5/5:[/yellow] Logging application...")
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
            "notes": f"ATS {'passed' if passed else 'partial'}; missing: {missing}",
            "created_at": db.now_iso(),
            "apply_method": "manual",
        })
        db.log_tailor(conn, job["id"], job["company"], pdf_path, passed)

    console.print(f"\n[bold green]Done![/bold green] Application ID: [cyan]{app_id}[/cyan]")
    console.print(f"PDF: [underline]{pdf_path}[/underline]")
    console.print(f"Apply at: [underline]{job['jd_url'] or 'URL not available'}[/underline]")


if __name__ == "__main__":
    main()
