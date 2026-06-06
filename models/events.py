from typing import Optional, Any
from pydantic import BaseModel, field_validator, ConfigDict


class Author(BaseModel):
    name: str
    email: str


class Commit(BaseModel):
    id: str
    timestamp: str
    message: str
    author: Author
    added: list[str]
    removed: list[str]
    modified: list[str]


class Repository(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str
    clone_url: str
    owner: str = ""

    @field_validator('owner', mode='before')
    @classmethod
    def extract_owner(cls, v):
        if isinstance(v, dict):
            return v.get('login') or v.get('username') or v.get('name') or ""
        return str(v) if v else ""


class Pusher(BaseModel):
    model_config = ConfigDict(extra='allow')
    name: str = ""
    email: str = ""

    @field_validator('name', mode='before')
    @classmethod
    def extract_name(cls, v):
        return str(v) if v else ""

    @field_validator('email', mode='before')
    @classmethod
    def extract_email(cls, v):
        return str(v) if v else ""


class PushEvent(BaseModel):
    model_config = ConfigDict(extra='allow')
    ref: str
    before: str
    after: str
    repository: Any  # Accept any format
    pusher: Any  # Accept any format
    commits: list[Commit]
    head_commit: Optional[Commit] = None

    @field_validator('repository', mode='before')
    @classmethod
    def normalize_repository(cls, v):
        if isinstance(v, dict):
            return Repository(
                name=v.get('name', ''),
                clone_url=v.get('clone_url') or v.get('html_url') or '',
                owner=v.get('owner', {}).get('login') if isinstance(v.get('owner'), dict) else (v.get('owner') or '')
            )
        return v

    @field_validator('pusher', mode='before')
    @classmethod
    def normalize_pusher(cls, v):
        if isinstance(v, dict):
            return Pusher(
                name=v.get('full_name') or v.get('name') or v.get('login') or '',
                email=v.get('email') or ''
            )
        return v

    @property
    def branch(self) -> str:
        ref = self.ref if isinstance(self.ref, str) else ""
        return ref.removeprefix("refs/heads/")
