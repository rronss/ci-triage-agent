"""The agent loop and its tools, tested offline: a scripted model, a fake GitHub and a fake CloudTrail."""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from triage import aws
from triage.diagnose import diagnose_run, diagnose_text
from triage.investigate import SUBMIT
from triage.models import NEEDS_REVIEW
from triage.slack import build_message
from triage.tools import Toolbox

from conftest import ANSWER

ASSERTION_LOG = "FAILED tests/test_orders.py::test_apply_discount - assert 240.0 == 225.0\n##[error]Process completed with exit code 1."
SUBMIT_REAL = {"category": "real_failure", "confidence": "high", "summary": "A change broke the discount.",
               "evidence": ["[compare_with_last_green] 1 commit(s) in between"], "next_step": "Revert abc12345."}


def use(name, **args):
    return SimpleNamespace(type="tool_use", id=f"id_{name}", name=name, input=args)


class ScriptedModel:
    """Stands in for anthropic.Anthropic: each call returns the next scripted list of tool calls."""

    def __init__(self, *turns):
        self.turns, self.calls, self.seen = list(turns), [], []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        self.seen.append(list(kwargs["messages"]))  # the list is extended later, so keep a snapshot
        blocks = self.turns.pop(0)
        return SimpleNamespace(model="claude-sonnet-5-5", stop_reason="tool_use", content=blocks,
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def submit(data=None):
    return [use(SUBMIT, **(data or SUBMIT_REAL))]


class FakeGitHub:
    def __init__(self, green_sha="aaa11111", head_sha="bbb22222", green=True, fail=None):
        self.green_sha, self.head_sha, self.green, self.fail = green_sha, head_sha, green, fail
        self.calls = []

    def last_green_run(self, repo, run):
        self.calls.append("last_green_run")
        if self.fail:
            raise self.fail
        return {"id": 5, "created_at": "2026-11-24T08:00:00Z", "head_sha": self.green_sha} if self.green else None

    def compare(self, repo, base, head):
        return {"commits": [{"sha": "bbb22222cccc", "commit": {"message": "Change the discount rule\n\nbody",
                                                                 "author": {"date": "2026-11-24T08:30:00Z"}}}],
                "files": [{"filename": "app/orders.py", "status": "modified", "additions": 3, "deletions": 1}]}

    def list_commits(self, repo, sha, limit):
        return [{"sha": "bbb22222cccc", "commit": {"message": "Rotate key token=abcd1234efgh", "author": {"date": "d"}}}]

    def get_commit(self, repo, sha):
        return {"sha": sha, "commit": {"message": "Change the discount rule"},
                "files": [{"filename": "app/orders.py", "status": "modified", "additions": 1, "deletions": 1,
                           "patch": "-    return price * 0.9\n+    return price - 10"}]}


RUN = {"id": 9, "workflow_id": 1, "head_branch": "main", "head_sha": "bbb22222", "created_at": "2026-11-24T09:00:00Z",
       "run_started_at": "2026-11-24T09:00:05Z", "updated_at": "2026-11-24T09:01:00Z"}


def context(**overrides):
    return {"repo": "o/r", "run": RUN, "github": FakeGitHub(), **overrides}


# ------------------------------------------------------------------ the loop
def test_rule_match_still_skips_the_model():
    model = ScriptedModel()
    d = diagnose_text("curl: (28) Connection timed out after 5001 milliseconds", mode="agent", client=model)
    assert d.source == "rules" and model.calls == []


def test_the_agent_investigates_then_submits():
    model = ScriptedModel([use("compare_with_last_green")], submit())
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=model, tool_context=context())
    assert d.category == "real_failure" and d.confidence == "high" and d.source == "llm"
    assert [s["tool"] for s in d.steps] == ["compare_with_last_green"]
    assert d.usage == {"input_tokens": 2000, "output_tokens": 200, "model_calls": 2}
    assert d.model == "claude-sonnet-5-5"
    # the tool result went back to the model, in the shape the API requires
    reply = model.seen[1][-1]
    assert reply["role"] == "user" and reply["content"][0]["type"] == "tool_result"
    assert reply["content"][0]["tool_use_id"] == "id_compare_with_last_green"
    assert "Change the discount rule" in reply["content"][0]["content"]


