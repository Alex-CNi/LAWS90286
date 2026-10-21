"""
core/results.py — THE RESULT OF ONE CHECK (shared by the gates, the hard checks
and the report).

Outcomes, from the knowledge base's severity rules (SCHEMA.md):
  pass        — the check is met
  caution     — shown to the user; never blocks (anything that depends on a
                Manual flag is at most a caution)
  error       — the Manual makes the result unlawful or erroneous on every
                reading; triggers the revision loop
  unresolved  — the Manual (or the facts) cannot settle it; shown as a caution
  info        — background, e.g. a rule that did not arise
"""

from __future__ import annotations

from dataclasses import dataclass, field

OUTCOME_ORDER = {"error": 0, "unresolved": 1, "caution": 2, "pass": 3, "info": 4}


@dataclass
class CheckResult:
    check_id: str                 # the knowledge-base item that states the rule (e.g. audit.cco_limits)
    title: str                    # short heading
    outcome: str                  # pass | caution | error | unresolved | info
    message: str                  # plain-English explanation
    limb: str = ""                # which part of the rule (e.g. "(2) duration")
    flags: list[str] = field(default_factory=list)       # flag.* ids engaged
    cite: list[str] = field(default_factory=list)        # item ids whose quotes support the message
    app_policy: bool = False      # True = the app's handling where the Manual is silent


def worst(results: list[CheckResult]) -> str:
    if not results:
        return "pass"
    return min(results, key=lambda r: OUTCOME_ORDER.get(r.outcome, 9)).outcome


def errors(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.outcome == "error"]


def sort_results(results: list[CheckResult]) -> list[CheckResult]:
    return sorted(results, key=lambda r: OUTCOME_ORDER.get(r.outcome, 9))
