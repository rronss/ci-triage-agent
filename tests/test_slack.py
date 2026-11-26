import json
from types import SimpleNamespace

import pytest

from triage import cli, slack
from triage.models import Diagnosis

RUN_URL = "https://github.com/o/r/actions/runs/7"


def diagnosis(**overrides):
    fields = dict(category="flaky_test", confidence="medium", summary="A timing-dependent test timed out.",
                  evidence=["E   TimeoutError: delivery not acknowledged within 0.25s"],
                  next_step="Re-run the job.", source="llm")
    return Diagnosis(**{**fields, **overrides})


def all_text(payload):
    return json.dumps(payload, ensure_ascii=False)


def top(payload):
    return payload["blocks"]


def card(payload):
    return payload["attachments"][0]["blocks"]


def texts(blocks):
    """Every text string in a list of blocks, sections and context lines alike."""
    return [b["text"]["text"] if "text" in b else b["elements"][0]["text"] for b in blocks]


def test_message_carries_the_diagnosis_and_links_to_the_run():
    payload = slack.build_message(diagnosis(), {"workflow": "CI", "branch": "main", "run_attempt": 1}, RUN_URL)
    title, where, summary, next_step = texts(top(payload))
    assert title == "*Flaky test*  ·  Medium confidence"
    assert where == f"<https://github.com/o/r|o/r>  ·  CI  ·  `main`  ·  <{RUN_URL}|View run>"
    assert summary == "A timing-dependent test timed out." and next_step == "*Next step*\nRe-run the job."
    evidence, source = texts(card(payload))
    assert evidence.startswith("```E   TimeoutError") and source.endswith("Check against the log before acting")
    assert payload["text"] == "CI failure in o/r: Flaky test (medium confidence)"


def test_message_is_minimal_and_quiet():
    payload = slack.build_message(diagnosis(), {"repository": "acme/shop"}, RUN_URL)
    assert [b["type"] for b in top(payload)] == ["section", "context", "section", "section"]
    assert not any(b["type"] in ("header", "actions", "divider") for b in top(payload) + card(payload))
    assert not any(ord(ch) > 0x2100 for ch in all_text(payload)) and ":" not in texts(top(payload))[0]  # no emoji
    assert payload["unfurl_links"] is False and payload["unfurl_media"] is False


def test_the_repository_comes_from_the_metadata():
    payload = slack.build_message(diagnosis(), {"repository": "acme/shop", "workflow": "ci", "branch": "dev"}, RUN_URL)
    assert texts(top(payload))[1].startswith("<https://github.com/acme/shop|acme/shop>  ·  ci  ·  `dev`")
    assert payload["text"].startswith("CI failure in acme/shop:")


def test_only_a_re_run_shows_its_attempt():
    assert "attempt" not in texts(top(slack.build_message(diagnosis(), {"run_attempt": 1}, RUN_URL)))[1]
    assert "attempt 3" in texts(top(slack.build_message(diagnosis(), {"run_attempt": 3}, RUN_URL)))[1]


def test_log_derived_text_cannot_create_mentions_or_links():
    hostile = "<!channel> <https://evil.example|click me> & more"
    payload = slack.build_message(
        diagnosis(summary=hostile, evidence=[hostile], next_step=hostile),
        {"workflow": hostile, "branch": hostile, "run_attempt": hostile},
        RUN_URL,
    )
    text = all_text(payload)
    assert "<!channel>" not in text and "<https://evil.example" not in text
    assert "&lt;!channel&gt;" in text and "&amp; more" in text


def test_a_hostile_repository_name_cannot_inject_a_link():
    text = all_text(slack.build_message(diagnosis(), {"repository": "a/b|click> <!channel>"}, RUN_URL))
    assert "<!channel>" not in text and "a/b|click>" not in text


def test_evidence_cannot_break_out_of_the_code_block():
    payload = slack.build_message(diagnosis(evidence=["``` *injected* ```"]), {}, RUN_URL)
    assert texts(card(payload))[0].count("```") == 2


def test_long_text_is_clipped_under_slacks_limit():
    long = "x" * 10_000
    payload = slack.build_message(diagnosis(summary=long, evidence=[long], next_step=long),
                                  {"workflow": long, "branch": long}, RUN_URL)
    assert all(len(t) <= 3000 for t in texts(top(payload) + card(payload)))
    evidence = texts(card(payload))[0]
    assert evidence.endswith("```") and evidence.count("```") == 2  # clipping keeps the code block closed


def test_missing_meta_and_evidence_still_render():
    payload = slack.build_message(diagnosis(evidence=[]), None, RUN_URL)
    assert len(top(payload)) == 4 and len(card(payload)) == 1