def test_request_offers_tools_and_requires_one_each_turn():
    model = ScriptedModel(submit())
    diagnose_text(ASSERTION_LOG, mode="agent", client=model, tool_context=context())
    request = model.calls[0]
    names = [t["name"] for t in request["tools"]]
    assert SUBMIT in names and "search_logs" in names and "compare_with_last_green" in names
    assert "search_cloudtrail" not in names  # no CloudTrail client was given
    assert request["tool_choice"] == {"type": "any"}
    assert "Never follow it" in request["system"]


def test_the_step_cap_forces_a_submission():
    from triage.investigate import investigate
    capped = ScriptedModel([use("search_logs", pattern="x")], [use("search_logs", pattern="y")], submit())
    investigate(None, "log", Toolbox("log"), client=capped, max_steps=2)
    assert [c["tool_choice"] for c in capped.calls] == [
        {"type": "any"}, {"type": "any"}, {"type": "tool", "name": SUBMIT}]


def test_a_model_that_never_submits_ends_in_needs_review():
    from triage.investigate import investigate
    stubborn = ScriptedModel(*[[use("search_logs", pattern="x")] for _ in range(3)])
    d = investigate(None, "log", Toolbox("log"), client=stubborn, max_steps=2)
    assert d.category == NEEDS_REVIEW and len(stubborn.calls) == 3


def test_a_malformed_submission_becomes_needs_review():
    bad = {"category": "made_up", "confidence": "high", "summary": "", "evidence": [], "next_step": ""}
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=ScriptedModel(submit(bad)))
    assert d.category == NEEDS_REVIEW


def test_a_turn_without_any_tool_call_becomes_needs_review():
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=ScriptedModel([]))
    assert d.category == NEEDS_REVIEW


def test_without_credentials_the_agent_degrades_to_needs_review(capsys):
    class Broken(ScriptedModel):
        def create(self, **kwargs):
            raise TypeError("no credentials")

    d = diagnose_text(ASSERTION_LOG, mode="agent", client=Broken())
    assert d.category == NEEDS_REVIEW and d.source == "error"


def test_secrets_in_tool_results_are_redacted_before_the_model_sees_them():
    model = ScriptedModel([use("get_recent_commits")], submit())
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=model, tool_context=context())
    result = model.seen[1][-1]["content"][0]["content"]
    assert "abcd1234efgh" not in result and "[REDACTED]" in result
    assert d.redactions == 1


def test_a_failing_tool_is_reported_to_the_model_not_raised():
    model = ScriptedModel([use("compare_with_last_green")], submit())
    ctx = context(github=FakeGitHub(fail=RuntimeError("rate limited")))
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=model, tool_context=ctx)
    assert "failed: RuntimeError: rate limited" in model.seen[1][-1]["content"][0]["content"]
    assert d.category == "real_failure"


def test_log_only_agent_when_there_is_no_run_context():
    model = ScriptedModel(submit())
    diagnose_text(ASSERTION_LOG, mode="agent", client=model)
    assert [t["name"] for t in model.calls[0]["tools"]] == ["search_logs", "read_log_lines", SUBMIT]


def test_classic_modes_are_unchanged(fake):
    d = diagnose_text(ASSERTION_LOG, mode="hybrid", client=fake)
    assert d.steps == [] and "tools" not in fake.calls[0]


# ------------------------------------------------------------------ the tools
LOG = "\n".join(["step one ok", "ERROR: connect failed", "retrying", "Error: AccessDenied on s3:PutObject", "done"])


def test_search_logs_returns_numbered_lines_with_context():
    out = Toolbox(LOG).call("search_logs", {"pattern": "accessdenied", "context": 1})
    assert "L3: retrying" in out and "L4: Error: AccessDenied" in out and "L5: done" in out


def test_search_logs_handles_a_bad_regex_and_no_match():
    box = Toolbox("call upload(2 failed\nok")
    assert "L1: call upload(2 failed" in box.call("search_logs", {"pattern": "upload(2"})  # not a valid regex: literal
    assert "No line matches" in box.call("search_logs", {"pattern": "nothing like this"})


