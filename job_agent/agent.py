"""
agent.py — LangGraph agentic loop. OpenAI gpt-4o-mini drives the plan;
Groq handles scoring and gap analysis inside the tools.

Usage:
    python agent.py                                                    # interactive
    python agent.py --goal "Find backend AI jobs in India on LinkedIn"
"""
import argparse
import os
import sys
from pathlib import Path
from typing import Annotated, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from rich.console import Console
from rich.panel import Panel

load_dotenv(Path(__file__).parent.parent / ".env")

from .tools import ALL_TOOLS

console = Console()

SYSTEM_PROMPT = """You are Nehal Mehta's personal job application agent. Your mission: find relevant engineering roles, evaluate fit, and help prepare tailored applications efficiently.

Nehal's profile:
- 6 years backend engineer — Node.js, TypeScript, Python, NestJS, FastAPI, GraphQL, Microservices
- AI/ML: RAG pipelines, LLM API integration, agentic systems, vector DBs, LangChain
- Data: PostgreSQL, MongoDB, Redis, Kafka, InfluxDB, AWS (Lambda, S3, Neptune)
- Target roles: Backend Engineer (AI), Full Stack AI Developer, Tech Lead
- Target salary: 40-50 LPA | Location: Delhi, India | Open to remote/hybrid

Your tools and when to use them:
- search_jobs — when asked to find jobs; use relevant AI/backend query terms
- list_jobs — after searching, or when asked what's in the pipeline
- tailor_resume — for jobs scoring >= 75; always needs user confirmation (built into the tool)
- update_application — after Nehal confirms he submitted an application
- get_stats — when asked for a summary or progress overview

Working style:
1. Think step by step but be concise — one short sentence before each tool call explaining what you're doing and why.
2. After search_jobs, always follow up with list_jobs to show results.
3. For jobs scoring >= 75, proactively suggest tailoring (but let the tool handle the Y/N prompt).
4. After tailoring, remind Nehal to apply manually and update status when done.
5. If a task fails, explain briefly and suggest the next sensible step.
"""


class State(TypedDict):
    messages: Annotated[list, add_messages]


def _build_graph():
    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0,
    )
    llm_with_tools = llm.bind_tools(ALL_TOOLS)

    def agent_node(state: State) -> dict:
        response = llm_with_tools.invoke(state["messages"])
        return {"messages": [response]}

    def should_continue(state: State) -> str:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    graph = StateGraph(State)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(ALL_TOOLS))
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


def _stream(compiled_graph, messages: list) -> list:
    """Stream graph execution, printing new AI messages as they arrive."""
    seen = len(messages)
    final = list(messages)

    for chunk in compiled_graph.stream({"messages": messages}, stream_mode="values"):
        all_msgs = chunk["messages"]
        for msg in all_msgs[seen:]:
            if getattr(msg, "type", None) == "ai" and msg.content:
                console.print(f"\n[bold blue]Agent:[/bold blue] {msg.content}")
        seen = len(all_msgs)
        final = all_msgs

    return final


def run_agent(goal: Optional[str] = None):
    if "OPENAI_API_KEY" not in os.environ:
        console.print("[red]OPENAI_API_KEY not set. Add it to your .env file.[/red]")
        sys.exit(1)

    graph = _build_graph()
    messages: list = [SystemMessage(content=SYSTEM_PROMPT)]

    if goal:
        # One-shot mode
        console.print(Panel(f"[bold cyan]Goal:[/bold cyan] {goal}", expand=False))
        messages.append(HumanMessage(content=goal))
        _stream(graph, messages)
        return

    # Interactive / conversational mode
    console.print(Panel(
        "[bold cyan]Job Agent[/bold cyan]  (agentic mode)\n"
        "[dim]Type your goal. 'quit' to exit.[/dim]",
        expand=False,
    ))

    while True:
        try:
            user_input = console.input("\n[bold green]You:[/bold green] ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Goodbye.[/dim]")
            break

        if not user_input or user_input.lower() in ("quit", "exit", "q"):
            console.print("[dim]Goodbye.[/dim]")
            break

        messages.append(HumanMessage(content=user_input))
        messages = _stream(graph, messages)


def main():
    parser = argparse.ArgumentParser(description="Agentic job application orchestrator")
    parser.add_argument("--goal", help="One-line goal (omit for interactive mode)")
    args = parser.parse_args()
    run_agent(args.goal)


if __name__ == "__main__":
    main()
