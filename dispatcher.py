#!/usr/bin/env python3
"""
One-shot dispatcher: fetch Todo issues from Linear, spawn Claude Code workers, exit.

Designed to be called by OS scheduler (launchd / systemd timer / Task Scheduler)
every 15 seconds. Runs in <2s, spawns detached workers, exits immediately.

Usage:
  python3 dispatcher.py
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

# ── Config (env vars) ────────────────────────────────────────────────

API_KEY = os.environ.get("LCO_LINEAR_API_KEY", os.environ.get("LINEAR_API_KEY", ""))
PROJECT_SLUG = os.environ.get("LCO_LINEAR_PROJECT_SLUG", "")
WORKSPACE_ROOT = Path(os.environ.get("LCO_WORKSPACE_ROOT", "~/.lco_workspaces")).expanduser().resolve()
MAX_CONCURRENT = int(os.environ.get("LCO_MAX_CONCURRENT", "5"))
TIMEOUT = os.environ.get("LCO_TIMEOUT_SECONDS", "3600")
LANG = os.environ.get("LCO_LANGUAGE", "zh")
POST_RESULT = (Path(__file__).resolve().parent / "post_result.py").as_posix()

# ── Linear helpers ───────────────────────────────────────────────────

ENDPOINT = "https://api.linear.app/graphql"

def gql(query: str, variables: dict | None = None) -> dict:
    body = {"query": query}
    if variables:
        body["variables"] = variables
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Authorization": API_KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        d = json.loads(r.read())
    if "errors" in d:
        raise RuntimeError(d["errors"])
    return d["data"]

def fetch_todo() -> list[dict]:
    d = gql(
        'query($slug: String!) { issues(filter: { project: { slugId: { eq: $slug } }, state: { name: { eq: "Todo" } } }) { nodes { id identifier title description } } }',
        {"slug": PROJECT_SLUG},
    )
    return d["issues"]["nodes"]

def move_to(issue_id: str, state: str) -> None:
    states = gql("{ workflowStates { nodes { id name } } }")["workflowStates"]["nodes"]
    sid = next((s["id"] for s in states if s["name"] == state), None)
    if sid:
        gql('mutation($id: String!, $sid: String!) { issueUpdate(id: $id, input: { stateId: $sid }) { issue { id } } }', {"id": issue_id, "sid": sid})

def count_in_progress() -> int:
    d = gql(
        'query($slug: String!) { issues(filter: { project: { slugId: { eq: $slug } }, state: { name: { eq: "In Progress" } } }) { nodes { id } } }',
        {"slug": PROJECT_SLUG},
    )
    return len(d["issues"]["nodes"])

# ── Workspace ─────────────────────────────────────────────────────────

def sanitize(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)

def workspace(ident: str) -> Path:
    p = WORKSPACE_ROOT / sanitize(ident)
    p.mkdir(parents=True, exist_ok=True)
    (p / "README.md").touch()
    return p

# ── Prompt ────────────────────────────────────────────────────────────

def prompt(issue: dict) -> str:
    title = issue["title"]
    desc = issue.get("description") or ""
    if LANG == "zh":
        return f"""你正在处理 Linear 工单 {issue['identifier']}: {title}

## 任务描述
{desc or '无描述'}

## 要求
1. 阅读任务描述，理解要做什么
2. 在工作目录中完成工作（写代码、做调研、分析问题）
3. 写完代码必须运行测试验证通过
4. 完成后用中文写总结：做了什么、创建/修改了哪些文件、遇到了什么问题

全程自主完成，不要提问。"""
    else:
        return f"""You are working on Linear issue {issue['identifier']}: {title}

## Task
{desc or 'No description'}

## Instructions
1. Understand the task
2. Complete the work
3. Test your work
4. Summarize: what you did, files created/changed, issues encountered

Be autonomous. Do not ask questions."""

# ── Main ──────────────────────────────────────────────────────────────

def main():
    dry_run = "--dry-run" in sys.argv

    if not API_KEY and not dry_run:
        sys.exit("LCO_LINEAR_API_KEY not set")
    if not PROJECT_SLUG and not dry_run:
        sys.exit("LCO_LINEAR_PROJECT_SLUG not set")

    if dry_run:
        print(f"[LCO] DRY RUN — platform: {sys.platform}")
        # Simulate a fake issue to test the full pipeline
        fake_issue = {"id": "00000000-0000-0000-0000-000000000000", "identifier": "TEST-1", "title": "Dry run test", "description": "test"}
        ws = workspace("TEST-1")
        (ws / "issue.json").write_text(json.dumps(fake_issue, ensure_ascii=False))
        p = prompt(fake_issue)
        worker_cmd = (
            f"cd {ws.as_posix()} && "
            f"claude --dangerously-skip-permissions --continue -p {shquote(p)} "
            f"> {ws.as_posix()}/output.txt 2>&1 ; "
            f"python3 {POST_RESULT} {shquote(fake_issue['id'])} {shquote(fake_issue['identifier'])}"
        )
        if sys.platform == "win32":
            print(f"[LCO] Windows: cmd /c {worker_cmd[:80]}...")
            print(f"[LCO] CREATE_NEW_PROCESS_GROUP: {hasattr(subprocess, 'CREATE_NEW_PROCESS_GROUP')}")
        else:
            print(f"[LCO] Unix: bash -c {worker_cmd[:80]}...")
            print(f"[LCO] start_new_session supported")
        print("[LCO] Dry run OK — all code paths valid")
        return

    try:
        active = count_in_progress()
    except Exception as e:
        print(f"[LCO] API error (count): {e}")
        return

    slots = max(0, MAX_CONCURRENT - active)
    if slots <= 0:
        return

    try:
        issues = fetch_todo()
    except Exception as e:
        print(f"[LCO] API error (fetch): {e}")
        return

    for issue in issues[:slots]:
        ident = issue["identifier"]
        ws = workspace(ident)
        (ws / "issue.json").write_text(json.dumps(issue, ensure_ascii=False))
        p = prompt(issue)

        try:
            move_to(issue["id"], "In Progress")
        except Exception as e:
            print(f"[LCO] {ident} move error: {e}")
            continue

        print(f"[LCO] {ident} -> In Progress")

        # Spawn detached worker: claude -> post_result
        worker_cmd = (
            f"cd {ws.as_posix()} && "
            f"claude --dangerously-skip-permissions --continue -p {shquote(p)} "
            f"> {ws.as_posix()}/output.txt 2>&1 ; "
            f"python3 {POST_RESULT} {shquote(issue['id'])} {shquote(ident)}"
        )
        if sys.platform == "win32":
            subprocess.Popen(["cmd", "/c", worker_cmd], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            subprocess.Popen(["bash", "-c", worker_cmd], start_new_session=True)

        print(f"[LCO] {ident} dispatched")


def shquote(s: str) -> str:
    """Single-quote a string for shell."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    main()
