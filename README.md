# Job Agent

A personal job application automation system. Scrapes jobs from Naukri and LinkedIn, scores them with AI, tailors your resume to each JD, and auto-applies via Playwright — all from a Streamlit dashboard.

## What it does

- **Scrape** — pulls jobs from Naukri, LinkedIn, Indeed, ZipRecruiter, Dice
- **Score** — uses Groq (Llama 3.3 70B) to rate each job's relevance to your resume (0–100)
- **Tailor** — gap-analyses the JD vs your resume, patches content, renders a tailored PDF, verifies ATS keyword coverage
- **Auto-apply** — Playwright automation for Naukri (full) and LinkedIn/Indeed Easy Apply (conditional); manual redirect link for everything else
- **Track** — SQLite database of all applications with status, resume file, and apply method

## Stack

Python · Streamlit · Playwright · Groq LLM · SQLite · ReportLab · LangGraph

## Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Install Playwright browser
playwright install chromium

# 3. Set environment variables
cp .env.example .env
# Edit .env — add GROQ_API_KEY
```

## Usage

### Dashboard

```bash
streamlit run dashboard.py
```

- **Jobs tab** — browse scored jobs, tailor resumes, auto-apply
- **Applications tab** — track status across all applications

### CLI agent

```bash
python run.py
# or with a goal:
python run.py --goal "Find backend AI jobs on Naukri and apply to the top matches"
```

The agent calls tools in sequence: `search_jobs → list_jobs → tailor_resume → apply_job`

### Session setup (one-time per portal)

Auto-apply requires a saved login session. Run this once per portal:

```bash
python -m job_agent.applier setup naukri
python -m job_agent.applier setup linkedin
python -m job_agent.applier setup indeed
```

A headed browser opens — log in manually, then press Enter. The session is saved to `data/sessions/{portal}/state.json` (gitignored).

## Portal support

| Portal | Auto-apply |
|---|---|
| Naukri | Full |
| LinkedIn | Easy Apply jobs only |
| Indeed | Indeed Apply jobs only |
| ZipRecruiter | Manual link |
| Dice | Manual link |

## Project structure

```
job_agent/
  agent.py      — LangGraph agent loop
  applier.py    — Playwright auto-apply dispatcher
  db.py         — SQLite schema and queries
  scraper.py    — Portal scrapers
  tailor.py     — Resume gap analysis and PDF generation
  tools.py      — LangChain tools for the agent
resume/
  builder.py    — ReportLab PDF renderer
dashboard.py    — Streamlit UI
tests/
  test_auto_apply.py  — 27 tests covering DB, applier, and tools
```

## Environment variables

| Variable | Description |
|---|---|
| `GROQ_API_KEY` | Groq API key (get one at console.groq.com) |
