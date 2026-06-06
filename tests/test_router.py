"""Tests for WorkflowRouter routing rules."""

from models.events import Author, Commit, PushEvent, Pusher, Repository
from workflows.router import BranchPatternRule, DefaultRule, FilePatternRule, WorkflowRouter
from workflows.definitions.full_review import FULL_REVIEW
from workflows.definitions.security_focus import SECURITY_FOCUS
from workflows.definitions.quick_review import QUICK_REVIEW


def _make_event(branch: str = "feature/x", files: list[str] | None = None) -> PushEvent:
    modified = files or []
    commit = Commit(
        id="a" * 40,
        timestamp="2026-01-01T00:00:00Z",
        message="test",
        author=Author(name="dev", email="dev@test.com"),
        added=[],
        removed=[],
        modified=modified,
    )
    return PushEvent(
        ref=f"refs/heads/{branch}",
        before="0" * 40,
        after="b" * 40,
        repository=Repository(name="test-repo", clone_url="/tmp/test"),
        pusher=Pusher(name="dev", email="dev@test.com"),
        commits=[commit] if modified else [],
    )


# ---------------------------------------------------------------------------
# BranchPatternRule
# ---------------------------------------------------------------------------


def test_branch_rule_matches_main():
    rule = BranchPatternRule(10, "main_branch", r"^(main|master|release/.+)$", FULL_REVIEW)
    assert rule.matches(_make_event("main"))
    assert rule.matches(_make_event("master"))
    assert rule.matches(_make_event("release/1.2"))


def test_branch_rule_does_not_match_feature():
    rule = BranchPatternRule(10, "main_branch", r"^(main|master|release/.+)$", FULL_REVIEW)
    assert not rule.matches(_make_event("feature/cool-thing"))
    assert not rule.matches(_make_event("dev"))


def test_branch_rule_returns_correct_workflow():
    rule = BranchPatternRule(10, "main_branch", r"^main$", FULL_REVIEW)
    assert rule.workflow is FULL_REVIEW


# ---------------------------------------------------------------------------
# FilePatternRule
# ---------------------------------------------------------------------------


def test_file_rule_matches_sensitive_file():
    rule = FilePatternRule(20, "secrets", r"\.(env|pem|key)$", SECURITY_FOCUS)
    event = _make_event(files=["config/prod.env", "src/main.py"])
    assert rule.matches(event)


def test_file_rule_matches_auth_path():
    rule = FilePatternRule(20, "auth", r"(secret|auth|oauth)", SECURITY_FOCUS)
    event = _make_event(files=["src/auth/login.py"])
    assert rule.matches(event)


def test_file_rule_no_match_on_plain_files():
    rule = FilePatternRule(20, "secrets", r"\.(env|pem|key)$", SECURITY_FOCUS)
    event = _make_event(files=["src/main.py", "tests/test_api.py"])
    assert not rule.matches(event)


def test_file_rule_no_commits():
    rule = FilePatternRule(20, "secrets", r"\.(env|pem|key)$", SECURITY_FOCUS)
    event = _make_event()  # no commits → no files
    assert not rule.matches(event)


# ---------------------------------------------------------------------------
# DefaultRule
# ---------------------------------------------------------------------------


def test_default_rule_always_matches():
    rule = DefaultRule()
    assert rule.matches(_make_event("anything"))
    assert rule.workflow is QUICK_REVIEW


# ---------------------------------------------------------------------------
# WorkflowRouter — integration
# ---------------------------------------------------------------------------


def test_router_main_branch_routes_full_review():
    router = WorkflowRouter()
    assert router.route(_make_event("main")).name == FULL_REVIEW.name


def test_router_sensitive_file_routes_security_focus():
    router = WorkflowRouter()
    event = _make_event("feature/x", files=["secrets.env"])
    assert router.route(event).name == SECURITY_FOCUS.name


def test_router_feature_branch_plain_files_routes_quick_review():
    router = WorkflowRouter()
    event = _make_event("feature/add-button", files=["src/button.py"])
    assert router.route(event).name == QUICK_REVIEW.name


def test_router_priority_branch_wins_over_file():
    """A push to main that also touches a .env file should route to full_review (lower priority number wins)."""
    router = WorkflowRouter()
    event = _make_event("main", files=["secrets.env"])
    assert router.route(event).name == FULL_REVIEW.name
