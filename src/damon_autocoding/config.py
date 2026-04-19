from __future__ import annotations

from pathlib import Path
from typing import TypeVar

import yaml
from pydantic import BaseModel

from .models import ExecutionPolicy, TaskRuntimeState, WorkerTask
from .project import ProjectConfig
from .repo_profile import RepositoryProfile

ModelT = TypeVar("ModelT", bound=BaseModel)


def load_yaml(path: str | Path) -> dict:
    content = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    if data is None:
        raise ValueError(f"{path} is empty")
    if not isinstance(data, dict):
        raise ValueError(f"{path} must define a mapping at the top level")
    return data


def load_model(path: str | Path, model_type: type[ModelT]) -> ModelT:
    return model_type.model_validate(load_yaml(path))


def load_policy(path: str | Path) -> ExecutionPolicy:
    return load_model(path, ExecutionPolicy)


def load_task(path: str | Path) -> WorkerTask:
    return load_model(path, WorkerTask)


def load_runtime_state(path: str | Path) -> TaskRuntimeState:
    return load_model(path, TaskRuntimeState)


def load_project(path: str | Path) -> ProjectConfig:
    return load_model(path, ProjectConfig)


def load_repository_profile(path: str | Path) -> RepositoryProfile:
    return load_model(path, RepositoryProfile)


def dump_yaml(path: str | Path, data: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def save_model(path: str | Path, model: BaseModel) -> None:
    dump_yaml(path, model.model_dump(mode="json"))
