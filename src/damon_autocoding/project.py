from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel, Field, model_validator


class GitLabProject(BaseModel):
    api_base_url: str
    web_base_url: str
    project_path: str
    token_env_var: str = "GITLAB_TOKEN"

    @property
    def encoded_project_path(self) -> str:
        return quote(self.project_path, safe="")

    @property
    def merge_requests_api(self) -> str:
        return f"{self.api_base_url.rstrip('/')}/projects/{self.encoded_project_path}/merge_requests"

    @property
    def project_web_url(self) -> str:
        return f"{self.web_base_url.rstrip('/')}/{self.project_path}"


class DeliveryOptions(BaseModel):
    use_push_options: bool = True
    draft_merge_requests: bool = False
    default_labels: list[str] = Field(default_factory=list)


class ProjectConfig(BaseModel):
    version: str
    name: str
    remote_name: str = "origin"
    remote_url: str
    default_target_branch: str = "main"
    gitlab: GitLabProject
    delivery: DeliveryOptions = Field(default_factory=DeliveryOptions)

    @model_validator(mode="after")
    def validate_paths(self) -> "ProjectConfig":
        normalized_remote = self.remote_url.removesuffix(".git")
        if self.gitlab.project_path not in normalized_remote:
            raise ValueError("remote_url must contain the configured gitlab.project_path")
        return self
