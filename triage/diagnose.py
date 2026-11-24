from __future__ import annotations

import sys

from . import investigate as agent
from . import llm, rules
from .logs import clean, extract_excerpt
from .models import NEEDS_REVIEW, Diagnosis
from .redact import redact
from .tools import Toolbox

MODES = ("hybrid", "rules", "llm", "agent")


def prepare(log_text: str) -> tuple[str, int]:
    """Clean and redact a raw log. Redaction runs on the full text so multi-line secrets are never cut in half."""
    return redact(clean(log_text))


def diagnose_text(
    log_text: str,
    meta: dict | None = None,
    mode: str = "hybrid",
    known_flaky: tuple[str, ...] = (),
    client=None,
    model: str | None = None,
    effort: str | None = None,
    tool_context: dict | None = None,
) -> Diagnosis:
    """tool_context (agent mode): repo, run, github and cloudtrail for the investigation tools. Without it the agent
    can still search the log it was given."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")

    text, redactions = prepare(log_text)
    rule = None if mode == "llm" else rules.match(text, known_flaky)
    toolbox = None

    if mode == "rules":
        diagnosis = rule or Diagnosis(
            category=NEEDS_REVIEW,
            confidence="low",
            summary="No rule matched this failure.",
            next_step="Triage this failure manually from the run logs.",
            source="rules",
        )
    elif mode in ("hybrid", "agent") and rule is not None and rule.confidence == "high":
        diagnosis = rule
    else:
        toolbox = Toolbox(text, **(tool_context or {})) if mode == "agent" else None
        try:
            if toolbox is not None:
                diagnosis = agent.investigate(meta, extract_excerpt(text), toolbox, hint=rule, client=client,
                                              model=model, effort=effort)
            else:
                diagnosis = llm.classify(meta, extract_excerpt(text), hint=rule, client=client, model=model,
                                         effort=effort)
        except llm.LLMUnavailable as exc:
            print(f"warning: LLM unavailable: {exc}", file=sys.stderr)
            diagnosis = Diagnosis(
                category=NEEDS_REVIEW,
                confidence="low",
                summary=f"Automatic analysis was unavailable ({exc}).",
                next_step="Triage this failure manually from the run logs.",
                source="error",
            )
    diagnosis.redactions = redactions + (toolbox.redactions if toolbox is not None else 0)
    return diagnosis


def diagnose_run(repo: str, run_id: int, github=None, **kwargs) -> tuple[Diagnosis, dict]:
    from .github import GitHubClient

    github = github or GitHubClient()
    meta, log_text = github.fetch_failure(repo, run_id)
    if kwargs.get("mode") == "agent" and "tool_context" not in kwargs:
        from .aws import CloudTrailClient

        kwargs["tool_context"] = {"repo": repo, "run": github.get_run(repo, run_id), "github": github,
                                  "cloudtrail": CloudTrailClient.from_env()}
    return diagnose_text(log_text, meta=meta, **kwargs), meta
