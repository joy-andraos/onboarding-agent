import base64
import os

import requests
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("codebase-onboarder", log_level="ERROR")

GITHUB_API = "https://api.github.com"

NOISE_DIR_NAMES = {
    ".git", "node_modules", "dist", "build", "out", "target",
    ".next", ".venv", "venv", "__pycache__", ".pytest_cache",
    "coverage", ".turbo", "vendor", ".idea", ".vscode",
}
NOISE_FILE_SUFFIXES = (
    ".lock", ".lockb", ".min.js", ".min.css", ".map",
)
NOISE_FILE_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", 
    "poetry.lock", "Cargo.lock", ".DS_Store",
}


def _github_headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _default_branch(owner: str, repo: str) -> str:
    resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=_github_headers(), timeout=15)
    resp.raise_for_status()
    return resp.json().get("default_branch", "main")


def _is_noise(path: str) -> bool:
    parts = path.split("/")
    if any(p in NOISE_DIR_NAMES for p in parts[:-1]):
        return True
    filename = parts[-1]
    if filename in NOISE_FILE_NAMES:
        return True
    if any(filename.endswith(suf) for suf in NOISE_FILE_SUFFIXES):
        return True
    return False


@mcp.tool(name="get_repo_structure", description="Fetch a GitHub repo's file tree and return it as an indented text tree.")
def get_repo_structure(
    owner: str,
    repo: str,
    branch: str | None = None,
    max_entries: int = 400,
) -> str:
    branch = branch or _default_branch(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{branch}"
    resp = requests.get(url, headers=_github_headers(), params={"recursive": "1"}, timeout=15)

    if resp.status_code == 404:
        return (
            f"Branch '{branch}' or repo '{owner}/{repo}' not found. "
        )
    if resp.status_code == 403:
        return "GitHub API rate limit hit. Set a GITHUB_TOKEN env var to raise the limit."
    resp.raise_for_status()

    data = resp.json()
    tree = data.get("tree", [])

    paths = sorted(
        item["path"] for item in tree
        if item.get("type") in ("blob", "tree") and not _is_noise(item["path"])
    )

    paths = paths[:max_entries]

    if data.get("truncated"):
        paths.append("... (tree truncated by GitHub API; repo is very large)")

    if not paths:
        return "No files found."

    # Render as an indented tree instead of a flat path list
    lines = []
    for path in paths:
        depth = path.count("/")
        name = path.rsplit("/", 1)[-1]
        lines.append("  " * depth + name)

    return "\n".join(lines)


@mcp.tool(name="get_readme", description="Fetch and decode the README for a public GitHub repo.")
def get_readme(
    owner: str, 
    repo: str, 
    branch: str | None = None
) -> str:
    branch = branch or _default_branch(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/readme"
    resp = requests.get(url, headers=_github_headers(), params={"ref": branch}, timeout=15)

    if resp.status_code == 404:
        return f"No README found for '{owner}/{repo}' on branch '{branch}'."
    if resp.status_code == 403:
        return "GitHub API rate limit hit. Set a GITHUB_TOKEN env var to raise the limit."
    resp.raise_for_status()

    data = resp.json()
    content = data.get("content", "")
    encoding = data.get("encoding", "base64")

    if encoding != "base64":
        return content  # unexpected, but don't crash

    decoded = base64.b64decode(content).decode("utf-8", errors="replace")
    return decoded


@mcp.tool(name="get_file_content", description="Fetch and decode a specific file from a public GitHub repo.")
def get_file_content(
    owner: str,
    repo: str,
    file_path: str,
    branch: str | None = None
) -> str:
    branch = branch or _default_branch(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/contents/{file_path}"
    resp = requests.get(url, headers=_github_headers(), params={"ref": branch}, timeout=15)

    if resp.status_code == 404:
        return f"File not found for '{owner}/{repo}' on branch '{branch}'."
    if resp.status_code == 403:
        return "GitHub API rate limit hit. Set a GITHUB_TOKEN env var to raise the limit."
    resp.raise_for_status()

    data = resp.json()
    content = data.get("content", "")
    encoding = data.get("encoding", "base64")

    if encoding != "base64":
        return content  # unexpected, but don't crash

    decoded = base64.b64decode(content).decode("utf-8", errors="replace")
    return decoded

@mcp.tool(name="get_open_issues", description="Fetch a list of open issues for a public GitHub repo (excludes pull requests).")
def get_open_issues(
    owner: str,
    repo: str,
    state: str = "open",
    max_issues: int = 20
) -> list[dict]:
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues"
    resp = requests.get(url, headers=_github_headers(), params={"state": state, "per_page": max_issues}, timeout=15)

    if resp.status_code == 404:
        return []
    if resp.status_code == 403:
        return [{"error": "GitHub API rate limit hit. Set a GITHUB_TOKEN env var to raise the limit."}]
    resp.raise_for_status()

    issues = [i for i in resp.json() if "pull_request" not in i]
    return [
        {
            "number": issue.get("number"),
            "title": issue.get("title"),
            "html_url": issue.get("html_url"),
            "labels": issue.get("labels", []),
            "comments": issue.get("comments", 0),
        }
        for issue in issues[:max_issues]
    ]


@mcp.tool(name="get_recent_commits", description="Fetch a list of recent commits for a public GitHub repo.")
def get_recent_commits(
    owner: str,
    repo: str,
    branch: str | None = None,
    max_commits: int = 20
) -> list[dict]:
    branch = branch or _default_branch(owner, repo)
    url = f"{GITHUB_API}/repos/{owner}/{repo}/commits"
    resp = requests.get(url, headers=_github_headers(), params={"sha": branch, "per_page": max_commits}, timeout=15)

    if resp.status_code == 404:
        return []
    if resp.status_code == 403:
        return [{"error": "GitHub API rate limit hit. Set a GITHUB_TOKEN env var to raise the limit."}]
    resp.raise_for_status()

    commits = resp.json()
    return [
        {
            "sha": commit.get("sha"),
            "message": commit.get("commit", {}).get("message", "").splitlines()[0],
            "author": commit.get("commit", {}).get("author", {}).get("name"),
            "date": commit.get("commit", {}).get("author", {}).get("date"),
        }
        for commit in commits[:max_commits]
    ]

if __name__ == "__main__":
    mcp.run()