from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, Field

from .config import dump_yaml, load_model, save_model
from .gitlab import GitLabDelivery, MergeRequestSpec
from .models import (
    CommitStrategy,
    DeliveryPolicy,
    EscalationPolicy,
    ExecutionPolicy,
    ExecutionSettings,
    GitLabPolicy,
    GitPolicy,
    PathConstraints,
    PlanningPolicy,
    RepositoryContext,
    TaskDeliverables,
    TaskHandoff,
    TaskInputs,
    TaskPlan,
    VerificationPolicy,
    WorkerTask,
)
from .project import DeliveryOptions, GitLabProject, ProjectConfig
from .repo_profile import CommandSpec, RepositoryProfile
from .task_runner import TaskRunner, dump_task_run_report
from .workers import CodexCLIWorker
from .workspace import GitWorktreeManager


class RunStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    EXECUTING = "executing"
    EXECUTED = "executed"
    COMPLETE_PR = "complete_pr"
    BLOCKED_PR = "blocked_pr"


class RepoScanSummary(BaseModel):
    repo_root: str
    repo_name: str
    current_branch: str | None = None
    default_branch: str = "main"
    remote_name: str = "origin"
    remote_url: str | None = None
    top_level_entries: list[str] = Field(default_factory=list)
    detected_stack: list[str] = Field(default_factory=list)
    make_targets: list[str] = Field(default_factory=list)
    suggested_lint_commands: list[str] = Field(default_factory=list)
    suggested_test_commands: list[str] = Field(default_factory=list)
    suggested_static_analysis_commands: list[str] = Field(default_factory=list)


class PlanningAnswers(BaseModel):
    goal: str
    title: str
    scope_items: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    architecture_notes: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    definition_of_done: list[str] = Field(default_factory=list)
    lint_commands: list[str] = Field(default_factory=list)
    test_commands: list[str] = Field(default_factory=list)
    static_analysis_commands: list[str] = Field(default_factory=list)
    target_branch: str = "main"
    base_ref: str | None = None
    working_branch: str
    run_review: bool = False
    auto_push_complete_pr: bool = True
    auto_push_blocked_pr: bool = True
    draft_merge_request: bool = True


class RunManifest(BaseModel):
    version: str = "0.1"
    run_id: str
    created_at: str
    repo_root: str
    planning_mode: str = "interactive"
    status: RunStatus = RunStatus.DRAFT
    goal: str
    title: str
    scan: RepoScanSummary
    answers: PlanningAnswers
    latest_execute_report: str | None = None
    latest_delivery_report: str | None = None


@dataclass(slots=True)
class RunPaths:
    repo_root: Path
    run_id: str

    @property
    def root(self) -> Path:
        return self.repo_root / ".damon" / "runs" / self.run_id

    @property
    def dossier_dir(self) -> Path:
        return self.root / "dossier"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def manifest_path(self) -> Path:
        return self.root / "run.yaml"

    @property
    def goal_path(self) -> Path:
        return self.dossier_dir / "goal.md"

    @property
    def architecture_path(self) -> Path:
        return self.dossier_dir / "architecture.md"

    @property
    def repo_scan_path(self) -> Path:
        return self.dossier_dir / "repo_scan.md"

    @property
    def constraints_path(self) -> Path:
        return self.dossier_dir / "constraints.yaml"

    @property
    def definition_of_done_path(self) -> Path:
        return self.dossier_dir / "definition_of_done.yaml"

    @property
    def delivery_policy_path(self) -> Path:
        return self.dossier_dir / "delivery_policy.yaml"

    @property
    def task_graph_path(self) -> Path:
        return self.dossier_dir / "task_graph.yaml"

    @property
    def project_path(self) -> Path:
        return self.dossier_dir / "project.yaml"

    @property
    def profile_path(self) -> Path:
        return self.dossier_dir / "repository_profile.yaml"

    @property
    def policy_path(self) -> Path:
        return self.dossier_dir / "execution_policy.yaml"

    @property
    def task_path(self) -> Path:
        return self.dossier_dir / "task_contract.yaml"


