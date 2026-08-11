"""
tracker.py — SQLite-backed application tracker with Gmail integration.

Usage:
    python tracker.py list
    python tracker.py list --status interview
    python tracker.py update <id> --status offer
    python tracker.py update <id> --notes "Recruiter call scheduled Thu 3pm"
    python tracker.py check-emails
    python tracker.py stats
"""
import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from groq import Groq
from rich.console import Console
from rich.table import Table
from rich import box

load_dotenv(Path(__file__).parent.parent / ".env")
from . import db

console = Console()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

VALID_STATUSES = ["applied", "screening", "interview", "offer", "rejected", "ghosted"]

STATUS_COLORS = {
    "applied":   "cyan",
    "screening": "yellow",
    "interview": "bold green",
    "offer":     "bold magenta",
    "rejected":  "red",
    "ghosted":   "dim",
}


# ── list ──────────────────────────────────────────────────────────────────

def cmd_list(status: str = None):
    db.init_db()
    with db.get_db() as conn:
        apps = db.get_applications(conn, status)

    if not apps:
        console.print("[yellow]No applications found.[/yellow]")
        return

    t = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold cyan")
    t.add_column("ID", style="dim", width=9)
    t.add_column("Company", width=22)
    t.add_column("Role", width=28)
    t.add_column("Portal", width=12)
    t.add_column("Applied", width=11)
    t.add_column("Status", width=12)
    t.add_column("Notes", width=35)

    for a in apps:
        color = STATUS_COLORS.get(a["status"], "white")
        t.add_row(
            a["id"],
            a["company"] or "—",
            (a["role"] or "")[:27],
            a["portal"] or "—",
            a["applied_date"] or "—",
            f"[{color}]{a['status']}[/{color}]",
            (a["notes"] or "")[:34],
        )

    console.print(t)
    console.print(f"  [dim]{len(apps)} application(s)[/dim]")


# ── update ────────────────────────────────────────────────────────────────

def cmd_update(app_id: str, status: str = None, notes: str = None):
    db.init_db()
    if status and status not in VALID_STATUSES:
        console.print(f"[red]Invalid status '{status}'. Choose from: {', '.join(VALID_STATUSES)}[/red]")
        sys.exit(1)

    fields = {}
    if status:
        fields["status"] = status
    if notes:
        fields["notes"] = notes

    if not fields:
        console.print("[yellow]Nothing to update — provide --status or --notes.[/yellow]")
        return

    with db.get_db() as conn:
        app = conn.execute("SELECT * FROM applications WHERE id=?", (app_id,)).fetchone()
        if not app:
            console.print(f"[red]Application '{app_id}' not found.[/red]")
            sys.exit(1)
        db.update_application(conn, app_id, **fields)

    console.print(f"[green]Updated {app_id}:[/green] {fields}")


# ── stats ─────────────────────────────────────────────────────────────────

def cmd_stats():
    db.init_db()
    with db.get_db() as conn:
        apps = db.get_applications(conn)
        jobs_count = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        scrape_logs = conn.execute(
            "SELECT COUNT(*) FROM scrape_logs WHERE error IS NULL"
        ).fetchone()[0]

    if not apps:
        console.print("[yellow]No applications yet.[/yellow]")
        return

    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a["status"]] = by_status.get(a["status"], 0) + 1

    by_portal: dict[str, int] = {}
    for a in apps:
        p = a["portal"] or "unknown"
        by_portal[p] = by_portal.get(p, 0) + 1

    t = Table(title="Application Stats", box=box.SIMPLE)
    t.add_column("Status")
    t.add_column("Count", justify="right")
    for status in VALID_STATUSES:
        n = by_status.get(status, 0)
        if n:
            color = STATUS_COLORS.get(status, "white")
            t.add_row(f"[{color}]{status}[/{color}]", str(n))
    console.print(t)

    console.print(f"\n[bold]Total applications:[/bold] {len(apps)}")
    console.print(f"[bold]Jobs in DB:[/bold] {jobs_count}")
    console.print(f"[bold]Successful scrape runs:[/bold] {scrape_logs}")

    if by_portal:
        console.print("\n[bold]By portal:[/bold]")
        for p, n in sorted(by_portal.items(), key=lambda x: -x[1]):
            console.print(f"  {p}: {n}")


# ── Gmail integration ─────────────────────────────────────────────────────

EMAIL_CLASSIFY_PROMPT = """You are classifying a recruiter email for a job application.

Company: {company}
Role: {role}
Email subject: {subject}
Email snippet: {snippet}

Return ONLY a JSON object:
{{"classification": "<one of: interview_invite | rejection | follow_up_needed | offer | other>", "summary": "<one sentence>"}}"""


