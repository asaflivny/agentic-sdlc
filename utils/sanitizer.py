"""Input sanitization utilities for LLM context.

Sanitizes text from untrusted sources (events, git, RAG) to prevent:
- Prompt injection via shell metacharacters
- Excessive context bloat from whitespace
- Secret exposure (API keys, tokens)
- Control character noise
"""

import re
from models.events import PushEvent


def sanitize_text(text: str | None) -> str:
    """Sanitize raw text before sending to LLM.

    Args:
        text: Raw text to sanitize

    Returns:
        Cleaned text safe for LLM consumption
    """
    if not text:
        return ""

    # Remove common secret patterns
    text = _remove_secrets(text)

    # Remove control characters except newline and tab
    text = "".join(char for char in text if ord(char) >= 32 or char in "\n\t")

    # Normalize excessive whitespace (collapse 3+ blank lines to 2)
    text = re.sub(r"\n\n\n+", "\n\n", text)

    # Escape shell metacharacters that could break prompts
    # Replace dangerous chars that might be in commit messages, file names, etc.
    text = text.replace("`", "'")  # backticks → single quotes
    text = text.replace("$(", "( ")  # $() → ( ) [command substitution]
    text = text.replace("|", "⎪")  # pipes → unicode equivalent

    # Truncate lines that are excessively long (common in minified code)
    lines = text.split("\n")
    truncated_lines = []
    for line in lines:
        if len(line) > 500:
            truncated_lines.append(line[:500] + " [...]")
        else:
            truncated_lines.append(line)
    text = "\n".join(truncated_lines)

    return text


def sanitize_diff(diff_text: str | None) -> str:
    """Sanitize git diff before sending to agents.

    Args:
        diff_text: Raw git diff output

    Returns:
        Cleaned diff safe for LLM analysis
    """
    if not diff_text:
        return ""

    # First pass: sanitize text content
    text = sanitize_text(diff_text)

    # Ensure diff headers are clean (normalize line endings)
    text = re.sub(r"(\+\+\+|---) [^\n]+\\n", r"\1 (file)\n", text)

    # Remove binary file indicators that could confuse LLM
    text = text.replace("Binary files", "Binary file")

    return text


def should_analyze_diff(diff_text: str, event: PushEvent) -> tuple[bool, str]:
    """Determine if a diff has meaningful changes worth analyzing.

    Early exit check: skip analysis for trivial changes.

    Args:
        diff_text: Git diff output
        event: Push event with commit metadata

    Returns:
        (should_analyze, reason_if_skip)
    """
    if not diff_text or not diff_text.strip():
        return False, "Diff is empty"

    # Check if only whitespace/blank lines changed
    lines = diff_text.split("\n")
    code_lines = [
        line for line in lines
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
    ]

    if not code_lines:
        return False, "No meaningful code changes (diff has no +/- lines)"

    # Check if changes are only comments
    comment_patterns = [
        r"^\+\s*#",  # Python comments
        r"^\+\s*//",  # C-style comments
        r"^\+\s*\*",  # Block comment continuation
        r"^\-\s*#",
        r"^\-\s*//",
        r"^\-\s*\*",
    ]

    significant_lines = [
        line for line in code_lines
        if not any(re.match(pattern, line) for pattern in comment_patterns)
        and line.strip() not in ["+", "-"]  # skip comment-only lines
    ]

    if not significant_lines:
        return False, "Only comments changed"

    # Check if changes are only imports
    import_patterns = [
        r"^\+\s*(from|import)\s",  # Python imports
        r"^\+\s*import\s",  # Java/Go imports
        r"^\-\s*(from|import)\s",
        r"^\-\s*import\s",
    ]

    non_import_lines = [
        line for line in significant_lines
        if not any(re.match(pattern, line) for pattern in import_patterns)
    ]

    if not non_import_lines:
        return False, "Only imports changed"

    # Check minimum meaningful change (at least 3 lines of code)
    if len(non_import_lines) < 3:
        return False, f"Diff too small ({len(non_import_lines)} lines of code)"

    return True, ""


def _remove_secrets(text: str) -> str:
    """Remove common API keys and secret patterns from text.

    Args:
        text: Text potentially containing secrets

    Returns:
        Text with secrets replaced with [REDACTED]
    """
    patterns = [
        # AWS keys: AKIA... + long hex
        r"AKIA[0-9A-Z]{16}",
        # GitHub tokens: ghp_, ghu_, ghs_, ghr_
        r"gh[pousr]_[a-zA-Z0-9_]{36,255}",
        # Generic API key patterns
        r"api[_-]?key['\"]?\s*[:=]\s*['\"]?[a-zA-Z0-9_\-]{20,}['\"]?",
        # Private key headers
        r"-----BEGIN (RSA|DSA|EC|OPENSSH|PGP) PRIVATE KEY",
        # AWS credentials
        r"aws_access_key_id\s*=\s*[^\s]+",
        r"aws_secret_access_key\s*=\s*[^\s]+",
    ]

    result = text
    for pattern in patterns:
        result = re.sub(pattern, "[REDACTED]", result, flags=re.IGNORECASE)

    return result
