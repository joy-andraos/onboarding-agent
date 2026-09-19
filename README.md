```bash
uv init
```

```bash
uv add "mcp[cli]" fastapi uvicorn requests anthropic python-dotenv
```

# Codebase Onboarder

An MCP server + Agent Skill that turns "I just joined this repo, where do I even start" into a structured onboarding doc.

- **MCP server** (`mcp_server/server.py`) exposes two tools that pull real data from the GitHub API:
  - `get_repo_structure(owner, repo, branch="main")` — a cleaned-up file tree (node_modules, build output, lockfiles, etc. filtered out)
  - `get_readme(owner, repo, branch="main")` — the decoded README
- **Skill** (`skills/codebase-onboarding/SKILL.md`) tells Claude how to turn that raw data into a genuinely useful onboarding doc: a fixed structure (what it does → where to start reading → key abstractions → gotchas → suggested first task), with rules against generic AI-summary filler.

## Setup

```bash
uv sync
source .venv/bin/activate
uvicorn main:app --reload
```

Optional: set `GITHUB_TOKEN` to raise GitHub's rate limit (60 req/hr unauthenticated) or to access private repos you have access to.

```bash
export GITHUB_TOKEN=ghp_your_token_here
```