"""Code checks on an answer: is it backed by the data the model was given?

One set of checks for three places: the guardrail on every answer (here),
the offline eval runner, and the nightly job over production traces.

Hard check (enforced): every number with a dive unit must appear in what
the model was given. A sentence that fails is removed from the answer.
Soft checks (reported only): banned stock images, DAN named without a
link, the roast format.
"""

import re
from dataclasses import dataclass, field

# Numbers that are diving knowledge rather than data: limits and drills the
# prompt and DAN material talk about. A sentence quoting them is not
# inventing anything.
SAFETY_CONSTANTS = (
    "3 min 5 min 3 m 5 m 6 m 8 m 9 m 10 m 18 m 30 m 40 m 9 m/min 10 m/min "
    "18 m/min 50 bar 1.4 1.6 70 bar 200 bar 232 bar 300 bar"
)

_UNIT = r"(?:m/min|L/min|l/min|°C|bar|metres?|meters?|minutes?|mins?|m)"
# "16.2 m/min", "37.5 m", "9-metre", "59 L/min", "14 °C"
_MEASURE = re.compile(rf"(?<![\w.,])(\d+(?:\.\d+)?)\s?-?\s?{_UNIT}(?![\w/])")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")

_BANNED = re.compile(
    r"\b(missiles?|rockets?|launch(?:es|ed|ing)?|elevators?|drive[- ]thru|"
    r"chamber ride|treats? (?:the |your )?(?:limits?|NDL) (?:as|like) (?:a )?suggestions?)\b",
    re.IGNORECASE,
)
_DAN_LINK = re.compile(r"\[DAN: [^\]]+\]\([^)]+\)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\[*\"'])")


# Logs this small are checked against every dive's numbers: few enough that
# a made-up value rarely matches by chance.
SMALL_LOG_DIVES = 10


@dataclass
class DiveFacts:
    """One dive as the model saw it: what names it, and its summary line."""

    names: list[str]  # site name, "#12", "dive 12"
    line: str


@dataclass
class Grounding:
    """What the model was given, split so each sentence is checked against
    the dives it talks about (plus everything log-wide) rather than against
    every number in a 200-dive log, which matches almost anything."""

    general: str  # prompt, log-wide aggregates, DAN material, tool output, the message
    dives: list[DiveFacts] = field(default_factory=list)

    def values_for(self, sentence: str) -> list[float]:
        lowered = sentence.lower()
        if len(self.dives) <= SMALL_LOG_DIVES:
            named = self.dives
        else:
            named = [
                d for d in self.dives if any(n.lower() in lowered for n in d.names)
            ]
        return grounded_values(self.general + "\n" + "\n".join(d.line for d in named))


@dataclass
class Report:
    stripped: list[str] = field(default_factory=list)  # removed sentences
    ungrounded: list[str] = field(default_factory=list)  # e.g. "23.4 m/min"
    banned: list[str] = field(default_factory=list)
    dan_without_link: bool = False
    format_issues: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.ungrounded


def _decimals(s: str) -> int:
    return len(s.split(".", 1)[1]) if "." in s else 0


def grounded_values(grounding: str) -> list[float]:
    return sorted(
        {float(n) for n in _NUMBER.findall(grounding + " " + SAFETY_CONSTANTS)}
    )


def is_grounded(number: str, values: list[float]) -> bool:
    """True if ``number`` is one of ``values`` as written (rounding allowed).

    "16.2" matches 16.24; "38" matches 37.5 (rounded to whole metres).
    """
    x = float(number)
    tolerance = 0.5 * 10 ** -_decimals(number) + 1e-9
    return any(abs(v - x) <= tolerance for v in values)


def ungrounded_measures(sentence: str, values: list[float]) -> list[str]:
    return [
        m.group(0).strip()
        for m in _MEASURE.finditer(sentence)
        if not is_grounded(m.group(1), values)
    ]


def _is_advice(line: str) -> bool:
    return line.lstrip("-*• ").lower().startswith("fix it")


def check_and_strip(
    text: str, grounding: Grounding, *, is_roast: bool, prose: bool = True
) -> tuple[str, Report]:
    """Remove sentences quoting numbers the model wasn't given; report the rest.

    A sentence's numbers must come from the dives it names (by site or
    number) or from log-wide facts. The "Fix it:" line is advice (drills,
    target rates), not a claim about the log, so it isn't checked.
    """
    report = Report()
    kept_lines = []
    for line in text.splitlines():
        if not line.strip() or _is_advice(line):
            kept_lines.append(line)
            continue
        prefix = re.match(r"\s*(?:[-*•]|\d+\.)\s+", line)
        lead = prefix.group(0) if prefix else ""
        sentences = _SENTENCE_END.split(line[len(lead) :])
        kept = []
        for sentence in sentences:
            bad = ungrounded_measures(sentence, grounding.values_for(sentence))
            if bad:
                report.ungrounded.extend(bad)
                report.stripped.append(sentence)
            else:
                kept.append(sentence)
        if kept:
            kept_lines.append(lead + " ".join(kept))
        elif not lead:
            kept_lines.append("")  # keep paragraph breaks; bullets just go
    cleaned = re.sub(r"\n{3,}", "\n\n", "\n".join(kept_lines)).strip()

    report.banned = sorted({m.group(0).lower() for m in _BANNED.finditer(cleaned)})
    without_links = _DAN_LINK.sub("", cleaned)
    report.dan_without_link = bool(re.search(r"\bDAN\b", without_links))
    report.format_issues = format_issues(cleaned, is_roast=is_roast, prose=prose)
    return cleaned, report


def format_issues(text: str, *, is_roast: bool, prose: bool = True) -> list[str]:
    """Format slips against the prompt's rules (prose for v6 on, bullets before)."""
    words = len(re.findall(r"\w+", _DAN_LINK.sub("", text)))
    bullets = [ln for ln in text.splitlines() if re.match(r"\s*(?:[-*•]|\d+\.)\s", ln)]
    issues = []
    if prose and bullets:
        issues.append(f"{len(bullets)} list lines")
    if is_roast:
        if not prose and len(bullets) > 4:
            issues.append(f"{len(bullets)} bullets")
        if not any(_is_advice(ln) for ln in text.splitlines()):
            issues.append("no Fix it line")
        if words > 150:  # 120 asked; some slack before it counts
            issues.append(f"{words} words")
    elif words > 90:  # 60 asked
        issues.append(f"{words} words")
    return issues
