from typing import Optional
from pydantic import BaseModel


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
    name: str
    clone_url: str


class Pusher(BaseModel):
    name: str
    email: str


class PushEvent(BaseModel):
    ref: str
    before: str
    after: str
    repository: Repository
    pusher: Pusher
    commits: list[Commit]
    head_commit: Optional[Commit] = None

    @property
    def branch(self) -> str:
        return self.ref.removeprefix("refs/heads/")
