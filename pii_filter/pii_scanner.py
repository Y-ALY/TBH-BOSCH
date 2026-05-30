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
    (?<!\d)                          # not preceded by a digit
    (?:
        \+?\d{1,3}                   # optional country code
        [-.\s]?
    )?
    \(?\d{2,4}\)?                    # area code (with or without parens)
    [-.\s]?
    \d{3,4}                          # subscriber part 1
    [-.\s]?
    \d{3,5}                          # subscriber part 2
    (?!\d)                           # not followed by a digit
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

    # ── Radix sort helper (stable counting sort per digit) ───
    @staticmethod
    def _radix_sort_candidates(
        candidates: List[Tuple[int, int, PIIMatch]],
    ) -> List[Tuple[int, int, PIIMatch]]:
        """
        Sort *candidates* using **LSD Radix Sort** (least-significant
        digit first) with a stable counting-sort subroutine on each
        digit position.

        Sorting criteria (matching the original Timsort logic):

        * **Primary key** – ``start`` index, ascending.
        * **Secondary key** – span length ``(end - start)``,
          **descending**.  Achieved by sorting on
          ``max_span - span`` so that radix works on non-negative
          integers only.

        Because LSD radix processes the *least-significant key first*,
        we sort by the secondary key (inverted span) first, then by
        the primary key (start).  Stability guarantees the secondary
        ordering is preserved within equal primary-key groups.

        Args:
            candidates: A list of ``(start, end, PIIMatch)`` tuples.

        Returns:
            A new list sorted by ``(start ASC, span DESC)``.
        """
        if len(candidates) <= 1:
            return list(candidates)

        # Pre-compute keys ───────────────────────────────────
        max_span = max(e - s for s, e, _ in candidates)
        max_start = max(c[0] for c in candidates)

        # Inverted span so that *larger* spans get *smaller* sort
        # keys, yielding descending order after a normal ascending
        # radix pass.
        inv_spans = [max_span - (e - s) for s, e, _ in candidates]
        starts = [c[0] for c in candidates]

        # ── Stable counting sort on a single digit ──────────
        def _counting_sort_by_digit(
            items: List[Tuple[int, int, PIIMatch]],
            keys: List[int],
            digit_pos: int,
            base: int = 10,
        ) -> Tuple[List[Tuple[int, int, PIIMatch]], List[int]]:
            """
            Perform one pass of counting sort on *items* using the
            digit at position *digit_pos* (0 = ones, 1 = tens, …)
            extracted from the corresponding *keys*.

            Returns the reordered ``(items, keys)`` pair so that
            subsequent passes can use the updated key order.
            """
            n = len(items)
            divisor = base ** digit_pos

            # Extract digit for each item
            digits = [(k // divisor) % base for k in keys]

            # Count occurrences
            count = [0] * base
            for d in digits:
                count[d] += 1

            # Prefix sum → starting positions
            for i in range(1, base):
                count[i] += count[i - 1]

            # Build output in reverse for stability
            out_items: List[Optional[Tuple[int, int, PIIMatch]]] = [None] * n
            out_keys: List[int] = [0] * n

            for i in range(n - 1, -1, -1):
                d = digits[i]
                count[d] -= 1
                pos = count[d]
                out_items[pos] = items[i]
                out_keys[pos] = keys[i]

            return out_items, out_keys  # type: ignore[return-value]

        # ── Number of digit passes needed ────────────────────
        def _num_digits(value: int, base: int = 10) -> int:
            if value == 0:
                return 1
            count = 0
            while value > 0:
                count += 1
                value //= base
            return count

        result = list(candidates)

        # Pass 1 — sort by SECONDARY key (inverted span) first
        sec_digits = _num_digits(max_span)
        current_keys = list(inv_spans)
        for d in range(sec_digits):
            result, current_keys = _counting_sort_by_digit(
                result, current_keys, d,
            )

        # Pass 2 — sort by PRIMARY key (start index)
        pri_digits = _num_digits(max_start)
        current_keys = [c[0] for c in result]  # re-extract starts after reorder
        for d in range(pri_digits):
            result, current_keys = _counting_sort_by_digit(
                result, current_keys, d,
            )

        return result

    # ── Overlap resolution ───────────────────────────────────
    @staticmethod
    def _resolve_overlaps(
        candidates: List[Tuple[int, int, PIIMatch]],
    ) -> List[PIIMatch]:
        """
        Remove overlapping matches, always keeping the *longer*
        (more specific) match when two intervals collide.

        Algorithm (greedy interval scheduling):
            1. Sort by start index ASC, then by span length DESC
               using a stable LSD Radix Sort.
               → At any position, the longest match comes first.
            2. Sweep left-to-right, tracking ``prev_end`` (the end
               index of the last accepted match).
            3. Accept a candidate only if ``start >= prev_end``
               (no overlap with the previous accepted match).

        Returns:
            A list of :class:`PIIMatch` in reading order (by
            ``char_offset``), with all nested / overlapping
            duplicates removed.
        """
        if not candidates:
            return []

        # Radix sort replaces the previous Timsort call:
        #   candidates.sort(key=lambda c: (c[0], -(c[1] - c[0])))
        candidates = PIIScanner._radix_sort_candidates(candidates)

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


