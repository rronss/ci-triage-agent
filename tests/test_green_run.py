"""Finding the last green run: same branch first, then the default branch."""
from types import SimpleNamespace

from triage.github import GitHubClient

RUN = {"id": 50, "workflow_id": 1, "head_branch": "sandbox/change-1", "created_at": "2026-11-24T10:00:00Z"}


class Session:
    def __init__(self, by_branch, default_branch="main"):
        self.headers, self.by_branch, self.default_branch, self.requested = {}, by_branch, default_branch, []

    def get(self, url, params=None, timeout=None):
        path = url.replace("https://api.github.com", "")
        self.requested.append((path, (params or {}).get("branch")))
        if path == "/repos/o/r":
            body = {"default_branch": self.default_branch}
        else:
            body = {"workflow_runs": self.by_branch.get(params["branch"], [])}
        return SimpleNamespace(json=lambda: body, text="", raise_for_status=lambda: None)


def client(by_branch, **kwargs):
    return GitHubClient(token="t", session=Session(by_branch, **kwargs))


def test_prefers_a_green_run_on_the_same_branch():
    gh = client({"sandbox/change-1": [{"id": 40, "created_at": "2026-11-24T09:00:00Z"}],
                 "main": [{"id": 30, "created_at": "2026-11-24T08:00:00Z"}]})
    assert gh.last_green_run("o/r", RUN)["id"] == 40


def test_falls_back_to_the_default_branch_for_a_new_branch():
    gh = client({"main": [{"id": 30, "created_at": "2026-11-24T08:00:00Z", "head_branch": "main"}]})
    assert gh.last_green_run("o/r", RUN)["id"] == 30
    assert [b for _, b in gh.session.requested if b] == ["sandbox/change-1", "main"]


def test_ignores_runs_created_after_the_failing_run_and_the_run_itself():
    gh = client({"main": [{"id": 60, "created_at": "2026-11-24T11:00:00Z"}, {"id": 50, "created_at": "2026-11-24T10:00:00Z"}]})
    assert gh.last_green_run("o/r", RUN) is None


def test_on_the_default_branch_there_is_no_second_lookup():
    run = {**RUN, "head_branch": "main"}
    gh = client({})
    assert gh.last_green_run("o/r", run) is None
    assert [b for _, b in gh.session.requested if b] == ["main"]
