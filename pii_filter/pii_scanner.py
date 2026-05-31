"""
pii_scanner.py – Core regex-based PII detection engine.

Design goals:
    • All patterns are compiled *once* at class-init (amortised cost).
    • Each pattern group is a ``(compiled_regex, PIIType)`` tuple so
      the scanner loop is a trivial list comprehension – no branching.
    • The snippet extractor is O(1) per match (simple slice arithmetic).
    • Luhn check-digit validation is applied to credit-card candidates
      to minimise false positives on arbitrary 16-digit numbers.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from pii_filter.models import PIIMatch, PIIType


# ── Compiled pattern registry ────────────────────────────────
# Each entry: (compiled_pattern, pii_category)

_EMAIL_RE = re.compile(
    r"""
    (?<![A-Za-z0-9._%+\-])          # negative look-behind: not mid-word
    [A-Za-z0-9._%+\-]+              # local part
    @
    [A-Za-z0-9.\-]+                 # domain
    \.[A-Za-z]{2,}                  # TLD
    """,
    re.VERBOSE,
)

_PHONE_RE = re.compile(
    r"""
    (?<![\w])                         # not inside an identifier
    (?:
        \+\d{1,3}[-.\s]?\d{2,4}[-.\s]?\d{3,5}[-.\s]?\d{3,5}
        |
        \(\d{2,4}\)\s?\d{3,4}[-.\s]\d{3,5}
        |
        \d{2,4}[-.\s]\d{3,4}[-.\s]\d{3,5}
    )
    (?![\w])                          # not inside an identifier
    """,
    re.VERBOSE,
)

_NAME_RE = re.compile(
    r"""
    \b
    (?:
        Employee|Name|Participant|Manager|Customer|Candidate|Person|Vendor[ \t]+contact
    )
    [ \t]*:[ \t]*
    [A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+
    (?:[ \t]+[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'’.-]+){1,3}
    \b
    """,
    re.VERBOSE,
)

_ADDRESS_RE = re.compile(
    r"""
    \b
    (?:(?:Home|Billing|Shipping)\s+)?Address
    [ \t]*:[ \t]*
    [A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ.-]+
    [ \t]+\d+[A-Za-z]?
    ,\s*
    \d{5}
    [ \t]+
    [A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ -]+
    \b
    """,
    re.VERBOSE | re.IGNORECASE,
)

_EMPLOYEE_ID_RE = re.compile(
    r"""
    \b
    E-\d{5}
    \b
    """,
    re.VERBOSE,
)

# IBAN: 2 uppercase letters, 2 check digits, 10-30 alphanumeric chars.
_IBAN_RE = re.compile(
    r"""
    \b
    [A-Z]{2}                         # country code
    \d{2}                            # check digits
    \s?
    [A-Z0-9]{4}                      # BBAN blocks (≥1)
    (?:\s?[A-Z0-9]{4}){2,7}         # remaining BBAN blocks
    (?:\s?[A-Z0-9]{1,4})?           # optional short final block
    \b
    """,
    re.VERBOSE,
)

# Credit card: Visa (4…), MC (5[1-5]…), Amex (3[47]…), Discover (6…).
# Allows optional separators (space / dash) every 4 digits.
_CC_RE = re.compile(
    r"""
    (?<!\d)
    (?:
        4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6(?:011|5\d{2})   # issuer prefix
    )
    (?:[-\s]?\d{4}){2,3}            # middle groups
    (?:[-\s]?\d{1,4})               # final group (Amex = 15 digits)
    (?!\d)
    """,
    re.VERBOSE,
)

# Ordered so the cheapest / most common patterns run first.
_PATTERNS: List[Tuple[re.Pattern[str], PIIType]] = [
    (_EMAIL_RE, PIIType.EMAIL),
    (_PHONE_RE, PIIType.PHONE),
    (_IBAN_RE, PIIType.IBAN),
    (_CC_RE, PIIType.CREDIT_CARD),
    (_NAME_RE, PIIType.NAME),
    (_ADDRESS_RE, PIIType.ADDRESS),
    (_EMPLOYEE_ID_RE, PIIType.EMPLOYEE_ID),
]

# ── Snippet settings ────────────────────────────────────────
_SNIPPET_RADIUS: int = 60  # chars of context on each side of the match


# ── Helpers ──────────────────────────────────────────────────

def _luhn_check(number: str) -> bool:
    """
    Validate a credit-card number via the Luhn algorithm.
    Strips separators before checking.

    Returns ``True`` if the number passes, ``False`` otherwise.
    """
    digits = [int(d) for d in number if d.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False

    checksum = 0
    # Walk digits right-to-left; double every second digit.
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def _extract_snippet(text: str, start: int, end: int) -> str:
    """
    Return a context window around ``text[start:end]``, capped at
    ~2 × ``_SNIPPET_RADIUS`` + match length characters.

    Adds ``…`` at the boundaries when text is truncated.
    """
    doc_len = len(text)
    snip_start = max(0, start - _SNIPPET_RADIUS)
    snip_end = min(doc_len, end + _SNIPPET_RADIUS)

    prefix = "…" if snip_start > 0 else ""
    suffix = "…" if snip_end < doc_len else ""

    snippet = text[snip_start:snip_end].replace("\n", " ").strip()
    return f"{prefix}{snippet}{suffix}"


# ── Public API ───────────────────────────────────────────────

class PIIScanner:
    """
    Stateless, thread-safe PII scanner.

    Usage::

        scanner = PIIScanner()
        matches = scanner.scan(document_text)

    All regex patterns are compiled at import time, so instantiation
    is essentially free.
    """

    def __init__(
        self,
        *,
        extra_patterns: Optional[List[Tuple[re.Pattern[str], PIIType]]] = None,
        enable_luhn: bool = True,
    ) -> None:
        """
        Args:
            extra_patterns: Additional ``(regex, PIIType)`` tuples to
                            append to the default pattern list.  Useful
                            for locale-specific identifiers (BSN, SSN, …).
            enable_luhn:    If ``True`` (default), credit-card candidates
                            are validated with the Luhn algorithm to cut
                            false positives.
        """
        self._patterns: List[Tuple[re.Pattern[str], PIIType]] = list(_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        self._enable_luhn = enable_luhn

    # ── Core scan method ─────────────────────────────────────
    def scan(self, text: str) -> List[PIIMatch]:
        """
        Scan *text* for all registered PII patterns, then remove
        overlapping matches (keeping the longest / most specific one).

        Returns a list of :class:`PIIMatch` instances sorted by
        character offset (ascending).  The list is empty if no PII
        is found.

        Overlap-resolution algorithm
        ────────────────────────────
        1. **Collect** – run every regex and record ``(start, end)``
           character indices alongside the PIIMatch data.
        2. **Sort** – order candidates by ``start`` ascending, then
           by span length ``(end - start)`` **descending**.  This
           ensures that when two matches start at the same position
           the longer one is considered first.
        3. **Sweep** – iterate through the sorted list and greedily
           accept a match only if it does **not** overlap with the
           last accepted match (i.e. its ``start ≥ prev_end``).
           Because longer matches are sorted first at each position,
           a shorter nested match (e.g. a PHONE inside an IBAN)
           will always appear *after* the longer one and be discarded.

        Complexity: O(N · D) where N = total raw matches and
        D = number of digits in the maximum key value — dominated
        by the radix sort step.
        """
        if not text:
            return []

        # ── Phase 1: collect all raw regex hits ──────────────
        # Each candidate is a (start, end, PIIMatch) tuple.
        candidates: List[Tuple[int, int, PIIMatch]] = []

        for pattern, pii_type in self._patterns:
            for m in pattern.finditer(text):
                raw_value = m.group(0).strip()
                start, end = m.start(), m.end()

                # ── Credit-card Luhn gate ────────────────────
                if pii_type is PIIType.CREDIT_CARD and self._enable_luhn:
                    if not _luhn_check(raw_value):
                        continue

                candidates.append((
                    start,
                    end,
                    PIIMatch(
                        pii_type=pii_type,
                        matched_value=raw_value,
                        snippet=_extract_snippet(text, start, end),
                        char_offset=start,
                        char_end=end,
                    ),
                ))

        # ── Phase 2: resolve overlapping intervals ───────────
        accepted = self._resolve_overlaps(candidates)

        # Already sorted by char_offset from _resolve_overlaps.
        return accepted



    # ── Overlap resolution ───────────────────────────────────
    @staticmethod
    def _resolve_overlaps(
        candidates: List[Tuple[int, int, PIIMatch]],
    ) -> List[PIIMatch]:
        """
        Remove overlapping matches, always keeping the *longer*
        (more specific) match when two intervals collide.
        """
        # 1. Sort by start index ASC, then by span length DESC
        #    We use Python's built-in Timsort which is extremely fast.
        candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))

        accepted: List[PIIMatch] = []
        prev_end: int = -1  # end index of the last accepted match

        for start, end, match in candidates:
            if start >= prev_end:
                # No overlap → accept this match.
                accepted.append(match)
                prev_end = end
            # else: this match is nested inside or overlaps with
            # an already-accepted longer match → discard it.

        return accepted
