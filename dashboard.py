"""
dashboard.py — Job Agent dashboard, redesigned with Ant Design (streamlit-antd-components).
Run: streamlit run dashboard.py
"""
import os
import sys
from pathlib import Path
from typing import Annotated, Optional, TypedDict

import pandas as pd
import streamlit as st
import streamlit_antd_components as sac
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

load_dotenv(Path(__file__).parent / ".env")

from job_agent import db
from job_agent.tools import _do_tailor, get_stats, list_jobs, search_jobs, update_application
from job_agent.applier import try_auto_apply, PORTAL_APPLY_SUPPORT
from job_agent.db import get_application_by_job

# ── Page config ────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Job Agent · Nehal Mehta",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1rem; padding-bottom: 1rem; }

/* Ant Design-style stat cards */
.stat-row { display: flex; gap: 12px; margin-bottom: 20px; }
.stat-card {
    flex: 1; background: #fff; border: 1px solid #f0f0f0;
    border-radius: 8px; padding: 18px 20px;
    box-shadow: 0 1px 3px rgba(0,0,0,.04);
    transition: box-shadow .2s;
}
.stat-card:hover { box-shadow: 0 3px 10px rgba(0,0,0,.08); }
.stat-value { font-size: 26px; font-weight: 700; color: #1677ff; line-height: 1; }
.stat-value.green  { color: #52c41a; }
.stat-value.orange { color: #fa8c16; }
.stat-value.purple { color: #722ed1; }
.stat-value.gray   { color: #8c8c8c; }
.stat-label {
    font-size: 11px; color: #8c8c8c; text-transform: uppercase;
    letter-spacing: .06em; margin-top: 6px;
}

/* Job card score display */
.job-score { text-align: center; padding: 4px 0; }
.job-score .num { font-size: 26px; font-weight: 700; line-height: 1; display: block; }
.job-score .lbl { font-size: 10px; color: #8c8c8c; text-transform: uppercase; letter-spacing: .05em; }

/* Job card status line (bottom of card) */
.job-status { font-size: 12px; padding: 4px 0; margin: 6px 0 0 0; border-top: 1px solid #f5f5f5; }
.job-status.tailored { color: #1677ff; }
.job-status.applied  { color: #52c41a; }
.job-status.warning  { color: #fa8c16; }

/* Chat bubbles */
.user-bubble {
    background: #e6f4ff; border-radius: 12px 12px 2px 12px;
    padding: 10px 14px; margin: 4px 0; max-width: 85%; margin-left: auto;
    font-size: 14px; color: #000d1a;
}
.agent-bubble {
    background: #f6ffed; border: 1px solid #b7eb8f;
    border-radius: 12px 12px 12px 2px;
    padding: 10px 14px; margin: 4px 0; max-width: 90%;
    font-size: 14px; color: #000d1a;
}
</style>
""", unsafe_allow_html=True)

# ── Constants ──────────────────────────────────────────────────────────────────────
VALID_STATUSES = ["applied", "screening", "interview", "offer", "rejected", "ghosted"]
STATUS_STEP    = ["applied", "screening", "interview", "offer"]   # linear progression

_SYSTEM = """You are Nehal Mehta's job application agent inside a web dashboard.
You help find backend AI/ML engineering roles, evaluate them, and track applications.

Nehal's profile:
- 6 years backend engineer (Node.js, TypeScript, Python, NestJS, FastAPI, GraphQL)
- AI/ML: RAG pipelines, LLM APIs, agentic systems, vector DBs, LangChain
- Target: Backend Engineer (AI) / Full Stack AI Dev / Tech Lead | 40-50 LPA | Delhi, remote ok

Your tools: search_jobs · list_jobs · update_application · get_stats
Note: resume tailoring is done via the Jobs tab (Tailor button) — not through this chat.
Be concise. Before each tool call write one short sentence explaining what you're doing.
After search_jobs always follow up with list_jobs to show results.
"""

CHAT_TOOLS = [search_jobs, list_jobs, update_application, get_stats]


# ── LangGraph agent ────────────────────────────────────────────────────────────────
@st.cache_resource
def _build_graph():
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(CHAT_TOOLS)

    class State(TypedDict):
        messages: Annotated[list, add_messages]

    def agent_node(state: State):
        return {"messages": [llm_with_tools.invoke(state["messages"])]}

    def route(state: State):
        return "tools" if getattr(state["messages"][-1], "tool_calls", None) else END

    g = StateGraph(State)
    g.add_node("agent", agent_node)
    g.add_node("tools", ToolNode(CHAT_TOOLS))
    g.set_entry_point("agent")
    g.add_conditional_edges("agent", route, {"tools": "tools", END: END})
    g.add_edge("tools", "agent")
    return g.compile()


# ── DB helpers ─────────────────────────────────────────────────────────────────────
def _apps_df() -> pd.DataFrame:
    db.init_db()
    with db.get_db() as conn:
        rows = [dict(r) for r in db.get_applications(conn)]
    cols = ["id", "company", "role", "portal", "applied_date", "status", "notes"]
    return pd.DataFrame(rows)[cols] if rows else pd.DataFrame(columns=cols)


def _jobs_df(min_score: int = 60) -> pd.DataFrame:
    db.init_db()
    with db.get_db() as conn:
        rows = [dict(r) for r in db.get_jobs(conn, min_score=min_score)]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _metrics() -> dict:
    db.init_db()
    with db.get_db() as conn:
        apps  = [dict(r) for r in db.get_applications(conn)]
        jobs_n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        last   = conn.execute(
            "SELECT run_at FROM scrape_logs WHERE error IS NULL ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
    m: dict = {
        "jobs_in_db":  jobs_n,
        "last_scrape": last[0][:10] if last else "never",
    }
    for a in apps:
        m[a["status"]] = m.get(a["status"], 0) + 1
    return m


# ── Agent runner ───────────────────────────────────────────────────────────────────
def _run_agent(messages: list) -> tuple:
    graph = _build_graph()
    if not graph:
        return messages, "⚠️ `OPENAI_API_KEY` not set in `.env`"

    seen  = len(messages)
    final = list(messages)

    with st.status("Agent working…", expanded=True) as status:
        for chunk in graph.stream({"messages": messages}, stream_mode="values"):
            msgs = chunk["messages"]
            for msg in msgs[seen:]:
                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        args    = tc.get("args") or {}
                        preview = ", ".join(
                            f"{k}={repr(v)[:28]}" for k, v in list(args.items())[:3]
                        )
                        status.write(f"🔧 `{tc['name']}({preview})`")
                elif getattr(msg, "type", None) == "tool":
                    status.write(f"✅ `{getattr(msg, 'name', 'tool')}` done")
            seen  = len(msgs)
            final = msgs
        status.update(label="Done", state="complete", expanded=False)

    ai_text = next(
        (m.content for m in reversed(final) if getattr(m, "type", None) == "ai" and m.content),
        "",
    )
    return final, ai_text


# ── Session state init ─────────────────────────────────────────────────────────────
if "lc_messages" not in st.session_state:
    st.session_state.lc_messages  = [SystemMessage(content=_SYSTEM)]
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []


# ── Sidebar ────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(
        "<h2 style='margin:0;padding:0'>🎯 Job Agent</h2>"
        "<p style='color:#8c8c8c;font-size:13px;margin:2px 0 12px'>Nehal Mehta · Backend AI / Tech Lead</p>",
        unsafe_allow_html=True,
    )

    sac.divider(label="Search Jobs", icon="search", align="center", color="gray")

    with st.form("search_form", clear_on_submit=False):
        query    = st.text_input("Query", "backend AI engineer")
        location = st.text_input("Location", "India")
        portals  = st.multiselect(
            "Portals",
            ["naukri", "linkedin", "indeed", "ziprecruiter", "dice"],
            default=["naukri", "linkedin"],
        )
        c1, c2    = st.columns(2)
        limit     = c1.number_input("Per portal", 5, 30, 10, step=5)
        min_score = c2.number_input("Min score",  0, 100, 60, step=5)
        remote    = st.checkbox("Remote only")
        submitted = st.form_submit_button("🔍  Search", use_container_width=True, type="primary")

    if submitted and query:
        portal_str = ", ".join(portals) if portals else "naukri, linkedin"
        st.session_state["_pending"] = (
            f"Search for '{query}' jobs in {location} on {portal_str}. "
            f"Limit {int(limit)} per portal, min score {int(min_score)}"
            f"{', remote only' if remote else ''}. "
            "After searching, list what was found."
        )

    sac.divider(label="Quick Actions", icon="lightning-fill", align="center", color="gray")

    col_s, col_l = st.columns(2)
    if col_s.button("📊 Stats",    use_container_width=True):
        st.session_state["_pending"] = "Show pipeline stats."
    if col_l.button("📋 List Jobs", use_container_width=True):
        st.session_state["_pending"] = "List all new jobs with score >= 60."

    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.lc_messages  = [SystemMessage(content=_SYSTEM)]
        st.session_state.chat_history = []
        st.rerun()


# ── Stat cards (shared across all tabs) ───────────────────────────────────────────
m = _metrics()

st.markdown(f"""
<div class="stat-row">
  <div class="stat-card">
    <div class="stat-value">{m.get("jobs_in_db", 0)}</div>
    <div class="stat-label">Jobs in DB</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{m.get("applied", 0)}</div>
    <div class="stat-label">Applied</div>
  </div>
  <div class="stat-card">
    <div class="stat-value orange">{m.get("screening", 0)}</div>
    <div class="stat-label">Screening</div>
  </div>
  <div class="stat-card">
    <div class="stat-value green">{m.get("interview", 0)}</div>
    <div class="stat-label">Interviews</div>
  </div>
  <div class="stat-card">
    <div class="stat-value purple">{m.get("offer", 0)}</div>
    <div class="stat-label">Offers</div>
  </div>
  <div class="stat-card">
    <div class="stat-value gray" style="font-size:16px;padding-top:4px">{m.get("last_scrape","never")}</div>
    <div class="stat-label">Last Scrape</div>
  </div>
</div>
""", unsafe_allow_html=True)


# ── Main tabs (Ant Design) ─────────────────────────────────────────────────────────
has_pending = "_pending" in st.session_state
tab_idx = sac.tabs(
    [
        sac.TabsItem("Chat",     icon="chat-dots-fill",  tag="1" if has_pending else None),
        sac.TabsItem("Pipeline", icon="kanban-fill"),
        sac.TabsItem("Jobs",     icon="briefcase-fill"),
    ],
    color="blue",
    size="md",
    return_index=True,
    key="main_tabs",
)


# ══════════════════════════════════════════════════════════════════════════════════
#  TAB 0 — CHAT
# ══════════════════════════════════════════════════════════════════════════════════
if tab_idx == 0:
    sac.divider(label="Conversation", icon="chat-dots", align="center", color="gray")

    # Render chat history
    for item in st.session_state.chat_history:
        if item["role"] == "user":
            st.markdown(f'<div class="user-bubble">🧑‍💻 {item["content"]}</div>', unsafe_allow_html=True)
        else:
            st.markdown(f'<div class="agent-bubble">🤖 {item["content"]}</div>', unsafe_allow_html=True)

    # Pending message (from sidebar search / quick buttons)
    pending = st.session_state.get("_pending")
    if pending:
        del st.session_state["_pending"]

    # Chat input
    chat_input = st.chat_input("Ask the agent — search jobs, check pipeline, update status…")
    user_msg   = pending or chat_input

    if user_msg:
        st.markdown(f'<div class="user-bubble">🧑‍💻 {user_msg}</div>', unsafe_allow_html=True)
        st.session_state.chat_history.append({"role": "user", "content": user_msg})
        st.session_state.lc_messages.append(HumanMessage(content=user_msg))

        new_msgs, ai_text = _run_agent(st.session_state.lc_messages)
        if ai_text:
            st.markdown(f'<div class="agent-bubble">🤖 {ai_text}</div>', unsafe_allow_html=True)

        st.session_state.lc_messages = new_msgs
        if ai_text:
            st.session_state.chat_history.append({"role": "assistant", "content": ai_text})

    if not st.session_state.chat_history and not user_msg:
        sac.alert(
            label="Agent ready",
            description='Use the search form or type a goal like "Find backend AI jobs in Bangalore on LinkedIn".',
            icon=True, closable=False, banner=False,
        )


# ══════════════════════════════════════════════════════════════════════════════════
#  TAB 1 — PIPELINE
# ══════════════════════════════════════════════════════════════════════════════════
elif tab_idx == 1:
    sac.divider(label="Application Pipeline", icon="kanban", align="center", color="gray")

    apps = _apps_df()

    if apps.empty:
        sac.result(
            label="No applications yet",
            description="Search for jobs and tailor your resume to start tracking applications.",
            status="info", icon=True,
        )
    else:
        view = sac.segmented(
            [
                sac.SegmentedItem("Table",      icon="table"),
                sac.SegmentedItem("Status View", icon="diagram-3"),
            ],
            align="start",
            return_index=True,
            key="pipeline_view",
        )

        # ── Table view ──────────────────────────────────────────────────────────
        if view == 0:
            original = apps.copy()
            edited = st.data_editor(
                apps,
                column_config={
                    "id":           st.column_config.TextColumn("ID",       disabled=True, width="small"),
                    "company":      st.column_config.TextColumn("Company",  disabled=True),
                    "role":         st.column_config.TextColumn("Role",     disabled=True),
                    "portal":       st.column_config.TextColumn("Portal",   disabled=True, width="small"),
                    "applied_date": st.column_config.TextColumn("Applied",  disabled=True, width="small"),
                    "status": st.column_config.SelectboxColumn(
                        "Status", options=VALID_STATUSES, width="medium"
                    ),
                    "notes": st.column_config.TextColumn("Notes"),
                },
                hide_index=True,
                use_container_width=True,
                num_rows="fixed",
                key="pipeline_editor",
            )

            if st.button("💾  Save Changes", type="primary"):
                db.init_db()
                saved = 0
                with db.get_db() as conn:
                    for _, row in edited.iterrows():
                        orig_rows = original[original["id"] == row["id"]]
                        if orig_rows.empty:
                            continue
                        orig   = orig_rows.iloc[0]
                        fields: dict = {}
                        if row.get("status") != orig.get("status"):
                            fields["status"] = row["status"]
                        if str(row.get("notes") or "") != str(orig.get("notes") or ""):
                            fields["notes"] = row["notes"]
                        if fields:
                            db.update_application(conn, row["id"], **fields)
                            saved += 1

                if saved:
                    st.success(f"Saved {saved} change(s).")
                    st.rerun()
                else:
                    st.info("No changes detected.")

        # ── Status view ─────────────────────────────────────────────────────────
        else:
            for _, row in apps.iterrows():
                status = row.get("status", "applied")
                step_idx = (
                    STATUS_STEP.index(status) if status in STATUS_STEP else 0
                )
                color = {
                    "applied":   "blue",
                    "screening": "orange",
                    "interview": "green",
                    "offer":     "purple",
                    "rejected":  "red",
                    "ghosted":   "gray",
                }.get(status, "blue")

                st.markdown(
                    f"**{row.get('company', '—')}** · {row.get('role', '—')} "
                    f"· <span style='color:{color};font-weight:600'>{status.upper()}</span>",
                    unsafe_allow_html=True,
                )

                if status not in ("rejected", "ghosted"):
                    sac.steps(
                        [sac.StepsItem(s.title()) for s in STATUS_STEP],
                        index=step_idx,
                        direction="horizontal",
                        dot=False,
                        return_index=False,
                    )
                else:
                    sac.alert(
                        label=status.title(),
                        description=row.get("notes") or "",
                        icon=True,
                        closable=False,
                    )

                st.markdown(
                    f"<small style='color:#8c8c8c'>Portal: {row.get('portal','—')} · Applied: {row.get('applied_date','—')}</small>",
                    unsafe_allow_html=True,
                )
                st.divider()


# ══════════════════════════════════════════════════════════════════════════════════
#  TAB 2 — JOBS
# ══════════════════════════════════════════════════════════════════════════════════
else:
    h1, h2 = st.columns([5, 1])
    h1.markdown("### Scraped Jobs")
    min_filter = h2.number_input("Min score", 0, 100, 60, step=5, key="jobs_min")

    sac.divider(icon="briefcase", align="center", color="gray")

    jobs = _jobs_df(min_score=int(min_filter))

    if jobs.empty:
        sac.result(
            label="No jobs found",
            description="Use the search form in the sidebar to scrape jobs from portals.",
            status="info", icon=True,
        )
    else:
        st.caption(f"{len(jobs)} job(s) · score ≥ {int(min_filter)}")

        for _, row in jobs.iterrows():
            score    = int(row.get("relevance_score") or 0)
            company  = str(row.get("company") or "—")
            title    = str(row.get("title") or "—")
            portal   = str(row.get("portal") or "—")
            job_id   = str(row["id"])
            jd_url   = str(row.get("jd_url") or "")
            location = str(row.get("location") or "")
            salary   = str(row.get("salary") or "")
            reason   = str(row.get("relevance_reason") or "")

            tailor_ok    = st.session_state.get(f"_tailor_ok_{job_id}", False)
            apply_result = st.session_state.get(f"_apply_result_{job_id}")
            support      = PORTAL_APPLY_SUPPORT.get(portal.lower(), "none")

            score_color = (
                "#52c41a" if score >= 75 else
                "#fa8c16" if score >= 60 else
                "#ff4d4f"
            )

            with st.container(border=True):
                info_col, score_col, action_col = st.columns([5, 1.5, 2.5])

                # ── Info column ─────────────────────────────────────────
                with info_col:
                    st.markdown(f"**{title}**")
                    meta_parts = [company, portal.upper()]
                    if location:
                        meta_parts.append(location)
                    if salary:
                        meta_parts.append(salary)
                    st.caption(" · ".join(meta_parts))
                    if reason:
                        st.markdown(
                            f'<p style="font-size:12px;color:#595959;font-style:italic;margin:4px 0 0 0">'
                            f'"{reason[:100]}{"…" if len(reason) > 100 else ""}"</p>',
                            unsafe_allow_html=True,
                        )

                # ── Score column ────────────────────────────────────────
                with score_col:
                    st.markdown(
                        f'<div class="job-score">'
                        f'<span class="num" style="color:{score_color}">{score}</span>'
                        f'<span class="lbl">match</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                # ── Action column ───────────────────────────────────────
                with action_col:
                    if apply_result and apply_result.success:
                        # Already applied — just show view link
                        if jd_url:
                            st.link_button(
                                f"View on {portal.title()} →",
                                url=jd_url,
                                use_container_width=True,
                            )
                    elif tailor_ok:
                        # Resume tailored — show apply options
                        if support != "none":
                            b1, b2 = st.columns(2)
                            with b1:
                                if st.button(
                                    "🚀 Auto",
                                    key=f"auto_{job_id}",
                                    type="primary",
                                    use_container_width=True,
                                ):
                                    st.session_state[f"_apply_run_{job_id}"] = True
                            with b2:
                                if jd_url:
                                    st.link_button(
                                        "Apply →",
                                        url=jd_url,
                                        use_container_width=True,
                                    )
                        else:
                            if jd_url:
                                st.link_button(
                                    f"📝 Apply →",
                                    url=jd_url,
                                    use_container_width=True,
                                )
                    else:
                        # Not yet tailored
                        if st.button(
                            "🎯 Tailor",
                            key=f"tailor_{job_id}",
                            type="primary",
                            use_container_width=True,
                        ):
                            st.session_state[f"_tailor_run_{job_id}"] = True

                # ── Status line (inside card, below 3-col layout) ───────
                if tailor_ok and not (apply_result and apply_result.success):
                    st.markdown(
                        '<p class="job-status tailored">📄 Resume tailored · ready to apply</p>',
                        unsafe_allow_html=True,
                    )
                elif apply_result and apply_result.success:
                    st.markdown(
                        f'<p class="job-status applied">✅ Auto-applied via {portal.title()}</p>',
                        unsafe_allow_html=True,
                    )
                elif apply_result and not apply_result.success:
                    msg = apply_result.message[:80]
                    st.markdown(
                        f'<p class="job-status warning">⚠️ {msg} — apply manually above</p>',
                        unsafe_allow_html=True,
                    )

                # ── JD expander (secondary, at card bottom) ─────────────
                with st.expander("View job description"):
                    jd_text = str(row.get("jd_text") or "")
                    if jd_text:
                        st.markdown(jd_text[:2000] + ("…" if len(jd_text) > 2000 else ""))
                    else:
                        st.caption("No job description available.")

                # ── Execute tailor (after button click above) ────────────
                if st.session_state.get(f"_tailor_run_{job_id}"):
                    del st.session_state[f"_tailor_run_{job_id}"]
                    with st.spinner(f"Tailoring resume for {company}…"):
                        result = _do_tailor(job_id)
                    failed = "failed" in result.lower() or "not found" in result.lower()
                    if failed:
                        st.error(result[:200])
                    else:
                        st.session_state[f"_tailor_ok_{job_id}"]  = True
                        st.session_state[f"_tailor_msg_{job_id}"]  = result
                        st.rerun()

                # ── Execute auto-apply (after Auto button click) ─────────
                if st.session_state.get(f"_apply_run_{job_id}"):
                    del st.session_state[f"_apply_run_{job_id}"]
                    tailor_msg = st.session_state.get(f"_tailor_msg_{job_id}", "")
                    pdf_path = ""
                    for line in tailor_msg.splitlines():
                        if line.startswith("PDF:"):
                            pdf_path = line.split("PDF:", 1)[1].strip()
                            break
                    if not pdf_path:
                        from job_agent.applier import ApplyResult
                        ar = ApplyResult(False, "manual_fallback",
                            "Could not locate resume PDF — please re-tailor this job.", jd_url)
                        st.session_state[f"_apply_result_{job_id}"] = ar
                        st.rerun()
                    with st.spinner(f"Auto-applying to {company} via {portal}…"):
                        ar = try_auto_apply(dict(row), pdf_path)
                        st.session_state[f"_apply_result_{job_id}"] = ar
                    if ar.success:
                        with db.get_db() as conn:
                            app_row = get_application_by_job(conn, job_id)
                            if app_row:
                                db.update_application(conn, dict(app_row)["id"], apply_method="auto")
                    st.rerun()
