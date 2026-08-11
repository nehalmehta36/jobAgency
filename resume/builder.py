"""
Nehal Mehta – ATS-optimised resume (ReportLab / 1 page).
All text lives in the CONTENT VARIABLES block below so tailor.py can
patch individual fields without touching the rendering code.
"""
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate,
    Paragraph, Spacer, HRFlowable, KeepTogether,
)
from reportlab.lib import colors

# ── CONTACT (never patched) ────────────────────────────────────────────────
CONTACT = {
    "name": "NEHAL MEHTA",
    "email": "nehalmehta36@gmail.com",
    "phone": "(+91) 99589 61516",
    "linkedin": "linkedin.com/in/nehallmehta",
    "github": "github.com/nehalmehta",
    "location": "Delhi, India · Open to Remote / Hybrid",
}

# ── CONTENT VARIABLES (patched by tailor.py) ──────────────────────────────
SUMMARY = (
    "Backend engineer with 6 years building distributed, event-driven systems across IoT and "
    "telecom. Designed scalable data ingestion pipelines, microservices APIs, and a graph-DB "
    "retrieval layer architected as the knowledge backbone for LLM-powered root-cause analysis — "
    "powering 100+ enterprise buildings and ~10,000 IoT controllers across Apollo Hospitals, "
    "Le Méridien, and KIIMS. Strong grasp of RAG pipeline architecture, prompt engineering, and "
    "agentic system design. Experienced in Agile delivery, cross-functional collaboration, "
    "authentication, and secure API development. Targeting Full Stack AI Developer roles building "
    "production-grade LLM and conversational AI platforms."
)

SJ_BULLETS = [
    (
        "Designed a graph-DB knowledge layer on AWS Neptune (Brick schema) modelling causal "
        "equipment dependencies across 100+ buildings — architected as the RAG retrieval backbone "
        "for a planned LLM-powered fault-diagnosis agent; enables semantic traversal to isolate "
        "root-cause of building system failures (e.g. chiller not receiving chilled water)."
    ),
    (
        "Architected real-time data ingestion pipeline (InfluxDB + Kafka + Node.js) processing "
        "structured telemetry from ~10,000 IoT sensors; built expression-based alerting engine "
        "with confidence-threshold evaluation and full event lifecycle tracking — 1 lakh+ events "
        "processed, 100+ DAU across enterprise clients."
    ),
    (
        "Built OTA firmware deployment system on AWS IoT Core + Lambda — eliminated manual "
        "SSH-based updates across the entire device fleet; integrated into CI/CD pipeline for "
        "zero-touch controller provisioning."
    ),
    (
        "Integrated BACnet protocol support end-to-end with IoT hardware team, expanding device "
        "compatibility fleet-wide; acting Scrum Master — sprint planning, dev onboarding, "
        "technical documentation, intern mentorship."
    ),
]

AC_BULLETS = [
    (
        "Architected user management microservice in NestJS/TypeScript with JWT-based "
        "authentication and role-based access control — reduced enterprise onboarding by 30%, "
        "support load by 25%; led code reviews, technical documentation, and team delivery."
    ),
    (
        "Built Kafka-based data processing pipeline at 850 RPS (15% throughput gain); implemented "
        "per-user API rate limiting on AWS Lambda (sliding window, 10k RPS) — created a "
        "metered-API revenue stream; serving Tata Telephony, TCS, DishTV."
    ),
    (
        "Developed VoIP communication platform via Linphone SDK on SIP + Asterisk — 30% "
        "improvement in call efficiency; built service management apps cutting support tickets by 25%."
    ),
]

SKILLS = {
    "GenAI & LLMs": (
        "RAG pipeline architecture · LLM API integration (OpenAI, Anthropic) · "
        "Prompt engineering · Agentic system design · Vector DB concepts · LangChain · "
        "GitHub Copilot · Cursor"
    ),
    "Backend": (
        "Node.js · TypeScript · Python · NestJS · Express.js · FastAPI · "
        "Next.js (SSR, API routes, auth) · REST · GraphQL · Microservices"
    ),
    "Data & DBs": (
        "PostgreSQL · MongoDB · Redis · InfluxDB · DynamoDB · "
        "AWS Neptune (Graph DB) · Kafka · AWS SQS/SNS"
    ),
    "Cloud & Infra": "AWS (Lambda · S3 · IoT Core · Neptune) · Docker · Kubernetes · CI/CD pipelines",
    "Security": (
        "JWT · OAuth · Role-based access control · Secure API design · "
        "Input validation · Encryption"
    ),
    "Leadership": (
        "Scrum Master · Agile delivery · Technical mentoring · "
        "Cross-functional collaboration · Technical documentation"
    ),
}

