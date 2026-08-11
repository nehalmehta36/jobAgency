"""
run.py — Entry point for the agentic job pipeline.
Delegates to agent.py which drives the loop via LangGraph + OpenAI gpt-4o-mini.

Usage:
    python run.py                                                    # interactive
    python run.py --goal "Find backend AI jobs in India on LinkedIn"
"""
from job_agent.agent import main

if __name__ == "__main__":
    main()
