from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BranchWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    display_name: str = Field(alias="displayName")


class GitHubRepoWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    owner: str
    repo: str
    is_private: bool | None = Field(default=None, alias="isPrivate")
    default_branch: BranchWire | None = Field(default=None, alias="defaultBranch")
    branches: list[BranchWire] = Field(default_factory=list)


class SourceWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    id: str | None = None
    github_repo: GitHubRepoWire | None = Field(default=None, alias="githubRepo")


class GitHubRepoContextWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    starting_branch: str | None = Field(default=None, alias="startingBranch")


class SourceContextWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str | None = None
    github_repo_context: GitHubRepoContextWire | None = Field(default=None, alias="githubRepoContext")
    working_branch: str | None = Field(default=None, alias="workingBranch")
    environment_variables_enabled: bool | None = Field(default=None, alias="environmentVariablesEnabled")


class PullRequestWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    url: str
    title: str | None = None
    description: str | None = None
    base_ref: str | None = Field(default=None, alias="baseRef")
    head_ref: str | None = Field(default=None, alias="headRef")


class GitPatchWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    base_commit_id: str | None = Field(default=None, alias="baseCommitId")
    unidiff_patch: str | None = Field(default=None, alias="unidiffPatch")
    suggested_commit_message: str | None = Field(default=None, alias="suggestedCommitMessage")


class ChangeSetWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    source: str | None = None
    git_patch: GitPatchWire | None = Field(default=None, alias="gitPatch")


class SessionOutputWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    pull_request: PullRequestWire | None = Field(default=None, alias="pullRequest")
    change_set: ChangeSetWire | None = Field(default=None, alias="changeSet")


class SessionWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    id: str
    prompt: str | None = None
    title: str | None = None
    state: str | None = None
    url: str | None = None
    source_context: SourceContextWire | None = Field(default=None, alias="sourceContext")
    outputs: list[SessionOutputWire] = Field(default_factory=list)
    archived: bool | None = None
    require_plan_approval: bool | None = Field(default=None, alias="requirePlanApproval")
    automation_mode: str | None = Field(default=None, alias="automationMode")
    create_time: str | None = Field(default=None, alias="createTime")
    update_time: str | None = Field(default=None, alias="updateTime")


class ArtifactWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    change_set: ChangeSetWire | None = Field(default=None, alias="changeSet")
    bash_output: dict[str, Any] | None = Field(default=None, alias="bashOutput")
    media: dict[str, Any] | None = None


class ActivityWire(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    id: str | None = None
    create_time: str | None = Field(default=None, alias="createTime")
    originator: str | None = None
    description: str | None = None
    status: str | None = None
    plan_generated: dict[str, Any] | None = Field(default=None, alias="planGenerated")
    plan_approved: dict[str, Any] | None = Field(default=None, alias="planApproved")
    user_messaged: dict[str, Any] | None = Field(default=None, alias="userMessaged")
    agent_messaged: dict[str, Any] | None = Field(default=None, alias="agentMessaged")
    progress_updated: dict[str, Any] | None = Field(default=None, alias="progressUpdated")
    session_completed: dict[str, Any] | None = Field(default=None, alias="sessionCompleted")
    session_failed: dict[str, Any] | None = Field(default=None, alias="sessionFailed")
    artifacts: list[ArtifactWire] = Field(default_factory=list)

    def event_type(self) -> str:
        known = [
            ("plan_generated", self.plan_generated),
            ("plan_approved", self.plan_approved),
            ("user_messaged", self.user_messaged),
            ("agent_messaged", self.agent_messaged),
            ("progress_updated", self.progress_updated),
            ("session_completed", self.session_completed),
            ("session_failed", self.session_failed),
        ]
        for name, value in known:
            if value is not None:
                return name
        return "unknown"


class DispatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_id: Literal["julesctl.dispatch.v1"] = Field(default="julesctl.dispatch.v1", alias="schema")
    dispatch_key: str
    work_definition: str | None = None
    occurrence: str | None = None
    repo: str | None = None
    starting_branch: str | None = None
    title: str
    prompt: str
    auto_create_pr: bool = True
    require_plan_approval: bool = False
    priority: int = 50
    caller: str | None = None
    collision_keys: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    expires_at: str | None = None

    @field_validator("repo")
    @classmethod
    def validate_repo(cls, value: str | None) -> str | None:
        if value is not None and (value.count("/") != 1 or value.startswith("/") or value.endswith("/")):
            raise ValueError("repo must be owner/repo")
        return value