class PlannerIO:
    def __init__(self, *, stdin: TextIO, stdout: TextIO) -> None:
        self.stdin = stdin
        self.stdout = stdout

    def line(self, text: str = "") -> None:
        self.stdout.write(f"{text}\n")
        self.stdout.flush()

    def ask_text(self, prompt: str, *, default: str | None = None) -> str:
        suffix = f" [{default}]" if default else ""
        self.stdout.write(f"{prompt}{suffix}: ")
        self.stdout.flush()
        answer = self.stdin.readline()
        if answer == "":
            return default or ""
        value = answer.rstrip("\n").strip()
        return value or (default or "")

    def ask_yes_no(self, prompt: str, *, default: bool = True) -> bool:
        marker = "Y/n" if default else "y/N"
        while True:
            raw = self.ask_text(f"{prompt} ({marker})", default="")
            if not raw:
                return default
            lowered = raw.lower()
            if lowered in {"y", "yes"}:
                return True
            if lowered in {"n", "no"}:
                return False
            self.line("Please answer yes or no.")

    def ask_list(self, prompt: str, *, default: list[str] | None = None) -> list[str]:
        self.line(prompt)
        self.line("Enter one item per line. Submit an empty line to finish.")
        if default:
            self.line(f"Default: {', '.join(default)}")
        items: list[str] = []
        while True:
            self.stdout.write("> ")
            self.stdout.flush()
            line = self.stdin.readline()
            if line == "":
                break
            value = line.rstrip("\n").strip()
            if not value:
                break
            items.append(value)
        return items or list(default or [])

    def ask_commands(self, prompt: str, *, default: list[str] | None = None) -> list[str]:
        default_text = "; ".join(default or [])
        answer = self.ask_text(prompt, default=default_text)
        if not answer:
            return []
        return [item.strip() for item in answer.split(";") if item.strip()]


