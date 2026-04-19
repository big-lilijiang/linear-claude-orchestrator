# Damon AutoCoding

`Damon AutoCoding` is now a thin supervisor around Codex.

It does not try to replace the native Codex experience. You keep planning and coding in Codex as usual. Damon only adds four things:

- bind the current repository to an existing Codex session
- auto-continue multiple checkpoints without manual "continue" prompts
- show progress/status from a local state file
- turn the current result into a GitLab merge request

## Commands

```bash
damon attach
damon loop 10
damon status
damon pr
```

## Command Model

- `damon attach`
  - Find the latest Codex session for the current repository and bind it into `.damon/sidecar.yaml`.
  - If you are still on the target branch, Damon creates a `damon/...` working branch automatically.

- `damon loop 10`
  - Resume the attached Codex session up to 10 checkpoints.
  - Each step asks Codex to keep working until a natural checkpoint, completion, or a hard blocker.
  - Damon records each step into the local sidecar state.

- `damon status`
  - Show the attached session, working branch, latest loop status, recent steps, and latest PR report.

- `damon pr`
  - Ask Codex to summarize the current repository state as a complete or blocked PR.
  - Push the current branch to GitLab and create/update the merge request.

## State

Damon stores local supervisor state under:

```text
.damon/sidecar.yaml
.damon/reports/*.json
.damon/blocked.md
```

## Notes

- Damon assumes you already use Codex directly for planning and implementation.
- Damon is intentionally thin. It should reduce repeated "keep going" prompts, not replace your workflow.
- GitLab configuration is inferred from the current repository remote.
