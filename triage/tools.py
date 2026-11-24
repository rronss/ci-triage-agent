"""The agent's investigation tools. Every one is read-only.

Everything a tool returns is untrusted text (log lines, commit messages and diffs, AWS error messages can all
carry attacker-controlled content), so each result is redacted and size-capped before the model sees it.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .redact import redact

MAX_TOOL_CHARS = 6000
MAX_LINE_CHARS = 300
MAX_PATTERN_CHARS = 200

SEARCH_LOGS = "search_logs"
READ_LOG_LINES = "read_log_lines"
GET_RECENT_COMMITS = "get_recent_commits"
COMPARE_WITH_LAST_GREEN = "compare_with_last_green"
GET_COMMIT_DIFF = "get_commit_diff"
SEARCH_CLOUDTRAIL = "search_cloudtrail"

_SPECS = {
    SEARCH_LOGS: {
        "description": "Search the full log of the failed run (every failed job and step) for a regular expression or "
                       "plain text, case-insensitive. Use it to find the first error, earlier warnings, or other "
                       "occurrences of a message. Returns matching lines with line numbers (L<n>).",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Regular expression or plain text to look for."},
                "context": {"type": "integer", "description": "Lines of context around each match, 0 to 5. Default 1."},
            },
            "required": ["pattern"],
        },
    },
    READ_LOG_LINES: {
        "description": "Read a range of lines from the full log, by line number (1-based, at most 80 lines).",
        "input_schema": {
            "type": "object",
            "properties": {"start": {"type": "integer"}, "end": {"type": "integer"}},
            "required": ["start", "end"],
        },
    },
    GET_RECENT_COMMITS: {
        "description": "List the most recent commits on the branch, up to the commit this run built.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "How many commits, 1 to 20. Default 10."}},
        },
    },
    COMPARE_WITH_LAST_GREEN: {
        "description": "Compare this run's commit with the last run of the same workflow on the same branch that "
                       "succeeded: which commits and files changed in between. If the commit is the same, the code "
                       "did not change, so a code change is not the cause. Use it to decide whether a change in the "
                       "repository could have caused the failure.",
        "input_schema": {"type": "object", "properties": {}},
    },
    GET_COMMIT_DIFF: {
        "description": "Show the files and patch of one commit (message, changed files, the diff, truncated).",
        "input_schema": {
            "type": "object",
            "properties": {"sha": {"type": "string", "description": "Commit SHA, full or at least 7 characters."}},
            "required": ["sha"],
        },
    },
    SEARCH_CLOUDTRAIL: {
        "description": "Search AWS CloudTrail event history around the time of the run for changes to the cloud "
                       "environment (IAM policy edits, deleted or modified resources, rejected API calls). Use it "
                       "when the failure involves a deployment, permissions, or AWS resources, to find out whether "
                       "something changed in the cloud shortly before. With no filter it lists events that changed "
                       "something. At most one of event_name, user_name, resource_name. CloudTrail lags by up to "
                       "about 15 minutes, so a very recent call may not be visible yet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "minutes_before": {"type": "integer", "description": "How far before the run started to look, 1 to 1440. Default 60."},
                "event_name": {"type": "string", "description": "For example PutRolePolicy or DeleteBucket."},
                "user_name": {"type": "string"},
                "resource_name": {"type": "string"},
                "errors_only": {"type": "boolean", "description": "Only events that failed (AccessDenied and similar)."},
            },
        },
    },
}


def _clamp(value, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _clip(text: str, limit: int = MAX_TOOL_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 40] + "\n... [truncated]"


def _day(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Toolbox:
    """Holds what the tools can see: the redacted log, and (when known) the run, GitHub and CloudTrail."""

    def __init__(self, log_text: str, repo: str | None = None, run: dict | None = None, github=None, cloudtrail=None):
        self.lines = log_text.splitlines()
        self.repo, self.run, self.github, self.cloudtrail = repo, run, github, cloudtrail
        self.redactions = 0

    def available(self) -> list[str]:
        names = [SEARCH_LOGS, READ_LOG_LINES]
        if self.github and self.repo and self.run:
            names += [GET_RECENT_COMMITS, COMPARE_WITH_LAST_GREEN, GET_COMMIT_DIFF]
        if self.cloudtrail and self.run:
            names.append(SEARCH_CLOUDTRAIL)
        return names

    def specs(self) -> list[dict]:
        return [{"name": n, **_SPECS[n]} for n in self.available()]

    def call(self, name: str, args: dict | None) -> str:
        """Run one tool. Never raises: a failure is returned as text so the model can carry on."""
        if name not in self.available():
            return f"Unknown or unavailable tool: {name}."
        args = args if isinstance(args, dict) else {}
        try:
            result = getattr(self, f"_{name}")(**args)
        except TypeError as exc:
            result = f"Invalid arguments for {name}: {exc}"
        except Exception as exc:  # a tool failing (rate limit, permissions, network) must not stop the diagnosis
            result = f"{name} failed: {type(exc).__name__}: {exc}"
        result, count = redact(result)
        self.redactions += count
        return _clip(result)

    # ---- log tools
    def _search_logs(self, pattern: str, context: int = 1) -> str:
        pattern = str(pattern)[:MAX_PATTERN_CHARS]
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        context, out, matches, printed_to = _clamp(context, 0, 5, 1), [], 0, -1
        for i, line in enumerate(self.lines):
            if not rx.search(line):
                continue
            matches += 1
            if matches > 15:
                out.append("... more matches not shown; refine the pattern.")
                break
            if printed_to >= 0 and i - context > printed_to + 1:
                out.append("--")
            for j in range(max(i - context, printed_to + 1), min(len(self.lines), i + context + 1)):
                out.append(f"L{j + 1}: {self.lines[j][:MAX_LINE_CHARS]}")
                printed_to = j
        if not matches:
            return f"No line matches {pattern!r} in the {len(self.lines)}-line log."
        return "\n".join(out)

    def _read_log_lines(self, start: int, end: int) -> str:
        start = _clamp(start, 1, max(len(self.lines), 1), 1)
        end = _clamp(end, start, start + 79, start)
        chunk = self.lines[start - 1:end]
        if not chunk:
            return f"The log has {len(self.lines)} lines."
        return "\n".join(f"L{start + i}: {line[:MAX_LINE_CHARS]}" for i, line in enumerate(chunk))

    # ---- GitHub tools
    def _get_recent_commits(self, limit: int = 10) -> str:
        commits = self.github.list_commits(self.repo, self.run["head_sha"], _clamp(limit, 1, 20, 10))
        return "\n".join(_commit_line(c) for c in commits) or "No commits found."

    def _compare_with_last_green(self) -> str:
        green = self.github.last_green_run(self.repo, self.run)
        if green is None:
            return "There is no earlier successful run of this workflow on this branch to compare with."
        base, head = green["head_sha"], self.run["head_sha"]
        when = green.get("created_at", "unknown time")
        if green.get("head_branch") and green["head_branch"] != self.run.get("head_branch"):
            when += f", on branch {green['head_branch']} (this branch has no successful run of its own)"
        if base == head:
            return (f"The last successful run (id {green['id']}, {when}) built the same commit {head[:8]}. The code did "
                    "not change between that run and this failure, so a code change in this repository is not the cause.")
        diff = self.github.compare(self.repo, base, head)
        commits = diff.get("commits", [])
        lines = [f"Last successful run: id {green['id']}, {when}, commit {base[:8]}.",
                 f"This run: commit {head[:8]}. {len(commits)} commit(s) in between:"]
        lines += [_commit_line(c) for c in commits[:30]]
        files = diff.get("files", [])
        lines.append(f"{len(files)} file(s) changed:")
        lines += [f"  {f['filename']} ({f.get('status')}, +{f.get('additions', 0)}/-{f.get('deletions', 0)})"
                  for f in files[:40]]
        return "\n".join(lines)

    def _get_commit_diff(self, sha: str) -> str:
        sha = str(sha).strip()
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
            return "sha must be 7 to 40 hexadecimal characters."
        commit = self.github.get_commit(self.repo, sha)
        message = commit["commit"]["message"].splitlines()[0][:150]
        lines = [f"{commit['sha'][:8]} {message}"]
        for f in commit.get("files", [])[:20]:
            lines.append(f"--- {f['filename']} ({f.get('status')}, +{f.get('additions', 0)}/-{f.get('deletions', 0)})")
            lines.append((f.get("patch") or "(no patch: binary or too large)")[:1500])
        return "\n".join(lines)

    # ---- CloudTrail
    def _search_cloudtrail(self, minutes_before: int = 60, event_name: str | None = None,
                           user_name: str | None = None, resource_name: str | None = None,
                           errors_only: bool = False) -> str:
        filters = {k: v for k, v in (("event_name", event_name), ("user_name", user_name),
                                     ("resource_name", resource_name)) if v}
        if len(filters) > 1:
            return "Use at most one of event_name, user_name, resource_name."
        started = _day(self.run.get("run_started_at") or self.run.get("created_at")) or datetime.now(timezone.utc)
        ended = _day(self.run.get("updated_at")) or datetime.now(timezone.utc)
        start = started - timedelta(minutes=_clamp(minutes_before, 1, 1440, 60))
        attribute, value = next(iter(filters.items()), (None, None))
        events = self.cloudtrail.lookup(start, ended, attribute, value, limit=20)
        if errors_only:
            events = [e for e in events if e["error_code"]]
        header = f"CloudTrail events {start:%Y-%m-%d %H:%M} to {ended:%Y-%m-%d %H:%M} UTC, newest first:"
        if not events:
            return header + "\nNone found. Recent events can take up to about 15 minutes to appear."
        lines = [header]
        for e in events[:20]:
            line = f"{e['time']}  {e['event']} ({e['service']})  by {e['user'] or 'unknown'}"
            if e["error_code"]:
                line += f"  ERROR {e['error_code']}: {e['error_message']}"
            if e["resources"]:
                line += f"  resources: {', '.join(e['resources'])}"
            lines.append(line)
        return "\n".join(lines)


def _commit_line(c: dict) -> str:
    message = (c["commit"]["message"].splitlines() or [""])[0][:100]
    date = (c["commit"].get("author") or {}).get("date", "")
    return f"{c['sha'][:8]} {date} {message}"
