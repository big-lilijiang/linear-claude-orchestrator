from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass

from .project import ProjectConfig


@dataclass(slots=True)
class MergeRequestSpec:
    source_branch: str
    target_branch: str
    title: str
    description: str
    draft: bool = True
    labels: list[str] | None = None

    @property
    def effective_title(self) -> str:
        if self.draft and not self.title.startswith("Draft:"):
            return f"Draft: {self.title}"
        return self.title


class GitLabDelivery:
    def __init__(self, project: ProjectConfig) -> None:
        self.project = project

    def build_push_command(self, spec: MergeRequestSpec) -> list[str]:
        command = [
            "git",
            "push",
            "-u",
            self.project.remote_name,
            spec.source_branch,
        ]
        if self.project.delivery.use_push_options:
            command.extend(
                [
                    "-o",
                    "merge_request.create",
                    "-o",
                    f"merge_request.target={spec.target_branch}",
                    "-o",
                    f"merge_request.title={spec.effective_title}",
                    "-o",
                    f"merge_request.description={spec.description}",
                ]
            )
            if spec.labels:
                command.extend(["-o", f"merge_request.label={','.join(spec.labels)}"])
        return command

    def build_api_payload(self, spec: MergeRequestSpec) -> dict:
        payload = {
            "source_branch": spec.source_branch,
            "target_branch": spec.target_branch,
            "title": spec.effective_title,
            "description": spec.description,
            "remove_source_branch": False,
        }
        if spec.labels:
            payload["labels"] = ",".join(spec.labels)
        return payload

    def create_merge_request_via_api(self, spec: MergeRequestSpec) -> dict:
        token = os.getenv(self.project.gitlab.token_env_var)
        if not token:
            raise RuntimeError(
                f"Environment variable {self.project.gitlab.token_env_var} is required for API delivery."
            )
        payload = json.dumps(self.build_api_payload(spec)).encode("utf-8")
        request = urllib.request.Request(
            self.project.gitlab.merge_requests_api,
            data=payload,
            headers={
                "PRIVATE-TOKEN": token,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def push_with_merge_request(self, spec: MergeRequestSpec, *, workdir: str) -> subprocess.CompletedProcess[str]:
        command = self.build_push_command(spec)
        return subprocess.run(command, cwd=workdir, text=True, capture_output=True, check=False)
