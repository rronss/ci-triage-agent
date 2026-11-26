from __future__ import annotations

import requests

from .models import DEPENDENCY_ENV, FLAKY_TEST, INFRA_FAILURE, NEEDS_REVIEW, REAL_FAILURE, Diagnosis

LABELS = {
    REAL_FAILURE: "Real failure",
    FLAKY_TEST: "Flaky test",
    INFRA_FAILURE: "Infra failure",
    DEPENDENCY_ENV: "Dependency / environment",
    NEEDS_REVIEW: "Needs human review",
}
# The only colour in the message: a thin bar beside the evidence, one muted colour per category.
COLORS = {
    REAL_FAILURE: "#B42318",  # red
    FLAKY_TEST: "#B54708",  # amber
    INFRA_FAILURE: "#175CD3",  # blue
    DEPENDENCY_ENV: "#6941C6",  # purple
    NEEDS_REVIEW: "#98A2B3",  # grey
}
MAX_TEXT = 2800  # Slack section text limit is 3000


def escape(text: str) -> str:
    """Escape Slack control characters so log-derived text cannot create mentions or links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": _clip(text)}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": _clip(text)}]}


def _repository(meta: dict, run_url: str) -> str:
    """owner/name from the run's metadata, or else from the run's URL."""
    if meta.get("repository"):
        return str(meta["repository"])
    parts = run_url.split("github.com/")[-1].split("/")
    return "/".join(parts[:2]) if len(parts) >= 2 else "unknown"


def build_message(d: Diagnosis, meta: dict | None, run_url: str) -> dict:
    meta = meta or {}
    repo = _clip(escape(_repository(meta, run_url)).replace("|", "/"), 200)
    workflow = _clip(escape(str(meta.get("workflow") or "CI")), 200)
    branch = _clip(escape(str(meta.get("branch") or "unknown")), 200)
    label = LABELS.get(d.category, d.category)
    confidence = f"{escape(d.confidence).capitalize()} confidence"

    where = [f"<https://github.com/{repo}|{repo}>", workflow, f"`{branch}`"]
    if str(meta.get("run_attempt") or "1") != "1":
        where.append(f"attempt {escape(str(meta['run_attempt']))}")
    where.append(f"<{run_url}|View run>")

    source = ["Triage agent", d.source]
    if d.steps:
        tools = ", ".join(dict.fromkeys(step["tool"] for step in d.steps))
        source.append(f"{len(d.steps)} tool call(s): {tools}")
    source.append("Check against the log before acting")

    # Everything needed to act is in the top-level blocks, which Slack never folds. The coloured card, which Slack may
    # fold behind "Show more" when long, holds only the evidence and where the diagnosis came from.
    blocks = [
        _section(f"*{label}*  ·  {confidence}"),
        _context("  ·  ".join(where)),
        _section(escape(d.summary)),
        _section(f"*Next step*\n{escape(d.next_step)}"),
    ]
    card = []
    if d.evidence:
        evidence = "\n".join(escape(line).replace("```", "'''") for line in d.evidence)
        card.append(_section(f"```{_clip(evidence, MAX_TEXT - 10)}```"))  # clip the lines, not the closing fence
    card.append(_context("  ·  ".join(source)))
    return {
        "text": f"CI failure in {repo}: {label} ({d.confidence} confidence)",  # notification preview
        "blocks": blocks,
        "attachments": [{"color": COLORS.get(d.category, COLORS[NEEDS_REVIEW]), "blocks": card}],
        "unfurl_links": False,  # no GitHub preview cards under the message
        "unfurl_media": False,
    }


def post(webhook_url: str, payload: dict, session=None) -> None:
    response = (session or requests).post(webhook_url, json=payload, timeout=15)
    if response.status_code != 200 or response.text.strip() != "ok":
        raise RuntimeError(f"Slack rejected the message: HTTP {response.status_code} {response.text[:200]}")
