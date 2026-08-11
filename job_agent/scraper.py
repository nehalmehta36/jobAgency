"""
scraper.py — scrape jobs from multiple portals, score with Groq, store in SQLite.

Usage:
    python scraper.py --query "AI engineer backend" --location "India" --limit 20
    python scraper.py --query "backend Node.js" --location "India" --portals indeed naukri
    python scraper.py --query "backend engineer" --location "India" --remote --limit 10
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import json
import re
from pathlib import Path
from urllib.parse import urlencode, quote_plus

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from groq import Groq
from rich.console import Console
from rich.table import Table

load_dotenv(Path(__file__).parent.parent / ".env")
from . import db

console = Console()
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

CANDIDATE_PROFILE = """
Name: Nehal Mehta
Experience: 6 years — Backend Engineer
Core skills: Node.js, TypeScript, Python, NestJS, FastAPI, GraphQL, REST, Microservices
AI/ML: RAG pipelines, LLM API integration, agentic system design, vector DBs, LangChain
Data: PostgreSQL, MongoDB, Redis, InfluxDB, Kafka, AWS Neptune (Graph DB)
Cloud: AWS (Lambda, S3, IoT Core, Neptune), Docker, Kubernetes, CI/CD
Target roles: Backend Engineer (AI), Full Stack AI Developer, Tech Lead
Target salary: 40-50 LPA
Location: Delhi, India — open to remote or hybrid
"""

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def _job_id(company: str, title: str) -> str:
    return hashlib.md5(f"{company.lower().strip()}{title.lower().strip()}".encode()).hexdigest()[:12]


# ── Relevance scoring ─────────────────────────────────────────────────────

SCORE_PROMPT = """You are a technical recruiter scoring job relevance for a candidate.

Candidate profile:
{profile}

Job Title: {title}
Company: {company}
Job Description:
{jd_text}

Return ONLY a JSON object: {{"score": <0-100 integer>, "reason": "<one sentence>"}}

