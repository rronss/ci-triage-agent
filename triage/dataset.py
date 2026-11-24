from __future__ import annotations

import json
import re
from pathlib import Path

from .diagnose import prepare
from .models import DEPENDENCY_ENV, FLAKY_TEST, INFRA_FAILURE, REAL_FAILURE

# Answer key for the demo workflow's neutral case codes. Only this file maps codes to labels, so the
# label never appears in anything the model reads (workflow, run title, job log).
CASE_LABELS = {
    "case_a": REAL_FAILURE,
    "case_b": FLAKY_TEST,
    "case_c": INFRA_FAILURE,
    "case_d": DEPENDENCY_ENV,
    # The deploy runs while the sandbox's deploy permission has been edited in AWS (terraform: deploy_can_write=false).
    "case_e": INFRA_FAILURE,
}
# Runs triggered by a push to a prepared branch carry no case code, so they are labelled by branch name. The
# branch holds one commit that breaks the code. The name is neutral on purpose: it reaches the model as metadata.
BRANCH_LABELS = {
    "sandbox/change-1": REAL_FAILURE,
}
_TITLE = re.compile(r"^CI \((\w+)\)")


def build_dataset(github, repo: str, out_dir: Path, workflow: str = "ci.yml") -> list[dict]:
    """Download redacted logs of the demo repo's failed runs and label them from the case code in the run title."""
    labels_path = out_dir / "labels.json"
    entries = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.exists() else []
    seen = {e["id"] for e in entries}
    added = []

    for run in github.list_failed_runs(repo, workflow):
        match = _TITLE.match(run.get("display_title", ""))
        label = CASE_LABELS.get(match.group(1)) if match else None
        label = label or BRANCH_LABELS.get(run.get("head_branch", ""))
        entry_id = f"run-{run['id']}"
        if label is None or entry_id in seen:
            continue
        meta, log_text = github.fetch_failure(repo, run["id"])
        text, _ = prepare(log_text)
        log_file = out_dir / "logs" / f"{entry_id}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(text, encoding="utf-8")
        added.append({"id": entry_id, "log": f"logs/{entry_id}.log", "label": label, "meta": meta})

    labels_path.write_text(json.dumps(entries + added, indent=2) + "\n", encoding="utf-8")
    return added