class RepositoryInspector:
    def inspect(self, repo_root: str | Path) -> RepoScanSummary:
        repo_root = Path(repo_root).resolve()
        top_level_entries = sorted(entry.name for entry in repo_root.iterdir() if entry.name != ".git")
        current_branch = self._git_output(repo_root, ["branch", "--show-current"], check=False) or None
        default_branch = self._detect_default_branch(repo_root) or current_branch or "main"
        remote_name = self._detect_remote_name(repo_root) or "origin"
        remote_url = self._git_output(repo_root, ["remote", "get-url", remote_name], check=False) or None
        make_targets = self._detect_make_targets(repo_root / "Makefile")
        detected_stack = self._detect_stack(repo_root)
        lint_commands, test_commands, static_analysis_commands = self._suggest_commands(repo_root, make_targets)
        return RepoScanSummary(
            repo_root=str(repo_root),
            repo_name=repo_root.name,
            current_branch=current_branch,
            default_branch=default_branch,
            remote_name=remote_name,
            remote_url=remote_url,
            top_level_entries=top_level_entries,
            detected_stack=detected_stack,
            make_targets=make_targets,
            suggested_lint_commands=lint_commands,
            suggested_test_commands=test_commands,
            suggested_static_analysis_commands=static_analysis_commands,
        )

    def _detect_stack(self, repo_root: Path) -> list[str]:
        stack: list[str] = []
        if (repo_root / "pyproject.toml").exists() or (repo_root / "requirements.txt").exists():
            stack.append("python")
        if (repo_root / "package.json").exists():
            stack.append("node")
        if (repo_root / "go.mod").exists():
            stack.append("go")
        if (repo_root / "Cargo.toml").exists():
            stack.append("rust")
        if (repo_root / "pom.xml").exists() or (repo_root / "build.gradle").exists() or (repo_root / "build.gradle.kts").exists():
            stack.append("java")
        return stack or ["unknown"]

    def _suggest_commands(self, repo_root: Path, make_targets: list[str]) -> tuple[list[str], list[str], list[str]]:
        lint_commands: list[str] = []
        test_commands: list[str] = []
        static_analysis_commands: list[str] = []

        if "lint" in make_targets:
            lint_commands.append("make lint")
        if "test" in make_targets:
            test_commands.append("make test")
        if "check" in make_targets:
            static_analysis_commands.append("make check")

        package_json = repo_root / "package.json"
        if package_json.exists():
            try:
                payload = json.loads(package_json.read_text(encoding="utf-8"))
                scripts = payload.get("scripts", {})
                if "lint" in scripts:
                    lint_commands.append("npm run lint")
                if "test" in scripts:
                    test_commands.append("npm test")
                if "typecheck" in scripts:
                    static_analysis_commands.append("npm run typecheck")
            except json.JSONDecodeError:
                pass

        if (repo_root / "pyproject.toml").exists():
            if (repo_root / "src").exists() and (repo_root / "tests").exists():
                lint_commands.append("python3 -m compileall src tests")
            elif (repo_root / "tests").exists():
                lint_commands.append("python3 -m compileall .")
            if (repo_root / "tests").exists():
                test_commands.append("PYTHONPATH=src python3 -m unittest discover -s tests -v")

        return (
            list(dict.fromkeys(lint_commands)),
            list(dict.fromkeys(test_commands)),
            list(dict.fromkeys(static_analysis_commands)),
        )

    def _detect_make_targets(self, makefile_path: Path) -> list[str]:
        if not makefile_path.exists():
            return []
        targets: list[str] = []
        for line in makefile_path.read_text(encoding="utf-8").splitlines():
            if ":" not in line or line.startswith(("\t", "#", ".")):
                continue
            target = line.split(":", 1)[0].strip()
            if target and " " not in target:
                targets.append(target)
        return sorted(dict.fromkeys(targets))

    def _detect_remote_name(self, repo_root: Path) -> str | None:
        remotes = self._git_output(repo_root, ["remote"], check=False)
        if not remotes:
            return None
        values = [line.strip() for line in remotes.splitlines() if line.strip()]
        if "origin" in values:
            return "origin"
        return values[0] if values else None

    def _detect_default_branch(self, repo_root: Path) -> str | None:
        ref = self._git_output(repo_root, ["symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
        if ref and "/" in ref:
            return ref.rsplit("/", 1)[-1]
        for candidate in ("refs/remotes/origin/main", "refs/heads/main", "refs/remotes/origin/master", "refs/heads/master"):
            if self._ref_exists(repo_root, candidate):
                return candidate.rsplit("/", 1)[-1]
        return None

    def _ref_exists(self, repo_root: Path, ref: str) -> bool:
        completed = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0

    def _git_output(self, repo_root: Path, args: list[str], *, check: bool) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
        if completed.returncode != 0:
            return ""
        return completed.stdout.strip()


class RunManager:
    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.runs_dir = self.repo_root / ".damon" / "runs"

    def create_paths(self, run_id: str) -> RunPaths:
        return RunPaths(repo_root=self.repo_root, run_id=run_id)

    def create_run_id(self, title: str) -> str:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        slug = (_slug(title)[:32]).strip("-") or "task"
        return f"RUN-{timestamp}-{slug}"

    def save_manifest(self, manifest: RunManifest) -> None:
        save_model(self.create_paths(manifest.run_id).manifest_path, manifest)

    def load_manifest(self, run_id: str) -> RunManifest:
        return load_model(self.create_paths(run_id).manifest_path, RunManifest)

    def latest_run_id(self) -> str:
        if not self.runs_dir.exists():
            raise FileNotFoundError("No runs found under .damon/runs")
        candidates = sorted(entry.name for entry in self.runs_dir.iterdir() if entry.is_dir())
        if not candidates:
            raise FileNotFoundError("No runs found under .damon/runs")
        return candidates[-1]

    def write_dossier(
        self,
        manifest: RunManifest,
        *,
        project: ProjectConfig,
        profile: RepositoryProfile,
        policy: ExecutionPolicy,
        task: WorkerTask,
    ) -> RunPaths:
        paths = self.create_paths(manifest.run_id)
        paths.root.mkdir(parents=True, exist_ok=True)
        paths.dossier_dir.mkdir(parents=True, exist_ok=True)
        paths.reports_dir.mkdir(parents=True, exist_ok=True)

        save_model(paths.project_path, project)
        save_model(paths.profile_path, profile)
        save_model(paths.policy_path, policy)
        save_model(paths.task_path, task)
        dump_yaml(
            paths.constraints_path,
            {
                "allowed_paths": manifest.answers.allowed_paths,
                "forbidden_paths": manifest.answers.forbidden_paths,
                "constraints": manifest.answers.constraints,
            },
        )
        dump_yaml(paths.definition_of_done_path, {"criteria": manifest.answers.definition_of_done})
        dump_yaml(
            paths.delivery_policy_path,
            {
                "auto_push_complete_pr": manifest.answers.auto_push_complete_pr,
                "auto_push_blocked_pr": manifest.answers.auto_push_blocked_pr,
                "draft_merge_request": manifest.answers.draft_merge_request,
            },
        )
        dump_yaml(
            paths.task_graph_path,
            {
                "tasks": [
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "working_branch": task.repository.working_branch,
                        "target_branch": task.repository.target_branch,
                        "depends_on": task.plan.depends_on,
                    }
                ]
            },
        )
        paths.goal_path.write_text(_render_goal_markdown(manifest), encoding="utf-8")
        paths.architecture_path.write_text(_render_architecture_markdown(manifest), encoding="utf-8")
        paths.repo_scan_path.write_text(_render_repo_scan_markdown(manifest.scan), encoding="utf-8")
        self.save_manifest(manifest)
        return paths

    def save_report(self, run_id: str, name: str, payload: dict) -> Path:
        paths = self.create_paths(run_id)
        paths.reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = paths.reports_dir / f"{name}.json"
        report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return report_path

    def load_report(self, report_path: str) -> dict:
        path = Path(report_path)
        if not path.is_absolute():
            path = self.repo_root / path
        return json.loads(path.read_text(encoding="utf-8"))


class StartFlow:
    def __init__(self, *, inspector: RepositoryInspector | None = None) -> None:
        self.inspector = inspector or RepositoryInspector()

    def run(self, *, repo_root: str | Path, goal: str | None, io: PlannerIO) -> tuple[RunManifest, RunPaths]:
        manager = RunManager(repo_root)
        scan = self.inspector.inspect(repo_root)

        io.line("")
        io.line("Repository Scan")
        io.line(f"- Repo: {scan.repo_name}")
        io.line(f"- Root: {scan.repo_root}")
        io.line(f"- Current branch: {scan.current_branch or 'unknown'}")
        io.line(f"- Default branch: {scan.default_branch}")
        io.line(f"- Remote: {scan.remote_name} -> {scan.remote_url or 'not configured'}")
        io.line(f"- Stack: {', '.join(scan.detected_stack)}")
        io.line(f"- Suggested lint: {'; '.join(scan.suggested_lint_commands) or 'none'}")
        io.line(f"- Suggested tests: {'; '.join(scan.suggested_test_commands) or 'none'}")
        io.line("")
        io.line("Planning Session")

        final_goal = goal or io.ask_text("Goal")
        title = io.ask_text("Short task title", default=_title_from_goal(final_goal))
        scope_items = io.ask_list("What is explicitly in scope?", default=[final_goal])
        non_goals = io.ask_list("What is explicitly out of scope?", default=["No unrelated refactors."])
        architecture_notes = io.ask_list(
            "Key architecture decisions or notes?",
            default=["Preserve existing patterns unless there is a clear reason to change them."],
        )
        allowed_paths = io.ask_list("Allowed paths or modules? Leave blank for repo-wide scope.", default=[])
        forbidden_paths = io.ask_list("Forbidden paths or modules? Leave blank if none.", default=[])
        constraints = io.ask_list(
            "Technical or business constraints?",
            default=["Avoid destructive operations outside policy."],
        )
        definition_of_done = io.ask_list(
            "Definition of done: what must be true before execution can finish?",
            default=["Relevant lint commands pass.", "Relevant tests pass.", "A GitLab merge request is ready for review."],
        )
        target_branch = io.ask_text("Target branch", default=scan.default_branch)
        base_ref = io.ask_text("Base ref for the task branch", default=scan.current_branch or target_branch)
        working_branch = io.ask_text("Working branch", default=f"damon/{_slug(title)}")
        lint_commands = io.ask_commands("Lint command(s), separated by ';'", default=scan.suggested_lint_commands)
        test_commands = io.ask_commands("Test command(s), separated by ';'", default=scan.suggested_test_commands)
        static_analysis_commands = io.ask_commands(
            "Static analysis command(s), separated by ';'. Leave blank if none.",
            default=scan.suggested_static_analysis_commands,
        )
        run_review = io.ask_yes_no("Run codex review after implementation?", default=False)
        auto_push_complete_pr = io.ask_yes_no("Push a complete PR automatically when execution succeeds?", default=True)
        auto_push_blocked_pr = io.ask_yes_no("Push a blocked PR automatically when execution fails?", default=True)
        draft_merge_request = io.ask_yes_no("Open merge requests as Draft?", default=True)

        answers = PlanningAnswers(
            goal=final_goal,
            title=title,
            scope_items=scope_items,
            non_goals=non_goals,
            architecture_notes=architecture_notes,
            allowed_paths=allowed_paths,
            forbidden_paths=forbidden_paths,
            constraints=constraints,
            definition_of_done=definition_of_done,
            lint_commands=lint_commands,
            test_commands=test_commands,
            static_analysis_commands=static_analysis_commands,
            target_branch=target_branch,
            base_ref=base_ref,
            working_branch=working_branch,
            run_review=run_review,
            auto_push_complete_pr=auto_push_complete_pr,
            auto_push_blocked_pr=auto_push_blocked_pr,
            draft_merge_request=draft_merge_request,
        )

        manifest = RunManifest(
            run_id=manager.create_run_id(title),
            created_at=datetime.now().isoformat(timespec="seconds"),
            repo_root=str(Path(repo_root).resolve()),
            goal=final_goal,
            title=title,
            scan=scan,
            answers=answers,
        )
        project = _build_project_config(scan, answers)
        profile = _build_repository_profile(answers)
        policy = _build_execution_policy(answers)
        task = _build_task_contract(manifest)
        paths = manager.write_dossier(manifest, project=project, profile=profile, policy=policy, task=task)

        io.line("")
        io.line("Dossier Summary")
        io.line(f"- Run ID: {manifest.run_id}")
        io.line(f"- Working branch: {answers.working_branch}")
        io.line(f"- Dossier: {paths.root}")
        io.line(f"- Goal: {answers.goal}")
        freeze = io.ask_yes_no("Freeze this dossier and mark it ready for execution?", default=True)
        manifest.status = RunStatus.READY if freeze else RunStatus.DRAFT
        manager.save_manifest(manifest)
        return manifest, paths


def execute_run(
    *,
    repo_root: str | Path,
    run_id: str,
    dry_run: bool,
    cleanup: bool,
    reset_worktree: bool,
    worker_timeout_seconds: int | None,
    review_timeout_seconds: int | None,
) -> tuple[RunManifest, dict]:
    manager = RunManager(repo_root)
    manifest = manager.load_manifest(run_id)
    paths = manager.create_paths(run_id)

    project = load_model(paths.project_path, ProjectConfig)
    profile = load_model(paths.profile_path, RepositoryProfile)
    policy = load_model(paths.policy_path, ExecutionPolicy)
    task = load_model(paths.task_path, WorkerTask)

    manifest.status = RunStatus.EXECUTING
    manager.save_manifest(manifest)
    defer_cleanup = cleanup and not dry_run and (
        manifest.answers.auto_push_complete_pr or manifest.answers.auto_push_blocked_pr
    )
    report = TaskRunner(project=project, policy=policy, profile=profile, worker=CodexCLIWorker(policy)).run(
        task,
        repo_root=repo_root,
        dry_run=dry_run,
        push=False,
        cleanup=cleanup and not defer_cleanup,
        reset_worktree=reset_worktree,
        worker_timeout_seconds=worker_timeout_seconds,
        review_timeout_seconds=review_timeout_seconds,
    )
    payload = json.loads(dump_task_run_report(report))
    report_path = manager.save_report(run_id, "execute-latest", payload)
    manifest.latest_execute_report = str(report_path.relative_to(manager.repo_root))

    if report.success and not dry_run and manifest.answers.auto_push_complete_pr:
        delivery = create_complete_pr(repo_root=repo_root, run_id=run_id)
        manifest.latest_delivery_report = str(Path(delivery["report_path"]).relative_to(manager.repo_root))
        manifest.status = RunStatus.COMPLETE_PR
    elif not report.success and not dry_run and manifest.answers.auto_push_blocked_pr:
        delivery = create_blocked_pr(repo_root=repo_root, run_id=run_id)
        manifest.latest_delivery_report = str(Path(delivery["report_path"]).relative_to(manager.repo_root))
        manifest.status = RunStatus.BLOCKED_PR
    else:
        manifest.status = RunStatus.EXECUTED

    if defer_cleanup and manifest.status in {RunStatus.COMPLETE_PR, RunStatus.BLOCKED_PR, RunStatus.EXECUTED}:
        _cleanup_worktree(repo_root=repo_root, report=payload)
        payload["cleanup_performed"] = True

    manager.save_manifest(manifest)
    return manifest, payload


def create_complete_pr(*, repo_root: str | Path, run_id: str) -> dict:
    manager = RunManager(repo_root)
    manifest = manager.load_manifest(run_id)
    if not manifest.latest_execute_report:
        raise RuntimeError("No execution report found for this run.")
    report = manager.load_report(manifest.latest_execute_report)
    if not report.get("success"):
        raise RuntimeError("Latest execution report is not successful. Use blocked-pr instead.")
    payload = _push_run_merge_request(repo_root=repo_root, run_id=run_id, kind="complete", execution_report=report)
    report_path = manager.save_report(run_id, "complete-pr-latest", payload)
    manifest.latest_delivery_report = str(report_path.relative_to(manager.repo_root))
    manifest.status = RunStatus.COMPLETE_PR
    manager.save_manifest(manifest)
    payload["report_path"] = str(report_path)
    return payload


def create_blocked_pr(*, repo_root: str | Path, run_id: str) -> dict:
    manager = RunManager(repo_root)
    manifest = manager.load_manifest(run_id)
    if not manifest.latest_execute_report:
        raise RuntimeError("No execution report found for this run.")
    report = manager.load_report(manifest.latest_execute_report)
    payload = _push_run_merge_request(repo_root=repo_root, run_id=run_id, kind="blocked", execution_report=report)
    report_path = manager.save_report(run_id, "blocked-pr-latest", payload)
    manifest.latest_delivery_report = str(report_path.relative_to(manager.repo_root))
    manifest.status = RunStatus.BLOCKED_PR
    manager.save_manifest(manifest)
    payload["report_path"] = str(report_path)
    return payload


def _push_run_merge_request(*, repo_root: str | Path, run_id: str, kind: str, execution_report: dict) -> dict:
    manager = RunManager(repo_root)
    manifest = manager.load_manifest(run_id)
    paths = manager.create_paths(run_id)
    project = load_model(paths.project_path, ProjectConfig)
    task = load_model(paths.task_path, WorkerTask)
    delivery = GitLabDelivery(project)

    worktree_path = Path(execution_report["worktree_path"])
    if not worktree_path.exists():
        raise RuntimeError("Worktree path no longer exists. Re-run execute without cleanup before pushing a PR.")

    git_manager = GitWorktreeManager(repo_root, remote_name=project.remote_name)
    if kind == "blocked":
        blocker_path = worktree_path / ".damon" / "blocked" / f"{run_id}.md"
        blocker_path.parent.mkdir(parents=True, exist_ok=True)
        blocker_path.write_text(_render_blocker_note(manifest, execution_report), encoding="utf-8")

    commit_sha = execution_report.get("commit_sha")
    if git_manager.has_uncommitted_changes(worktree_path):
        commit_message = (
            f"{task.task_id}: finalize complete PR"
            if kind == "complete"
            else f"{task.task_id}: capture blocked progress"
        )
        commit_sha = git_manager.commit_all(worktree_path, message=commit_message)
    elif not commit_sha:
        commit_sha = git_manager.current_head(worktree_path)

    title = task.title if kind == "complete" else f"Blocked: {task.title}"
    description = (
        _render_complete_pr_description(manifest, execution_report)
        if kind == "complete"
        else _render_blocked_pr_description(manifest, execution_report)
    )
    spec = MergeRequestSpec(
        source_branch=execution_report["working_branch"],
        target_branch=task.repository.target_branch,
        title=title,
        description=description,
        draft=manifest.answers.draft_merge_request,
        labels=project.delivery.default_labels,
    )
    result = delivery.push_with_merge_request(spec, workdir=str(worktree_path))
    return {
        "kind": kind,
        "run_id": run_id,
        "commit_sha": commit_sha,
        "merge_request_spec": {
            "source_branch": spec.source_branch,
            "target_branch": spec.target_branch,
            "title": spec.effective_title,
            "description": spec.description,
            "labels": spec.labels or [],
        },
        "push_command": delivery.build_push_command(spec),
        "push_exit_code": result.returncode,
        "push_stdout": result.stdout,
        "push_stderr": result.stderr,
        "merge_request_url_hint": f"{project.gitlab.project_web_url}/-/merge_requests",
    }


def _cleanup_worktree(*, repo_root: str | Path, report: dict) -> None:
    worktree_path = report.get("worktree_path")
    if not worktree_path:
        return
    GitWorktreeManager(repo_root).remove(worktree_path, force=True, missing_ok=True)


def _build_project_config(scan: RepoScanSummary, answers: PlanningAnswers) -> ProjectConfig:
    remote_url = scan.remote_url or f"git@example.com:{scan.repo_name}.git"
    web_base_url, project_path = _derive_web_info(remote_url)
    return ProjectConfig(
        version="0.1",
        name=scan.repo_name,
        remote_name=scan.remote_name,
        remote_url=remote_url,
        default_target_branch=answers.target_branch,
        gitlab=GitLabProject(
            api_base_url=f"{web_base_url}/api/v4",
            web_base_url=web_base_url,
            project_path=project_path,
        ),
        delivery=DeliveryOptions(
            use_push_options=True,
            draft_merge_requests=answers.draft_merge_request,
            default_labels=["damon", _slug(scan.repo_name)],
        ),
    )


def _build_repository_profile(answers: PlanningAnswers) -> RepositoryProfile:
    return RepositoryProfile(
        version="0.1",
        worktree_root=".damon/worktrees",
        setup_commands=[],
        lint_commands=[CommandSpec(name=f"lint-{index + 1}", command=command) for index, command in enumerate(answers.lint_commands)],
        test_commands=[CommandSpec(name=f"test-{index + 1}", command=command) for index, command in enumerate(answers.test_commands)],
        static_analysis_commands=[
            CommandSpec(name=f"static-{index + 1}", command=command) for index, command in enumerate(answers.static_analysis_commands)
        ],
        run_review=answers.run_review,
        commit_changes=True,
        commit_message_template="{task_id}: {title}",
    )


def _build_execution_policy(answers: PlanningAnswers) -> ExecutionPolicy:
    return ExecutionPolicy(
        version="0.1",
        allow_silent_replans=True,
        planning=PlanningPolicy(),
        escalation=EscalationPolicy(
            blocker_categories=[
                "missing_credentials",
                "missing_required_runtime",
                "unresolved_requirement_conflict",
                "destructive_operation_outside_policy",
            ],
            retry_budget_per_stage=3,
            consecutive_failure_limit=3,
            max_unattended_cycles=12,
        ),
        delivery=DeliveryPolicy(
            git=GitPolicy(branch_prefix="damon/", commit_strategy=CommitStrategy.CHECKPOINT, push_after_green=True),
            gitlab=GitLabPolicy(open_merge_request=True, draft_by_default=answers.draft_merge_request, labels=["damon"]),
        ),
        verification=VerificationPolicy(
            require_unit_tests=bool(answers.test_commands),
            require_lint=bool(answers.lint_commands),
            require_static_analysis=bool(answers.static_analysis_commands),
            require_ci_green=False,
            reviewer_agent_required=answers.run_review,
        ),
        execution=ExecutionSettings(),
    )


def _build_task_contract(manifest: RunManifest) -> WorkerTask:
    answers = manifest.answers
    return WorkerTask(
        version="0.1",
        task_id=manifest.run_id,
        title=answers.title,
        objective=answers.goal,
        repository=RepositoryContext(
            path=manifest.repo_root,
            default_branch=answers.target_branch,
            base_ref=answers.base_ref,
            working_branch=answers.working_branch,
            target_branch=answers.target_branch,
        ),
        acceptance_criteria=answers.definition_of_done,
        constraints=PathConstraints(
            allowed_paths=answers.allowed_paths,
            forbidden_paths=answers.forbidden_paths,
            max_changed_files=50,
        ),
        inputs=TaskInputs(
            architecture_refs=[f".damon/runs/{manifest.run_id}/dossier/architecture.md"],
            policy_ref=f".damon/runs/{manifest.run_id}/dossier/execution_policy.yaml",
            related_issues=[],
        ),
        plan=TaskPlan(parent_goal=answers.goal),
        deliverables=TaskDeliverables(
            code_changes_required=True,
            tests_required=bool(answers.test_commands),
            docs_required=False,
            expected_outputs=["patch", "implementation_notes", "test_evidence", "risk_summary"],
        ),
        handoff=TaskHandoff(
            must_produce=["implementation_notes", "test_evidence", "risk_summary"],
            reviewer="reviewer",
            merge_request_template="default",
        ),
    )


def _render_goal_markdown(manifest: RunManifest) -> str:
    lines = [f"# {manifest.title}", "", "## Goal", "", manifest.goal, "", "## In Scope", ""]
    lines.extend(f"- {item}" for item in manifest.answers.scope_items or [manifest.goal])
    lines.extend(["", "## Out of Scope", ""])
    lines.extend(f"- {item}" for item in manifest.answers.non_goals or ["None specified."])
    return "\n".join(lines)


def _render_architecture_markdown(manifest: RunManifest) -> str:
    lines = [
        f"# Architecture Notes for {manifest.run_id}",
        "",
        "## Repo Context",
        "",
        f"- Repo root: {manifest.scan.repo_root}",
        f"- Stack: {', '.join(manifest.scan.detected_stack)}",
        f"- Base ref: {manifest.answers.base_ref or manifest.scan.default_branch}",
        "",
        "## Notes",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest.answers.architecture_notes or ["No extra notes provided."])
    return "\n".join(lines)


def _render_repo_scan_markdown(scan: RepoScanSummary) -> str:
    lines = [
        f"# Repository Scan: {scan.repo_name}",
        "",
        f"- Root: {scan.repo_root}",
        f"- Current branch: {scan.current_branch or 'unknown'}",
        f"- Default branch: {scan.default_branch}",
        f"- Remote: {scan.remote_name} -> {scan.remote_url or 'not configured'}",
        f"- Stack: {', '.join(scan.detected_stack)}",
        "",
        "## Top Level Entries",
        "",
    ]
    lines.extend(f"- {item}" for item in scan.top_level_entries)
    return "\n".join(lines)


def _render_complete_pr_description(manifest: RunManifest, execution_report: dict) -> str:
    lines = [
        f"Automated delivery for {manifest.run_id}.",
        "",
        f"Goal: {manifest.goal}",
        f"Working branch: {execution_report.get('working_branch')}",
        "",
        "Definition of done:",
    ]
    lines.extend(f"- {item}" for item in manifest.answers.definition_of_done)
    return "\n".join(lines)


def _render_blocked_pr_description(manifest: RunManifest, execution_report: dict) -> str:
    lines = [
        f"Blocked automated delivery for {manifest.run_id}.",
        "",
        f"Goal: {manifest.goal}",
        f"Working branch: {execution_report.get('working_branch')}",
        "",
        "Reasons this run is blocked:",
    ]
    worker_result = execution_report.get("worker_result")
    if worker_result:
        lines.append(f"- Worker exit code: {worker_result.get('exit_code')}")
    for result in execution_report.get("lint_results", []):
        if result.get("required") and result.get("exit_code") != 0:
            lines.append(f"- Lint failed: {result.get('name')}")
    for result in execution_report.get("test_results", []):
        if result.get("required") and result.get("exit_code") != 0:
            lines.append(f"- Test failed: {result.get('name')}")
    lines.extend(
        [
            "",
            "Definition of done:",
        ]
    )
    lines.extend(f"- {item}" for item in manifest.answers.definition_of_done)
    return "\n".join(lines)


def _render_blocker_note(manifest: RunManifest, execution_report: dict) -> str:
    lines = [
        f"# Blocked Run: {manifest.run_id}",
        "",
        f"Goal: {manifest.goal}",
        f"Title: {manifest.title}",
        "",
        "The automated run could not finish without escalation. This note is committed so the branch can be reviewed.",
        "",
        "Execution summary:",
        f"- Success: {execution_report.get('success')}",
        f"- Worktree: {execution_report.get('worktree_path')}",
        f"- Base ref: {execution_report.get('base_ref')}",
    ]
    return "\n".join(lines)


def _derive_web_info(remote_url: str) -> tuple[str, str]:
    normalized = remote_url.removesuffix(".git")
    if normalized.startswith("git@") and ":" in normalized:
        host = normalized.split("@", 1)[1].split(":", 1)[0]
        path = normalized.split(":", 1)[1]
        return f"http://{host}", path
    if normalized.startswith("http://") or normalized.startswith("https://"):
        parts = normalized.split("/", 3)
        web_base = "/".join(parts[:3])
        path = parts[3] if len(parts) > 3 else "example/project"
        return web_base, path
    return "http://example.com", "example/project"


def _title_from_goal(goal: str) -> str:
    goal = goal.strip()
    if not goal:
        return "Planned Task"
    return " ".join(goal.split()[:8]).strip().capitalize()


def _slug(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "-" for char in value)
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    return normalized.strip("-") or "task"