EDUCATION = [
    {
        "degree": "B.Tech, Computer Science",
        "school": "Ansal University, Gurugram",
        "dates": "2016 – 2020",
    }
]

AWARDS = [
    "Innovation Award & GEM Award — Acefone",
    "Employee of the Month (Spotlight) — Q1 2022 · Consistent Key Contributor / Exceeded Expectations rating",
]

# ── EXPERIENCE METADATA (never patched) ───────────────────────────────────
SJ_META = {
    "title": "Backend Engineer",
    "company": "SmartJoules",
    "location": "Gurugram, India",
    "dates": "Jan 2025 – Present",
}
AC_META = {
    "title": "Software Developer",
    "company": "Acefone",
    "location": "Gurugram, India",
    "dates": "Jan 2020 – Sep 2024",
}

# ── STYLES ────────────────────────────────────────────────────────────────
_GREY = colors.HexColor("#444444")
_BLACK = colors.black

def _make_styles():
    return {
        "name": ParagraphStyle(
            "Name", fontName="Helvetica-Bold", fontSize=17,
            leading=20, alignment=TA_CENTER, textColor=_BLACK, spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "Contact", fontName="Helvetica", fontSize=8.5,
            leading=11, alignment=TA_CENTER, textColor=_GREY,
        ),
        "section": ParagraphStyle(
            "Section", fontName="Helvetica-Bold", fontSize=9.5,
            leading=12, alignment=TA_LEFT, textColor=_BLACK,
            spaceBefore=5, spaceAfter=1, textTransform="uppercase",
        ),
        "job_title": ParagraphStyle(
            "JobTitle", fontName="Helvetica-Bold", fontSize=9,
            leading=11, textColor=_BLACK, spaceBefore=4, spaceAfter=0,
        ),
        "job_meta": ParagraphStyle(
            "JobMeta", fontName="Helvetica", fontSize=8.5,
            leading=11, textColor=_GREY, spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "Bullet", fontName="Helvetica", fontSize=8.5,
            leading=11, leftIndent=10, firstLineIndent=0,
            spaceAfter=2, textColor=_BLACK,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=8.5,
            leading=11, textColor=_BLACK, spaceAfter=2,
        ),
        "skills_label": ParagraphStyle(
            "SkillsLabel", fontName="Helvetica-Bold", fontSize=8.5,
            leading=11, textColor=_BLACK,
        ),
        "skills_value": ParagraphStyle(
            "SkillsValue", fontName="Helvetica", fontSize=8.5,
            leading=11, textColor=_BLACK,
        ),
        "edu_line": ParagraphStyle(
            "EduLine", fontName="Helvetica", fontSize=8.5,
            leading=11, textColor=_BLACK, spaceAfter=1,
        ),
    }


def _hr():
    return HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#bbbbbb"), spaceAfter=3)


def _section(title, s):
    return [Paragraph(title, s["section"]), _hr()]


def _bullet_para(text, s):
    return Paragraph(f"<bullet>&ndash;</bullet>{text}", s["bullet"])


def _job_block(meta, bullets, s):
    items = [
        Paragraph(
            f'<b>{meta["title"]}</b>',
            s["job_title"],
        ),
        Paragraph(
            f'{meta["company"]} · {meta["location"]} &nbsp;&nbsp;&nbsp; '
            f'<font color="#888888">{meta["dates"]}</font>',
            s["job_meta"],
        ),
    ]
    for b in bullets:
        items.append(_bullet_para(b, s))
    return KeepTogether(items)


