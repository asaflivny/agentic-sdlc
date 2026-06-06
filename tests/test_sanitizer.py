"""Tests for input sanitization utilities."""

import pytest
from models.events import PushEvent, Repository, Pusher, Commit
from utils.sanitizer import sanitize_text, sanitize_diff, should_analyze_diff


class TestSanitizeText:
    """Tests for sanitize_text function."""

    def test_empty_string(self):
        """Empty/None inputs return empty string."""
        assert sanitize_text("") == ""
        assert sanitize_text(None) == ""

    def test_removes_control_characters(self):
        """Control characters (0-31) are removed except newline/tab."""
        text = "hello\x00world\x01test\nfoo\tbar"
        result = sanitize_text(text)
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\n" in result
        assert "\t" in result

    def test_removes_secrets_github_token(self):
        """GitHub tokens are redacted."""
        text = "My token is ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        result = sanitize_text(text)
        assert "[REDACTED]" in result
        assert "ghp_" not in result

    def test_removes_secrets_aws_key(self):
        """AWS keys are redacted."""
        text = "AKIA1234567890ABCDEF secret"
        result = sanitize_text(text)
        assert "[REDACTED]" in result
        assert "AKIA" not in result

    def test_removes_secrets_private_key_header(self):
        """Private key headers are redacted."""
        text = "-----BEGIN RSA PRIVATE KEY-----\ndata"
        result = sanitize_text(text)
        assert "[REDACTED]" in result

    def test_escapes_shell_metacharacters(self):
        """Shell metacharacters are escaped."""
        text = "Use `command` and $(subshell) and pipe |"
        result = sanitize_text(text)
        assert "`" not in result
        assert "$(" not in result
        assert "|" not in result
        assert "(" in result  # At least some transformation

    def test_collapses_excessive_blank_lines(self):
        """3+ blank lines are collapsed to 2."""
        text = "line1\n\n\n\n\nline2"
        result = sanitize_text(text)
        assert "\n\n\n" not in result
        assert "line1\n\nline2" in result

    def test_truncates_long_lines(self):
        """Lines >500 chars are truncated."""
        long_line = "x" * 600
        result = sanitize_text(long_line)
        assert len(result) < 600
        assert "[...]" in result

    def test_preserves_normal_text(self):
        """Normal text is preserved."""
        text = "This is normal code\nwith multiple\nlines"
        result = sanitize_text(text)
        assert "normal code" in result
        assert "multiple" in result


class TestSanitizeDiff:
    """Tests for sanitize_diff function."""

    def test_empty_diff(self):
        """Empty diffs return empty string."""
        assert sanitize_diff("") == ""
        assert sanitize_diff(None) == ""

    def test_sanitizes_text_content(self):
        """Diffs are sanitized like text."""
        diff = "+token = ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        result = sanitize_diff(diff)
        assert "[REDACTED]" in result

    def test_handles_diff_headers(self):
        """Diff headers are preserved or cleaned."""
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,4 @@\n+new line"
        result = sanitize_diff(diff)
        assert "---" in result
        assert "+++" in result
        assert "new line" in result

    def test_removes_binary_file_indicators(self):
        """Binary file indicators are normalized."""
        diff = "Binary files a/image.png and b/image.png differ"
        result = sanitize_diff(diff)
        assert "Binary file" in result or "differ" in result


class TestShouldAnalyzeDiff:
    """Tests for should_analyze_diff early exit logic."""

    @pytest.fixture
    def sample_event(self):
        """Create a minimal PushEvent for testing."""
        return PushEvent(
            repository=Repository(
                name="test-repo",
                clone_url="https://github.com/test/repo.git",
                owner="test",
            ),
            ref="refs/heads/main",
            before="abc123",
            after="def456",
            commits=[],
            pusher=Pusher(name="tester", email="test@example.com"),
        )

    def test_empty_diff_early_exit(self, sample_event):
        """Empty diff triggers early exit."""
        should_analyze, reason = should_analyze_diff("", sample_event)
        assert not should_analyze
        assert "empty" in reason.lower()

    def test_whitespace_only_early_exit(self, sample_event):
        """Whitespace-only diff triggers early exit."""
        diff = "--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,4 @@\n+   \n+\n"
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert not should_analyze

    def test_comment_only_early_exit(self, sample_event):
        """Comment-only changes trigger early exit."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
 def foo():
-    # Old comment
+    # New comment
"""
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert not should_analyze
        assert "comment" in reason.lower()

    def test_import_only_early_exit(self, sample_event):
        """Import-only changes trigger early exit."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
-import os
+import os
+import sys
"""
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert not should_analyze
        assert "import" in reason.lower()

    def test_too_small_diff_early_exit(self, sample_event):
        """Very small diffs (< 3 lines) trigger early exit."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,4 @@
+x = 1
"""
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert not should_analyze
        assert "too small" in reason.lower()

    def test_meaningful_changes_proceed(self, sample_event):
        """Meaningful code changes proceed to analysis."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,5 +1,6 @@
 def old_function():
-    return None
+def new_function():
+    x = 42
+    y = 100
+    return x + y
"""
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert should_analyze
        assert reason == ""

    def test_mixed_changes_with_meaningful_code(self, sample_event):
        """Mixed comments + code proceeds if code is meaningful."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,5 +1,6 @@
-# Old comment
+# New comment
-x = 1
+x = 1
+y = 2
+z = x + y
"""
        should_analyze, reason = should_analyze_diff(diff, sample_event)
        assert should_analyze
