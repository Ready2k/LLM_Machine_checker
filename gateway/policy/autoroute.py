"""
Auto-routing policy for model: "auto" requests.

Inspects the prompt text and returns the most appropriate route name
without requiring the client to specify a model. No LLM required —
purely rule-based scoring on prompt signals.

Route priority order:
  1. private-docs  — triggered by privacy/sensitivity signals
  2. local-quality — triggered by complexity/depth signals
  3. local-fast    — default for short/simple/code-gen tasks
"""

import re
from typing import List, Dict

# ---------------------------------------------------------------------------
# Signal definitions
# ---------------------------------------------------------------------------

_PRIVACY_SIGNALS = [
    r"\bconfidential\b", r"\bprivate\b", r"\bsalary\b", r"\bpassword\b",
    r"\bpersonal\b", r"\binternal\b", r"\bsecret\b", r"\bpii\b",
    r"\bsensitive\b", r"\bnda\b", r"\bproprietary\b", r"\brestricted\b",
    r"\bcredential", r"\bsocial security\b", r"\bdate of birth\b",
]

_QUALITY_SIGNALS = [
    r"\bexplain\b", r"\barchitecture\b", r"\breview\b", r"\banalyse\b",
    r"\banalyze\b", r"\bcompare\b", r"\bdesign\b", r"\bwhy\b",
    r"\bhow does\b", r"\bhow would\b", r"\bhow should\b", r"\bhow do i\b",
    r"\brefactor\b", r"\bsecurity\b", r"\baudit\b", r"\btest\b",
    r"\bstrategy\b", r"\btradeoff\b", r"\btrade-off\b", r"\bdiagram\b",
    r"\bdocument\b", r"\bsummarise\b", r"\bsummarize\b", r"\bplan\b",
    r"\bimplications\b", r"\bcritique\b", r"\bevaluate\b", r"\bimplement\b",
    r"\bdeploy\b", r"\bsetup\b", r"\bconfigure\b",
]

_FAST_SIGNALS = [
    r"\bwrite a function\b", r"\bsnippet\b", r"\bfix\b", r"\bdebug\b",
    r"\bconvert\b", r"\btranslate\b", r"\bcomplete\b", r"\bautocomplet",
    r"\bgenerate\b", r"\bformat\b", r"\brename\b", r"\blist\b",
    r"\bwhat is\b", r"\bdefine\b",
]

# Thresholds
_PRIVACY_THRESHOLD = 1      # any privacy signal → private-docs
_QUALITY_THRESHOLD = 2      # 2+ quality signals → local-quality
_LONG_PROMPT_CHARS = 400    # long prompts default to quality
_VERY_SHORT_CHARS = 120     # very short prompts default to fast


def _extract_text(messages: List[Dict]) -> str:
    """Concatenate all user message content for analysis."""
    parts = []
    for msg in messages:
        if msg.get("role") in ("user", "system"):
            content = msg.get("content", "")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
    return " ".join(parts).lower()


def _count_signals(text: str, patterns: List[str]) -> int:
    return sum(1 for p in patterns if re.search(p, text))


def select_auto_route(messages: List[Dict], privacy_level: str = "personal") -> tuple[str, str]:
    """
    Analyse messages and return (route_name, reason).

    Args:
        messages: The chat messages from the request.
        privacy_level: Already-extracted privacy level from headers/metadata.

    Returns:
        Tuple of (route_name, reason_string) for logging.
    """
    # Privacy level from header always overrides content scanning
    if privacy_level in ("confidential", "restricted"):
        return "private-docs", f"privacy_header:{privacy_level}"

    text = _extract_text(messages)
    char_count = len(text)

    privacy_hits = _count_signals(text, _PRIVACY_SIGNALS)
    quality_hits = _count_signals(text, _QUALITY_SIGNALS)
    fast_hits = _count_signals(text, _FAST_SIGNALS)

    # Rule 1: any privacy signal in content → private-docs
    if privacy_hits >= _PRIVACY_THRESHOLD:
        return "private-docs", f"privacy_signals:{privacy_hits}"

    # Rule 2: long prompt with quality signals → local-quality
    if char_count >= _LONG_PROMPT_CHARS and quality_hits >= 1:
        return "local-quality", f"long_prompt:{char_count}chars+quality_signals:{quality_hits}"

    # Rule 3: multiple quality signals → local-quality
    if quality_hits >= _QUALITY_THRESHOLD:
        return "local-quality", f"quality_signals:{quality_hits}"

    # Rule 4: long prompt with no strong signals → lean quality
    if char_count >= _LONG_PROMPT_CHARS:
        return "local-quality", f"long_prompt:{char_count}chars"

    # Rule 5: fast signals or short prompt → local-fast
    if fast_hits >= 1 or char_count <= _VERY_SHORT_CHARS:
        return "local-fast", f"fast_signals:{fast_hits},chars:{char_count}"

    # Default: local-fast
    return "local-fast", f"default,chars:{char_count}"