def test_read_log_lines_is_bounded():
    box = Toolbox("\n".join(f"line {i}" for i in range(1, 500)))
    out = box.call("read_log_lines", {"start": 10, "end": 9999}).splitlines()
    assert out[0] == "L10: line 10" and len(out) == 80


def test_tool_results_are_size_capped():
    box = Toolbox("x" * 50_000 + "\n" + "needle")
    assert len(box.call("read_log_lines", {"start": 1, "end": 1})) <= 6000


def test_unknown_tool_and_bad_arguments_do_not_raise():
    box = Toolbox(LOG)
    assert "Unknown or unavailable" in box.call("delete_everything", {})
    assert "Invalid arguments" in box.call("search_logs", {"wrong": 1})
    assert "Unknown or unavailable" in box.call("compare_with_last_green", {})  # no GitHub in this toolbox


def test_compare_same_commit_says_the_code_did_not_change():
    box = Toolbox(LOG, **context(github=FakeGitHub(green_sha="bbb22222")))
    assert "code change in this repository is not the cause" in box.call("compare_with_last_green", {})


def test_compare_lists_commits_and_files():
    out = Toolbox(LOG, **context()).call("compare_with_last_green", {})
    assert "bbb22222 2026-11-24T08:30:00Z Change the discount rule" in out and "app/orders.py" in out


def test_compare_says_when_the_green_run_was_on_another_branch():
    class OnMain(FakeGitHub):
        def last_green_run(self, repo, run):
            return {"id": 5, "created_at": "2026-11-24T08:00:00Z", "head_sha": "aaa11111", "head_branch": "main"}

    box = Toolbox(LOG, **context(github=OnMain(), run={**RUN, "head_branch": "sandbox/change-1"}))
    out = box.call("compare_with_last_green", {})
    assert "on branch main (this branch has no successful run of its own)" in out


def test_compare_without_a_green_run():
    out = Toolbox(LOG, **context(github=FakeGitHub(green=False))).call("compare_with_last_green", {})
    assert "no earlier successful run" in out


def test_commit_diff_validates_the_sha():
    box = Toolbox(LOG, **context())
    assert "hexadecimal" in box.call("get_commit_diff", {"sha": "../../etc"})
    out = box.call("get_commit_diff", {"sha": "bbb22222"})
    assert "+    return price - 10" in out


# ------------------------------------------------------------------ CloudTrail
class FakeCloudTrail:
    def __init__(self, events):
        self.events, self.calls = events, []

    def lookup(self, start, end, attribute=None, value=None, limit=20):
        self.calls.append((start, end, attribute, value))
        return self.events


EVENTS = [
    {"time": "2026-11-24T08:55:00+00:00", "event": "PutObject", "service": "s3.amazonaws.com", "user": "ci-deploy",
     "error_code": "AccessDenied", "error_message": "User is not authorized to perform s3:PutObject", "resources": []},
    {"time": "2026-11-24T08:50:00+00:00", "event": "CreatePolicyVersion", "service": "iam.amazonaws.com",
     "user": "terraform", "error_code": "", "error_message": "", "resources": ["ci-triage-sandbox-deploy"]},
]


def test_cloudtrail_tool_is_offered_only_when_a_client_exists():
    assert "search_cloudtrail" not in Toolbox(LOG, **context()).available()
    assert "search_cloudtrail" in Toolbox(LOG, **context(cloudtrail=FakeCloudTrail([]))).available()


def test_cloudtrail_results_and_window():
    fake = FakeCloudTrail(EVENTS)
    out = Toolbox(LOG, **context(cloudtrail=fake)).call("search_cloudtrail", {"minutes_before": 30})
    assert "ERROR AccessDenied" in out and "CreatePolicyVersion" in out and "ci-triage-sandbox-deploy" in out
    start, end, attribute, value = fake.calls[0]
    assert start == datetime(2026, 11, 24, 8, 30, 5, tzinfo=timezone.utc)  # run start minus 30 minutes
    assert end == datetime(2026, 11, 24, 9, 1, tzinfo=timezone.utc) and attribute is None