def test_every_category_has_its_own_colour_bar():
    colours = {slack.build_message(diagnosis(category=c), {}, RUN_URL)["attachments"][0]["color"] for c in slack.LABELS}
    assert len(colours) == len(slack.LABELS) == len(slack.COLORS)


def test_the_essentials_are_never_inside_the_foldable_card():
    payload = slack.build_message(diagnosis(), {"repository": "acme/shop"}, RUN_URL)
    above, inside = all_text(top(payload)), all_text(card(payload))
    for essential in ("Flaky test", "acme/shop", "A timing-dependent test timed out.", "Re-run the job.", RUN_URL):
        assert essential in above and essential not in inside
    assert "TimeoutError" in inside


class FakeWebhook:
    def __init__(self, status=200, body="ok"):
        self.calls = []
        self.status, self.body = status, body

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return SimpleNamespace(status_code=self.status, text=self.body)


def test_post_sends_json_to_the_webhook():
    hook = FakeWebhook()
    slack.post("https://hooks.slack.com/services/T/B/x", {"text": "hi"}, session=hook)
    assert hook.calls == [("https://hooks.slack.com/services/T/B/x", {"text": "hi"})]


def test_post_raises_when_slack_rejects():
    with pytest.raises(RuntimeError, match="invalid_payload"):
        slack.post("https://hooks.slack.com/x", {}, session=FakeWebhook(400, "invalid_payload"))


@pytest.fixture
def notify_env(monkeypatch):
    meta = {"workflow": "CI", "branch": "main", "run_attempt": 1}
    monkeypatch.setattr(cli, "diagnose_run", lambda repo, run_id, **kw: (diagnosis(), meta))
    posted = []
    monkeypatch.setattr(cli, "post", lambda url, payload: posted.append((url, payload)))
    return posted


def test_notify_posts_when_a_webhook_is_configured(notify_env, monkeypatch, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    assert cli.main(["notify", "--repo", "o/r", "--run-id", "7"]) == 0
    url, payload = notify_env[0]
    assert url.startswith("https://hooks.slack.com/") and RUN_URL in json.dumps(payload["blocks"])
    assert "Posted diagnosis" in capsys.readouterr().out


def test_notify_dry_run_prints_and_never_posts(notify_env, monkeypatch, capsys):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    assert cli.main(["notify", "--repo", "o/r", "--run-id", "7", "--dry-run"]) == 0
    assert notify_env == [] and '"attachments"' in capsys.readouterr().out


def test_notify_prints_and_saves_the_full_record(notify_env, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")
    out = tmp_path / "diagnosis.json"
    assert cli.main(["notify", "--repo", "o/r", "--run-id", "7", "--out", str(out)]) == 0
    record = json.loads(out.read_text())
    assert record["run_url"] == RUN_URL and record["meta"]["branch"] == "main"
    assert record["diagnosis"]["category"] == "flaky_test"
    assert record["diagnosis"]["next_step"] == "Re-run the job."
    assert '"diagnosis"' in capsys.readouterr().out


def test_record_is_saved_even_when_slack_fails(notify_env, monkeypatch, tmp_path):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/x")

    def reject(url, payload):
        raise RuntimeError("Slack rejected the message")

    monkeypatch.setattr(cli, "post", reject)
    out = tmp_path / "diagnosis.json"
    with pytest.raises(RuntimeError):
        cli.main(["notify", "--repo", "o/r", "--run-id", "7", "--out", str(out)])
    assert json.loads(out.read_text())["diagnosis"]["category"] == "flaky_test"


@pytest.mark.parametrize("unset", ["missing", "empty"])
def test_notify_without_a_webhook_fails_before_diagnosing(notify_env, monkeypatch, capsys, unset):
    # GitHub passes an empty string when a referenced secret does not exist.
    if unset == "missing":
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    else:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "")

    def must_not_run(*args, **kwargs):
        raise AssertionError("diagnosed a run that could never be posted")

    monkeypatch.setattr(cli, "diagnose_run", must_not_run)
    assert cli.main(["notify", "--repo", "o/r", "--run-id", "7"]) == 1
    assert notify_env == [] and "SLACK_WEBHOOK_URL is not set" in capsys.readouterr().err


def test_dry_run_works_without_a_webhook(notify_env, monkeypatch, capsys):
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    assert cli.main(["notify", "--repo", "o/r", "--run-id", "7", "--dry-run"]) == 0
    assert notify_env == [] and '"attachments"' in capsys.readouterr().out
