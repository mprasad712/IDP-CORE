"""Is an extracted value actually present in the document?

This answers ONE question — *grounded or not* — and deliberately does not produce a score. It used to:
``extraction._ocr_evidence`` returned a float derived from how much of an OCR token the value covered, and
that float became the field's ``confidence_score``. Three things were wrong with that.

1. **It measured tokenization, not correctness.** ``text_layer`` emits one token per *word* for a digital
   PDF but one token per *paragraph* for a docx. The substring score ``(0.70 + ratio × 0.17)`` divides by
   token length, so the same perfect extraction scored 1.0 from a PDF and 0.746 from a docx — the file
   format decided the confidence.
2. **It punished the model for obeying the prompt.** The prompt says "dates as YYYY-MM-DD"; the page says
   "30 April 2022"; the value scored 0.285 — the band reserved for invented values.
3. **It discarded the model's judgement where they agreed.** An exact token match returned
   ``0.88 + ocr_conf × 0.12`` without ever reading ``llm_conf``, so a value the model flagged as a guess
   scored 1.0 because the string happened to appear on the page.

So: ``confidence_score`` is now the model's own number, and grounding is an INDEPENDENT boolean that the
model does not control. That separation is the point — a hallucinated value carries high confidence (that
is what hallucination *is*), and only an outside check can catch it.

``grounded`` is tri-state:

    True   — found in the document's token stream
    False  — a token stream exists and the value is NOT in it   → hallucination shape, route to review
    None   — no token stream to check against (vision path, or text extraction failed) → unknown, not a
             failure. Treating this as False would send every vision document to review.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

#: Longest run of adjacent tokens joined when looking for a multi-word value ("ELLINGTON WOOD DECOR").
_MAX_SPAN = 12
#: Tokens of surrounding context quoted into `reasoning_trace`, and the cap on that quote.
_SNIPPET_CONTEXT = 2
_SNIPPET_LIMIT = 160

#: Whitespace to collapse. ``\u00a0`` is written as an escape on purpose: it is invisible in source.
_WS = re.compile("[\\s\\u00a0]+")
#: Thousands separators and currency markers, stripped from BOTH sides so "2,511.54" matches "2511.54"
#: and "£600.00" matches "600.00". Applied to the document text too, not just to the value.
#:
#: U+00A0 is in this class because it is a THOUSANDS SEPARATOR in fr/de locales ("2\u00a0511,54") and must
#: vanish rather than become a word gap. An ordinary space is deliberately NOT in it: removing spaces would
#: collapse "ACME CORP" to "acmecorp" and let a value match across any run of adjacent words.
_NOISE = re.compile("[,\\u00a0$£€₹¥]")
_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def normalize(text: Any) -> str:
    """Casefold, strip currency/thousands noise, collapse whitespace. Applied to values AND tokens."""
    s = _NOISE.sub("", str(text or "").strip().lower())
    return _WS.sub(" ", s).strip()


def _date_variants(value: str) -> List[str]:
    """An ISO date the model was told to emit, rendered the ways a document might actually print it.

    The extraction prompt mandates ``YYYY-MM-DD``. A document almost never contains that string, so
    without this every single date field reads as ungrounded.
    """
    try:
        d = date.fromisoformat(value.strip())
    except (ValueError, AttributeError):
        return []

    month, day, year = _MONTHS[d.month - 1], d.day, d.year
    dd, mm = f"{d.day:02d}", f"{d.month:02d}"
    return [
        f"{day} {month} {year}", f"{month} {day} {year}", f"{month} {day}, {year}",
        f"{day} {month[:3]} {year}", f"{month[:3]} {day} {year}", f"{month[:3]} {day}, {year}",
        f"{dd}/{mm}/{year}", f"{mm}/{dd}/{year}", f"{dd}-{mm}-{year}", f"{mm}-{dd}-{year}",
        f"{dd}.{mm}.{year}", f"{year}/{mm}/{dd}", f"{year}-{mm}-{dd}",
        f"{d.day}/{d.month}/{year}", f"{d.month}/{d.day}/{year}",
    ]


def _numeric_variants(value: str) -> List[str]:
    """``600.00`` also prints as ``600``; ``600`` also prints as ``600.00``. Commas are already stripped."""
    v = value.strip()
    try:
        f = float(v)
    except (TypeError, ValueError):
        return []
    out = []
    if f == int(f):
        out += [str(int(f)), f"{int(f)}.00", f"{int(f)}.0"]
    out.append(f"{f:.2f}")
    return [x for x in out if x != v]


def value_variants(value: str) -> List[str]:
    """Every spelling of ``value`` worth looking for, normalized. First entry is the value itself."""
    nv = normalize(value)
    seen, out = {nv}, [nv]
    for raw in (*_date_variants(str(value).strip()), *_numeric_variants(nv)):
        n = normalize(raw)
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


_NUMERIC = re.compile(r"^[\d./-]+$")


def _contains(haystack: str, needle: str) -> bool:
    """``needle`` occurs in ``haystack`` — with a boundary guard for numbers.

    A bare substring test grounds quantity ``1`` against the ``1`` inside ``2511.54``, and ``600`` against
    ``1600.00``. So a numeric value may not sit flush against another digit (or a decimal point, which would
    let ``600`` match ``600.005``).

    Only digits and ``.`` are barriers — NOT letters. ``600.00`` must still match the token ``GBP600.00``,
    and a currency prefix is exactly how a real invoice prints a total. Words need no guard at all: the
    value ``GBP`` has to match ``GBP600.00`` too.
    """
    if not needle:
        return False
    if _NUMERIC.match(needle):
        return re.search(rf"(?<![0-9.]){re.escape(needle)}(?![0-9.])", haystack) is not None
    return needle in haystack


def _union_box(tokens: Iterable[Dict[str, Any]]) -> Optional[list]:
    """Bounding box enclosing a run of tokens, or None if any of them has none (docx, txt, xlsx).

    The old code took the bbox of whichever token matched *first*, so a five-line ``bill_to_address``
    highlighted the single word "Ellington". A multi-token value needs the union or nothing.
    """
    xs: List[float] = []
    ys: List[float] = []
    for t in tokens:
        box = t.get("bounding_box")
        if not box:
            return None
        for point in box:
            try:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
            except (TypeError, ValueError, IndexError):
                return None
    if not xs:
        return None
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


class Grounder:
    """Checks values against one document's token stream. Build once per document, not per field.

    ``_ocr_evidence`` rebuilt the joined document text on every field; on a 40-field config over a
    multi-page scan that is 40 full passes over the token list.
    """

    __slots__ = ("_tokens", "_norm", "available")

    def __init__(self, tokens: Optional[List[Dict[str, Any]]] = None) -> None:
        self._tokens: List[Dict[str, Any]] = [t for t in (tokens or []) if str(t.get("text", "")).strip()]
        self._norm: List[str] = [normalize(t.get("text")) for t in self._tokens]
        #: False on the vision path (no OCR text) — then every check returns `grounded=None`, never False.
        self.available: bool = bool(self._tokens)

    def _hit(self, haystack: str, needle: str) -> bool:
        """Match ``needle`` in ``haystack``, also with the run's spaces removed.

        A digital PDF splits ``2,511.54`` into the tokens ``2,`` and ``511.54``; joined with a space that
        is ``2 511.54``, which contains no ``2511.54``. Spaceless matching is only safe when the value
        itself has no spaces — otherwise ``ELLINGTON WOOD DECOR`` would match ``ellingtonwooddecor``
        anywhere in the stream.
        """
        if _contains(haystack, needle):
            return True
        return " " not in needle and _contains(haystack.replace(" ", ""), needle)

    def _find_span(self, needle: str) -> Optional[tuple]:
        """Index range (i, j) of the SHORTEST adjacent token run containing ``needle``, or None.

        Shortest-first, not first-found. Over the word tokens ``["invoice", "number", "042022"]`` a
        left-to-right scan finds ``042022`` inside the joined run ``invoice number 042022`` starting at
        token 0, and the bounding box would then enclose all three words. Widening the window only after
        every narrower one has failed keeps the highlight on the value itself.

        Handles both tokenizations in one pass: a docx paragraph token *contains* the value outright
        (width 1), while a digital-PDF word-token stream needs several tokens joined.
        """
        if not needle:
            return None
        n = len(self._norm)
        for width in range(1, _MAX_SPAN + 1):
            if width > n:
                break
            for i in range(n - width + 1):
                acc = " ".join(self._norm[i : i + width]).strip()
                if acc and self._hit(acc, needle):
                    return i, i + width - 1
        return None

    def _snippet(self, i: int, j: int) -> str:
        """The document text around the matched span, quoted verbatim, for a human reviewer.

        Widened by a couple of tokens so a bare ``042022`` reads back as ``Invoice Number : 042022 Date``.
        This is quoted from the DOCUMENT, not narrated by the model: a model asked to justify a value it
        invented will invent a justification too, and that string is what used to fill ``reasoning_trace``.
        """
        lo, hi = max(0, i - _SNIPPET_CONTEXT), min(len(self._tokens), j + 1 + _SNIPPET_CONTEXT)
        text = _WS.sub(" ", " ".join(str(t.get("text", "")).strip() for t in self._tokens[lo:hi])).strip()
        return text if len(text) <= _SNIPPET_LIMIT else text[: _SNIPPET_LIMIT - 1].rstrip() + "…"

    def check(self, value: Any) -> tuple:
        """``(source_location | None, grounded: bool | None)`` for one extracted value.

        ``source_location`` carries ``page_number``, the union ``bounding_box`` of the value's own tokens,
        the lowest OCR ``confidence`` among them, and a verbatim ``snippet`` of the surrounding text.
        """
        if value is None or not str(value).strip():
            return None, None
        if not self.available:
            return None, None  # nothing to check against — unknown, NOT ungrounded

        for variant in value_variants(str(value)):
            span = self._find_span(variant)
            if span is None:
                continue
            i, j = span
            run = self._tokens[i : j + 1]
            confs = [float(t.get("confidence", 1.0)) for t in run]
            return {
                "page_number": run[0].get("page_number", 1),
                "bounding_box": _union_box(run),
                "confidence": round(min(confs), 4) if confs else 1.0,
                "snippet": self._snippet(i, j),
            }, True

        return None, False


def evidence_trace(grounded: Optional[bool], source_location: Optional[Dict[str, Any]]) -> Optional[str]:
    """A ``reasoning_trace`` derived from the document, not from the model.

    The extraction prompt no longer asks for ``reasoning``: a 100–200 char string per cell × N fields × M
    rows is what overflowed the output-token budget and truncated ``line_items`` to empty. It was also the
    wrong source — a model narrating why it produced a hallucinated value narrates a hallucination.

    Grounding already knows exactly which tokens matched, so the trace is free and it is true.
    """
    if grounded is True and source_location:
        page = source_location.get("page_number", 1)
        snippet = source_location.get("snippet")
        return f'Found on page {page}: "{snippet}"' if snippet else f"Found on page {page}"
    if grounded is False:
        return "Not found in the document text — inferred, calculated, or reformatted by the model"
    return None  # unknown: no token stream to check against (vision run)


def grounding_summary(grounded_flags: Iterable[Optional[bool]]) -> Dict[str, int]:
    """Counts for logs and payloads. ``unknown`` is not a failure; ``ungrounded`` is."""
    flags = list(grounded_flags)
    return {
        "checked": sum(1 for g in flags if g is not None),
        "grounded": sum(1 for g in flags if g is True),
        "ungrounded": sum(1 for g in flags if g is False),
        "unknown": sum(1 for g in flags if g is None),
    }
