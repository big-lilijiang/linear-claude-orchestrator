#!/usr/bin/env python3
"""
Post-result reporter: called by worker after Claude Code finishes.
Reads claude output, posts comment to Linear, moves issue to Done or Todo.

Usage:
  python3 post_result.py <issue_id> <issue_identifier>
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("LCO_LINEAR_API_KEY", os.environ.get("LINEAR_API_KEY", ""))
ENDPOINT = "https://api.linear.app/graphql"
BOT_MARKER = os.environ.get("LCO_BOT_MARKER", "🤖 Claude Code")
LANG = os.environ.get("LCO_LANGUAGE", "zh")


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


def add_comment(issue_id: str, body: str) -> None:
    gql(
        'mutation($id: String!, $body: String!) { commentCreate(input: { issueId: $id, body: $body }) { success } }',
        {"id": issue_id, "body": body},
    )


def move_to(issue_id: str, state: str) -> None:
    states = gql("{ workflowStates { nodes { id name } } }")["workflowStates"]["nodes"]
    sid = next((s["id"] for s in states if s["name"] == state), None)
    if sid:
        gql('mutation($id: String!, $sid: String!) { issueUpdate(id: $id, input: { stateId: $sid }) { issue { id } } }', {"id": issue_id, "sid": sid})


def main():
    if len(sys.argv) < 3:
        sys.exit("Usage: post_result.py <issue_id> <issue_identifier>")
    issue_id, ident = sys.argv[1], sys.argv[2]

    # Read claude output from workspace
    ws = Path(os.environ.get("LCO_WORKSPACE_ROOT", "~/.lco_workspaces")).expanduser().resolve()
    output_file = ws / ident.replace(" ", "_") / "output.txt"
    output = output_file.read_text(encoding="utf-8", errors="replace") if output_file.exists() else "(no output)"

    # Determine success
    ok = "exit 0" not in output.lower()[:100] and len(output.strip()) > 0
    # Better: check actual exit code if available
    if output.startswith("[exit "):
        ok = False

    status = "完成" if ok else "失败" if LANG == "zh" else "OK" if ok else "FAILED"
    comment = f"{BOT_MARKER} 执行结果: {status}\n\n{output}"
    if len(comment) > 10000:
        comment = comment[:9900] + "\n\n..."

    add_comment(issue_id, comment)
    move_to(issue_id, "Done" if ok else "Todo")

    # Cleanup
    output_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