Score 0-100 where:
- 80-100: excellent match (core skills + tech stack align well)
- 60-79: good match (most skills match, minor gaps)
- 40-59: partial match (some relevant skills but significant gaps)
- 0-39: poor match"""


def score_job(title: str, company: str, jd_text: str, client: Groq) -> tuple[int, str]:
    try:
        resp = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": SCORE_PROMPT.format(
                    profile=CANDIDATE_PROFILE,
                    title=title,
                    company=company,
                    jd_text=jd_text[:3000],
                ),
            }],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=200,
        )
        data = json.loads(resp.choices[0].message.content)
        return int(data.get("score", 0)), str(data.get("reason", ""))
    except Exception as e:
        console.print(f"  [dim red]Scoring error: {e}[/dim red]")
        return 0, "scoring failed"


# ── Indeed scraper ────────────────────────────────────────────────────────

def _get_page(url: str, retries=2, delay=2.0) -> BeautifulSoup | None:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(delay)
            else:
                raise


def scrape_indeed(query: str, location: str, limit: int = 10, remote: bool = False) -> list[dict]:
    console.print("  [dim]Scraping Indeed...[/dim]")
    jobs = []
    params = {"q": query, "l": location, "limit": min(limit, 25)}
    if remote:
        params["remotejobs"] = "1"
    url = f"https://www.indeed.com/jobs?{urlencode(params)}"

    try:
        soup = _get_page(url)
        cards = soup.select("[data-jk]") or soup.select(".job_seen_beacon") or []

        for card in cards[:limit]:
            try:
                title_el = card.select_one("h2.jobTitle span, [data-testid='job-title'] span, h2 a span")
                company_el = card.select_one("[data-testid='company-name'], .companyName, span.companyName")
                location_el = card.select_one("[data-testid='text-location'], .companyLocation")
                salary_el = card.select_one("[data-testid='attribute_snippet_testid'], .metadata .salary-snippet")

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else ""
                loc = location_el.get_text(strip=True) if location_el else location
                salary = salary_el.get_text(strip=True) if salary_el else ""

                jk = card.get("data-jk") or ""
                jd_url = f"https://www.indeed.com/viewjob?jk={jk}" if jk else ""

                if not title or not company:
                    continue

                # Fetch JD text
                jd_text = ""
                if jd_url:
                    try:
                        detail_soup = _get_page(jd_url, retries=1)
                        jd_el = detail_soup.select_one("#jobDescriptionText, .jobsearch-jobDescriptionText")
                        jd_text = jd_el.get_text(" ", strip=True) if jd_el else ""
                    except Exception:
                        pass

                jobs.append({
                    "id": _job_id(company, title),
                    "title": title,
                    "company": company,
                    "location": loc,
                    "salary": salary,
                    "portal": "indeed",
                    "jd_url": jd_url,
                    "jd_text": jd_text,
                    "scraped_at": db.now_iso(),
                    "status": "new",
                })
                time.sleep(0.5)
            except Exception as e:
                console.print(f"  [dim red]Indeed card parse error: {e}[/dim red]")
                continue
    except Exception as e:
        raise RuntimeError(f"Indeed scrape failed: {e}")

    return jobs


# ── Naukri scraper ────────────────────────────────────────────────────────

def scrape_naukri(query: str, location: str, limit: int = 10, remote: bool = False) -> list[dict]:
    console.print("  [dim]Scraping Naukri...[/dim]")
    jobs = []

    # Naukri uses a JSON API for search
    api_url = "https://www.naukri.com/jobapi/v3/search"
    params = {
        "noOfResults": min(limit, 20),
        "urlType": "search_by_keyword",
        "searchType": "adv",
        "keyword": query,
        "location": location,
        "experience": "4",
        "src": "jobsearchDesk",
        "latLong": "",
    }
    api_headers = {
        **HEADERS,
        "appid": "109",
        "systemid": "Naukri",
        "Accept": "application/json",
        "Referer": "https://www.naukri.com/",
    }

    try:
        resp = requests.get(api_url, headers=api_headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        job_list = data.get("jobDetails") or data.get("jobs") or []

        for item in job_list[:limit]:
            title = item.get("title", "")
            company = item.get("companyName", "")
            loc = ", ".join(item.get("placeholders", [{}])[0].get("label", "").split(",")[:2]) if item.get("placeholders") else location
            salary = item.get("salary", "")
            jd_url = item.get("jdURL", "") or item.get("jobUrl", "")
            jd_text = item.get("jobDescription", "") or item.get("snippet", "")

            if not title or not company:
                continue

            jobs.append({
                "id": _job_id(company, title),
                "title": title,
                "company": company,
                "location": loc,
                "salary": salary,
                "portal": "naukri",
                "jd_url": jd_url,
                "jd_text": jd_text,
                "scraped_at": db.now_iso(),
                "status": "new",
            })
    except Exception as e:
        # Fallback: try HTML scraping
        console.print(f"  [dim yellow]Naukri API failed ({e}), trying HTML...[/dim yellow]")
        try:
            slug = quote_plus(query.replace(" ", "-").lower())
            url = f"https://www.naukri.com/{slug}-jobs-in-{location.lower().replace(' ', '-')}"
            soup = _get_page(url)
            cards = soup.select("article.jobTuple, .cust-job-tuple")
            for card in cards[:limit]:
                title_el = card.select_one("a.title")
                company_el = card.select_one("a.subTitle, .companyInfo a")
                location_el = card.select_one("li.fleft.grey-text")
                salary_el = card.select_one(".salary")

                title = title_el.get_text(strip=True) if title_el else ""
                company = company_el.get_text(strip=True) if company_el else ""
                loc = location_el.get_text(strip=True) if location_el else location
                salary = salary_el.get_text(strip=True) if salary_el else ""
                jd_url = title_el.get("href", "") if title_el else ""

                if not title or not company:
                    continue

                jobs.append({
                    "id": _job_id(company, title),
                    "title": title, "company": company, "location": loc,
                    "salary": salary, "portal": "naukri", "jd_url": jd_url,
                    "jd_text": "", "scraped_at": db.now_iso(), "status": "new",
                })
        except Exception as e2:
            raise RuntimeError(f"Naukri scrape failed: {e2}")

    return jobs


# ── LinkedIn scraper ──────────────────────────────────────────────────────

def scrape_linkedin(query: str, location: str, limit: int = 10, remote: bool = False) -> list[dict]:
    console.print("  [dim]Scraping LinkedIn...[/dim]")
    jobs = []
    params = {
        "keywords": query,
        "location": location,
        "f_TPR": "r604800",  # last 7 days
        "start": "0",
    }
    if remote:
        params["f_WT"] = "2"

    url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{urlencode(params)}"

    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li")

        for card in cards[:limit]:
            title_el = card.select_one("h3.base-search-card__title")
            company_el = card.select_one("h4.base-search-card__subtitle")
            location_el = card.select_one("span.job-search-card__location")
            link_el = card.select_one("a.base-card__full-link")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else location
            jd_url = link_el.get("href", "") if link_el else ""

            if not title or not company:
                continue

            # Fetch JD text from job page
            jd_text = ""
            if jd_url:
                try:
                    jd_resp = requests.get(jd_url, headers=HEADERS, timeout=10)
                    jd_soup = BeautifulSoup(jd_resp.text, "html.parser")
                    jd_el = jd_soup.select_one(".description__text, .show-more-less-html__markup")
                    jd_text = jd_el.get_text(" ", strip=True) if jd_el else ""
                except Exception:
                    pass

            jobs.append({
                "id": _job_id(company, title),
                "title": title, "company": company, "location": loc,
                "salary": "", "portal": "linkedin", "jd_url": jd_url,
                "jd_text": jd_text, "scraped_at": db.now_iso(), "status": "new",
            })
            time.sleep(0.3)
    except Exception as e:
        raise RuntimeError(f"LinkedIn scrape failed: {e}")

    return jobs


# ── ZipRecruiter scraper ──────────────────────────────────────────────────

def scrape_ziprecruiter(query: str, location: str, limit: int = 10, remote: bool = False) -> list[dict]:
    console.print("  [dim]Scraping ZipRecruiter...[/dim]")
    jobs = []
    params = {"search": query, "location": location, "radius": "25"}
    if remote:
        params["refine_by_location_type"] = "only_remote"

    url = f"https://www.ziprecruiter.com/jobs-search?{urlencode(params)}"

    try:
        soup = _get_page(url)
        cards = soup.select("article.job_result, [data-testid='job-card'], .job_content")

        for card in cards[:limit]:
            title_el = card.select_one("h2.job_title a, .job_title a, [data-testid='job-title']")
            company_el = card.select_one(".hiring_company_text, [data-testid='job-company']")
            location_el = card.select_one(".location, [data-testid='job-location']")
            salary_el = card.select_one(".compensation, [data-testid='job-salary']")

            title = title_el.get_text(strip=True) if title_el else ""
            company = company_el.get_text(strip=True) if company_el else ""
            loc = location_el.get_text(strip=True) if location_el else location
            salary = salary_el.get_text(strip=True) if salary_el else ""
            jd_url = title_el.get("href", "") if title_el and title_el.name == "a" else ""

            if not title or not company:
                continue

            jd_text = ""
            if jd_url:
                try:
                    jd_soup = _get_page(jd_url, retries=1)
                    jd_el = jd_soup.select_one(".jobDescriptionSection, #job_desc")
                    jd_text = jd_el.get_text(" ", strip=True) if jd_el else ""
                except Exception:
                    pass

            jobs.append({
                "id": _job_id(company, title),
                "title": title, "company": company, "location": loc,
                "salary": salary, "portal": "ziprecruiter", "jd_url": jd_url,
                "jd_text": jd_text, "scraped_at": db.now_iso(), "status": "new",
            })
    except Exception as e:
        raise RuntimeError(f"ZipRecruiter scrape failed: {e}")

    return jobs


# ── Dice scraper ──────────────────────────────────────────────────────────

def scrape_dice(query: str, location: str, limit: int = 10, remote: bool = False) -> list[dict]:
    console.print("  [dim]Scraping Dice...[/dim]")
    jobs = []

    # Dice has a public REST API
    api_url = "https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search"
    payload = {
        "q": query,
        "countryCode2": "US",
        "radius": "30",
        "radiusUnit": "mi",
        "page": 1,
        "pageSize": min(limit, 20),
        "filters": {
            "employmentType": "FULLTIME",
            "postedDate": "ONE_WEEK",
        },
        "fields": "id,title,company,location,salary,detailUrl,descriptionFragment",
        "culture": "en",
        "recommendations": True,
        "interactionId": 0,
        "pivot": False,
    }
    if remote:
        payload["filters"]["workplaceTypes"] = "Remote"

    try:
        resp = requests.post(
            api_url,
            json=payload,
            headers={**HEADERS, "Accept": "application/json", "Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        job_list = data.get("data", [])

        for item in job_list[:limit]:
            title = item.get("title", "")
            company = item.get("company", "")
            loc = item.get("location", "")
            salary = item.get("salary", "")
            jd_url = item.get("detailUrl", "")
            jd_text = item.get("descriptionFragment", "")

            if not title or not company:
                continue

            jobs.append({
                "id": _job_id(company, title),
                "title": title, "company": company, "location": loc,
                "salary": salary, "portal": "dice", "jd_url": jd_url,
                "jd_text": jd_text, "scraped_at": db.now_iso(), "status": "new",
            })
    except Exception as e:
        raise RuntimeError(f"Dice scrape failed: {e}")

    return jobs


# ── Scrape + score + store ────────────────────────────────────────────────

PORTAL_FNS = {
    "indeed": scrape_indeed,
    "naukri": scrape_naukri,
    "linkedin": scrape_linkedin,
    "ziprecruiter": scrape_ziprecruiter,
    "dice": scrape_dice,
}


def run_portal(name: str, fn, query: str, location: str, limit: int, remote: bool,
               client: Groq, min_score: int = 60) -> int:
    """Scrape one portal, score jobs, store results. Returns number stored."""
    try:
        jobs = fn(query, location, limit, remote)
    except Exception as e:
        console.print(f"  [red]{name} failed:[/red] {e}")
        with db.get_db() as conn:
            db.log_scrape(conn, name, query, location, error=str(e))
        return 0

    stored = 0
    with db.get_db() as conn:
        for job in jobs:
            if not job["jd_text"]:
                job["jd_text"] = f"{job['title']} at {job['company']} in {job['location']}"

            score, reason = score_job(job["title"], job["company"], job["jd_text"], client)
            job["relevance_score"] = score
            job["relevance_reason"] = reason

            if score >= min_score:
                db.upsert_job(conn, job)
                stored += 1
                console.print(
                    f"  [green]+[/green] {job['title'][:45]:<45} "
                    f"@ {job['company'][:25]:<25} [{score:3d}] {reason[:60]}"
                )
            else:
                console.print(
                    f"  [dim]- {job['title'][:40]:<40} [{score:3d}] (below threshold)[/dim]"
                )

        db.log_scrape(conn, name, query, location, len(jobs), stored)

    return stored


# ── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Scrape jobs and score with Groq")
    parser.add_argument("--query", required=True)
    parser.add_argument("--location", default="India")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-score", type=int, default=60)
    parser.add_argument(
        "--portals", nargs="+",
        choices=list(PORTAL_FNS.keys()),
        default=["naukri", "linkedin", "indeed"],
        help="Which portals to scrape (default: naukri linkedin indeed)",
    )
    args = parser.parse_args()

    db.init_db()
    client = Groq(api_key=os.environ["GROQ_API_KEY"])

    console.print(f"\n[bold cyan]Job Scraper[/bold cyan]")
    console.print(f"Query: [bold]{args.query}[/bold]  Location: [bold]{args.location}[/bold]  "
                  f"Portals: [bold]{', '.join(args.portals)}[/bold]\n")

    total = 0
    for portal in args.portals:
        console.print(f"[bold]{portal.upper()}[/bold]")
        n = run_portal(
            portal, PORTAL_FNS[portal],
            args.query, args.location, args.limit, args.remote,
            client, args.min_score,
        )
        total += n
        console.print(f"  [cyan]→ {n} job(s) stored[/cyan]\n")

    console.print(f"[bold green]Total stored:[/bold green] {total} jobs (score ≥ {args.min_score})")


if __name__ == "__main__":
    main()
