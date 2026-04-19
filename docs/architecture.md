# Architecture

## Positioning

Damon is a sidecar supervisor for Codex.

It is not a replacement for Codex planning or coding. The user continues to work in Codex directly. Damon only adds lightweight control around that session:

- attach to an existing Codex session
- continue that session repeatedly with minimal operator intervention
- record checkpoints and blockers locally
- hand the current state off into a GitLab merge request

## Core Loop

1. The user plans in Codex and leaves plan/architecture files inside the repository.
2. `damon attach` binds the repository to the latest Codex session for that repo.
3. `damon loop N` resumes that session repeatedly.
4. Each loop asks Codex to keep working until one of:
   - checkpoint
   - done
   - blocker
5. Damon records the result into `.damon/sidecar.yaml`.
6. `damon pr` asks Codex to summarize the current state and opens a GitLab merge request.

## Stored State

- `.damon/sidecar.yaml`
  - attached session
  - working branch
  - target branch
  - loop history
  - latest status

- `.damon/reports/*.json`
  - PR reports and machine-readable outputs

- `.damon/blocked.md`
  - blocked handoff note when Codex reports a blocker

## Design Goal

The design goal is simple:

- keep Codex as the main UX
- remove repeated "continue" prompts
- let the operator check progress occasionally instead of driving every step