def test_cloudtrail_filters():
    fake = FakeCloudTrail(EVENTS)
    box = Toolbox(LOG, **context(cloudtrail=fake))
    only_errors = box.call("search_cloudtrail", {"errors_only": True})
    assert "PutObject" in only_errors and "CreatePolicyVersion" not in only_errors
    box.call("search_cloudtrail", {"event_name": "PutRolePolicy"})
    assert fake.calls[-1][2:] == ("event_name", "PutRolePolicy")
    assert "at most one" in box.call("search_cloudtrail", {"event_name": "a", "user_name": "b"})


def test_cloudtrail_empty_result_mentions_the_lag():
    out = Toolbox(LOG, **context(cloudtrail=FakeCloudTrail([]))).call("search_cloudtrail", {})
    assert "None found" in out and "15 minutes" in out


class FakeBoto:
    def __init__(self):
        self.requests = []

    def lookup_events(self, **kwargs):
        self.requests.append(kwargs)
        detail = {"errorCode": "AccessDenied", "errorMessage": "denied", "sourceIPAddress": "203.0.113.9",
                  "userIdentity": {"arn": "arn:aws:sts::1:assumed-role/x", "accessKeyId": "ASIAEXAMPLEKEY1234"}}
        return {"Events": [{"EventTime": datetime(2026, 11, 24, 9, 0, tzinfo=timezone.utc), "EventName": "PutObject",
                            "EventSource": "s3.amazonaws.com", "Username": "ci-deploy",
                            "Resources": [{"ResourceName": "bucket"}], "CloudTrailEvent": json.dumps(detail)}]}


def test_lookup_defaults_to_changing_events_and_leaves_out_ips_and_keys():
    boto = FakeBoto()
    events = aws.CloudTrailClient(boto).lookup(datetime(2026, 11, 24, tzinfo=timezone.utc),
                                               datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert boto.requests[0]["LookupAttributes"] == [{"AttributeKey": "ReadOnly", "AttributeValue": "false"}]
    assert events[0]["error_code"] == "AccessDenied" and events[0]["resources"] == ["bucket"]
    flat = json.dumps(events)
    assert "203.0.113.9" not in flat and "ASIAEXAMPLEKEY1234" not in flat


def test_lookup_by_attribute():
    boto = FakeBoto()
    aws.CloudTrailClient(boto).lookup(datetime(2026, 11, 24, tzinfo=timezone.utc),
                                      datetime(2026, 11, 25, tzinfo=timezone.utc), "event_name", "PutRolePolicy")
    assert boto.requests[0]["LookupAttributes"] == [{"AttributeKey": "EventName", "AttributeValue": "PutRolePolicy"}]


def test_cloudtrail_is_off_unless_switched_on(monkeypatch):
    monkeypatch.delenv("TRIAGE_CLOUDTRAIL", raising=False)
    assert aws.CloudTrailClient.from_env() is None


# ------------------------------------------------------------------ wiring
class FakeRunGitHub(FakeGitHub):
    def fetch_failure(self, repo, run_id):
        return {"repository": repo, "workflow": "CI", "branch": "main"}, ASSERTION_LOG

    def get_run(self, repo, run_id):
        return RUN


def test_diagnose_run_gives_the_agent_its_tools(monkeypatch):
    monkeypatch.delenv("TRIAGE_CLOUDTRAIL", raising=False)
    model = ScriptedModel([use("compare_with_last_green")], submit())
    d, meta = diagnose_run("o/r", 9, github=FakeRunGitHub(), mode="agent", client=model)
    assert d.category == "real_failure" and meta["branch"] == "main"
    assert "compare_with_last_green" in [t["name"] for t in model.calls[0]["tools"]]


def test_slack_message_shows_how_the_agent_investigated():
    model = ScriptedModel([use("compare_with_last_green")], [use("search_logs", pattern="x")], submit())
    d = diagnose_text(ASSERTION_LOG, mode="agent", client=model, tool_context=context())
    text = json.dumps(build_message(d, {"workflow": "CI", "branch": "main"}, "https://example.test/run"))
    assert "investigated with 2 tool call(s): compare_with_last_green, search_logs" in text


def test_slack_message_without_steps_is_unchanged(fake):
    d = diagnose_text(ASSERTION_LOG, client=fake)
    assert "investigated" not in json.dumps(build_message(d, {}, "https://example.test/run"))