# ── RENDER ────────────────────────────────────────────────────────────────
def render_pdf(
    output_path: str,
    summary: str = None,
    sj_bullets: list = None,
    ac_bullets: list = None,
    skills: dict = None,
) -> str:
    """Render the resume to a PDF.

    All parameters default to the module-level content variables.
    tailor.py passes patched values to override only what changed.
    """
    _summary = summary if summary is not None else SUMMARY
    _sj_bullets = sj_bullets if sj_bullets is not None else SJ_BULLETS
    _ac_bullets = ac_bullets if ac_bullets is not None else AC_BULLETS
    _skills = skills if skills is not None else SKILLS

    s = _make_styles()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    margin = 0.5 * inch
    doc = BaseDocTemplate(
        output_path,
        pagesize=letter,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=0.45 * inch,
        bottomMargin=0.4 * inch,
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height,
        id="main", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([PageTemplate(id="main", frames=frame)])

    story = []

    # ── Header ────────────────────────────────────────────────────────────
    story.append(Paragraph(CONTACT["name"], s["name"]))
    story.append(Paragraph(
        f'{CONTACT["email"]} &nbsp;·&nbsp; {CONTACT["phone"]} &nbsp;·&nbsp; '
        f'{CONTACT["linkedin"]} &nbsp;·&nbsp; {CONTACT["github"]}',
        s["contact"],
    ))
    story.append(Paragraph(CONTACT["location"], s["contact"]))
    story.append(Spacer(1, 4))
    story.append(_hr())

    # ── Summary ───────────────────────────────────────────────────────────
    story.extend(_section("Summary", s))
    story.append(Paragraph(_summary, s["body"]))
    story.append(Spacer(1, 3))

    # ── Experience ────────────────────────────────────────────────────────
    story.extend(_section("Experience", s))
    story.append(_job_block(SJ_META, _sj_bullets, s))
    story.append(Spacer(1, 4))
    story.append(_job_block(AC_META, _ac_bullets, s))
    story.append(Spacer(1, 3))

    # ── Skills ────────────────────────────────────────────────────────────
    story.extend(_section("Skills", s))
    for label, value in _skills.items():
        story.append(Paragraph(
            f'<b>{label}</b>&nbsp;&nbsp;{value}', s["body"],
        ))

    story.append(Spacer(1, 3))

    # ── Education ─────────────────────────────────────────────────────────
    story.extend(_section("Education", s))
    for ed in EDUCATION:
        story.append(Paragraph(
            f'<b>{ed["degree"]}</b> · {ed["school"]} '
            f'<font color="#888888">{ed["dates"]}</font>',
            s["edu_line"],
        ))

    story.append(Spacer(1, 3))

    # ── Awards ────────────────────────────────────────────────────────────
    story.extend(_section("Awards", s))
    for award in AWARDS:
        story.append(_bullet_para(award, s))

    doc.build(story)
    return output_path


def resume_as_text(
    summary: str = None,
    sj_bullets: list = None,
    ac_bullets: list = None,
    skills: dict = None,
) -> str:
    """Return resume as plain text (for LLM prompts and ATS checks)."""
    _summary = summary or SUMMARY
    _sj_bullets = sj_bullets or SJ_BULLETS
    _ac_bullets = ac_bullets or AC_BULLETS
    _skills = skills or SKILLS

    lines = [
        CONTACT["name"],
        f'{CONTACT["email"]} | {CONTACT["phone"]} | {CONTACT["linkedin"]} | {CONTACT["github"]}',
        CONTACT["location"],
        "",
        "SUMMARY",
        _summary,
        "",
        "EXPERIENCE",
        f'{SJ_META["title"]} | {SJ_META["company"]} | {SJ_META["dates"]}',
    ]
    for b in _sj_bullets:
        lines.append(f"- {b}")
    lines.append("")
    lines.append(f'{AC_META["title"]} | {AC_META["company"]} | {AC_META["dates"]}')
    for b in _ac_bullets:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("SKILLS")
    for label, value in _skills.items():
        lines.append(f"{label}: {value}")
    lines.append("")
    lines.append("EDUCATION")
    for ed in EDUCATION:
        lines.append(f'{ed["degree"]} | {ed["school"]} | {ed["dates"]}')
    lines.append("")
    lines.append("AWARDS")
    for a in AWARDS:
        lines.append(f"- {a}")
    return "\n".join(lines)


if __name__ == "__main__":
    out = Path(__file__).parent / "outputs" / "Nehal_Mehta_Resume_2025.pdf"
    render_pdf(str(out))
    print(f"Generated: {out}")
