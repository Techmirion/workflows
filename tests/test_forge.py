"""GitHubForge without the network: what it does with GitHub's answers."""
import pytest

from tmflow.forge import ForgeError, GitHubForge


@pytest.fixture
def forge():
    return GitHubForge("Techmirion/sandbox", token="t")


def answer(forge, monkeypatch, error):
    def _req(method, path, data=None, **kw):
        if error:
            raise ForgeError(f"{method} {path}: {error}")
    monkeypatch.setattr(forge, "_req", _req)


def test_delete_branch_deletes(forge, monkeypatch):
    answer(forge, monkeypatch, None)
    assert forge.delete_branch("fix/1-x") is True


@pytest.mark.parametrize("error", [
    'HTTP 422 {"message":"Reference does not exist","status":"422"}',  # Dependabot deleted it first
    'HTTP 404 {"message":"Not Found"}',
])
def test_delete_branch_already_gone_is_not_an_error(forge, monkeypatch, error):
    answer(forge, monkeypatch, error)
    assert forge.delete_branch("dependabot/npm_and_yarn/dev/npm-1") is False


@pytest.mark.parametrize("error", [
    'HTTP 422 {"message":"Cannot delete this protected branch"}',
    'HTTP 403 {"message":"Resource not accessible by integration"}',
])
def test_delete_branch_other_errors_still_fail(forge, monkeypatch, error):
    answer(forge, monkeypatch, error)
    with pytest.raises(ForgeError):
        forge.delete_branch("fix/1-x")


def test_cleanup_when_the_branch_vanishes_before_the_delete(monkeypatch, capsys):
    """Dependabot deletes its own branch on merge; the cleanup job saw it a moment
    earlier, so its delete gets 422. That is the outcome we wanted: exit 0."""
    from types import SimpleNamespace

    from tmflow import cli

    head, sha = "dependabot/npm_and_yarn/dev/npm-1", "a" * 40

    class Gone:
        def get_pr(self, n):
            return {"number": n, "merged": True, "head": head, "head_repo": "T/r", "base_repo": "T/r",
                    "head_sha": sha}

        def default_branch(self):
            return "main"

        def branch_sha(self, branch):
            return sha  # still there when checked

        def open_prs(self, head=None):
            return []

        def delete_branch(self, branch):
            return False  # gone by the time of the delete

    monkeypatch.setattr(cli, "_forge", lambda a: Gone())
    a = SimpleNamespace(event=None, pr=43, protected="dev main", dry_run=False)
    assert cli.cmd_cleanup(a) == 0
    assert f"keep `{head}`: `{head}` was already gone when the delete ran" in capsys.readouterr().out