def _get_gmail_service():
    """Build and return Gmail API service with OAuth2."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        console.print("[red]Gmail packages not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client[/red]")
        return None

    creds_path = Path(__file__).parent.parent / os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
    token_path = Path(__file__).parent.parent / os.getenv("GMAIL_TOKEN_PATH", "token.json")
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not creds_path.exists():
                console.print(f"[red]Gmail credentials not found at {creds_path}.[/red]")
                console.print("Download OAuth2 credentials from Google Cloud Console and save as credentials.json")
                return None
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def cmd_check_emails():
    db.init_db()
    with db.get_db() as conn:
        open_apps = [
            dict(a) for a in db.get_applications(conn)
            if a["status"] not in ("offer", "rejected", "ghosted")
        ]

    if not open_apps:
        console.print("[yellow]No open applications to check.[/yellow]")
        return

    service = _get_gmail_service()
    if not service:
        return

    client = Groq(api_key=os.environ["GROQ_API_KEY"])
    updates = 0

    for app in open_apps:
        company = app.get("company", "")
        role = app.get("role", "")
        if not company:
            continue

        # Extract domain-ish terms from company name
        company_clean = company.lower().replace(" ", "").replace(",", "")
        query = (
            f'subject:(interview OR application OR shortlist OR regret OR offer OR congratulations) '
            f'"{company}"'
        )

        try:
            results = service.users().messages().list(
                userId="me", q=query, maxResults=3
            ).execute()
            messages = results.get("messages", [])
        except Exception as e:
            console.print(f"  [dim red]Gmail search error for {company}: {e}[/dim red]")
            continue

        if not messages:
            continue

        for msg_meta in messages[:2]:
            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_meta["id"], format="metadata",
                    metadataHeaders=["Subject", "From", "Date"]
                ).execute()

                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = headers.get("Subject", "")
                snippet = msg.get("snippet", "")
                email_date = headers.get("Date", "")

                # Skip if already processed (within 30s of last update)
                if app.get("last_email_update") and email_date in (app.get("last_email_update") or ""):
                    continue

                # Classify with Groq
                resp = client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=[{"role": "user", "content": EMAIL_CLASSIFY_PROMPT.format(
                        company=company, role=role, subject=subject, snippet=snippet
                    )}],
                    response_format={"type": "json_object"},
                    temperature=0.1,
                    max_tokens=150,
                )
                result = json.loads(resp.choices[0].message.content)
                classification = result.get("classification", "other")
                summary = result.get("summary", "")

                # Map classification to status
                status_map = {
                    "interview_invite": "interview",
                    "rejection": "rejected",
                    "offer": "offer",
                    "follow_up_needed": app["status"],
                    "other": app["status"],
                }
                new_status = status_map.get(classification, app["status"])

                if new_status != app["status"]:
                    with db.get_db() as conn:
                        db.update_application(conn, app["id"],
                            status=new_status,
                            last_email_update=email_date,
                            notes=f"[Email] {summary}",
                        )
                    color = STATUS_COLORS.get(new_status, "white")
                    console.print(
                        f"[bold]{company}[/bold] [{app['status']} → [{color}]{new_status}[/{color}]] "
                        f"{subject[:60]}"
                    )
                    updates += 1

            except Exception as e:
                console.print(f"  [dim red]Message processing error: {e}[/dim red]")

    if updates == 0:
        console.print("[dim]No status changes detected from emails.[/dim]")
    else:
        console.print(f"\n[green]{updates} application(s) updated from email.[/green]")


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Job application tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List applications")
    p_list.add_argument("--status", choices=VALID_STATUSES)

    # update
    p_update = sub.add_parser("update", help="Update an application")
    p_update.add_argument("id", help="Application ID")
    p_update.add_argument("--status", choices=VALID_STATUSES)
    p_update.add_argument("--notes", help="Free-text notes")

    # check-emails
    sub.add_parser("check-emails", help="Check Gmail for application updates")

    # stats
    sub.add_parser("stats", help="Summary statistics")

    args = parser.parse_args()

    if args.command == "list":
        cmd_list(args.status)
    elif args.command == "update":
        cmd_update(args.id, getattr(args, "status", None), getattr(args, "notes", None))
    elif args.command == "check-emails":
        cmd_check_emails()
    elif args.command == "stats":
        cmd_stats()


if __name__ == "__main__":
    main()
