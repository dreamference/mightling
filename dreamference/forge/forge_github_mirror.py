"""The forge's outbound mirror to GitHub (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §3.2, §5.3, §5.4).

The forge is the origin and GitHub its public copy. Forgejo's own push mirror pushes with
`--mirror`, which deletes on GitHub what the forge deleted, so it is off (`DISABLE_NEW_PUSH`) and
this job does the mirroring instead, on the forge of record's host: a systemd path unit runs it
seconds after a push (the repository's post-receive hook touches a file) and a timer every ten
minutes. For each repository with a GitHub destination it:

1. refuses to send anything from a repository that is private or on the never-mirror list, and
   stops the whole run when it cannot read a repository's visibility (fail closed);
2. fetches the forge's branches and tags and GitHub's into a work clone;
3. re-runs the anonymity check on every commit GitHub does not have yet (the second layer) and
   stops the run on any finding;
4. pushes each branch that is new or ahead on the forge, without `--force` and without
   `--prune`, so nothing is ever deleted or rewritten on GitHub;
5. brings back into the forge what exists only on GitHub or is ahead there (release tags made by
   the release workflow, a branch pushed to GitHub directly), so the two never diverge silently;
   a branch that diverged both ways is reported, never forced.

GitHub's credentials are the `gh` login of the configured account, asked for at run time
(`gh auth token --user <account>`) through git's credential helper: no token is copied into a
file, a URL or a log. At `/airgapped on` the job sends nothing and says so.
"""

import fcntl
import json
import os
import re
import subprocess
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_anonymity import ForgeAnonymity
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_settings import ROLE_RECORD, ForgeSettings

STATUS_NAME: Final[str] = "github-mirror-status.json"
SETTINGS_NAME: Final[str] = "github-mirror.json"
GITHUB_PREFIX: Final[str] = "https://github.com/"
# The value of `mightling_airgapped` that stops every outbound mirror.
AIRGAPPED_ON: Final[str] = "on"
# What GitHub's stderr says when it is throttling the account: the run backs off to the next tick.
RATE_LIMIT_MARKERS: Final[tuple[str, ...]] = (
    "rate limit",
    "abuse detection",
    "too many requests",
    "error: 429",
    "returned error: 403",
)
# A 403 that refuses the token or its rights is not throttling: it fails the branch, so a token
# without write access is reported instead of retried every tick.
REFUSAL_MARKERS: Final[tuple[str, ...]] = ("denied", "not granted")
# The forge's anonymity hook quotes the offending line; only `added:<line>:<file>` is kept.
ANONYMITY_REFUSAL: Final[str] = "anonymity"
HOOK_QUOTE: Final[re.Pattern[str]] = re.compile(r"(added:\d+:\S*?):\s.*")


class ForgeGithubMirror:
    """Runs and reports the GitHub mirror."""

    @classmethod
    def settings(cls) -> dict[str, Any]:
        """Read the GitHub mirror's settings.

        Returns:
            dict[str, Any]: `enabled` (the `--all-branches` switch) and `account` (the `gh`
            account whose login pushes).
        """
        try:
            data = json.loads(ForgeSettings.secret_path(SETTINGS_NAME).read_text())
        except (OSError, ValueError):
            data = {}
        return {"enabled": bool(data.get("enabled")), "account": data.get("account") or ""}

    @classmethod
    def save_settings(cls, enabled: bool, account: str) -> None:
        """Save the GitHub mirror's settings.

        Args:
            enabled: Whether the mirror runs.
            account: The `gh` account that pushes.
        """
        ForgeSettings.write_secret(
            SETTINGS_NAME, json.dumps({"enabled": enabled, "account": account})
        )

    @classmethod
    def github_environment(cls) -> dict[str, str]:
        """Return this environment with the `gh` account to ask named, and git never prompting.

        Returns:
            dict[str, str]: This environment with the `gh` account to ask named, and git never
            prompting.
        """
        environment = dict(os.environ)
        environment["MIGHTLING_GH_ACCOUNT"] = cls.settings()["account"]
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment

    @classmethod
    def github_credential_arguments(cls) -> list[str]:
        """Return the `git -c` arguments that let git push to GitHub with the `gh` login.

        Returns:
            list[str]: `git -c` arguments whose credential helper asks `gh` for the configured
            account's token at the moment git needs it.
        """
        helper = (
            '!f() { test "$1" = get || exit 0; echo username=x-access-token; '
            'if [ -n "$MIGHTLING_GH_ACCOUNT" ]; then t=$(gh auth token --user "$MIGHTLING_GH_ACCOUNT"); '
            'else t=$(gh auth token); fi; echo "password=$t"; }; f'
        )
        return [
            "-c",
            "credential.https://github.com.helper=",
            "-c",
            f"credential.https://github.com.helper={helper}",
        ]

    @classmethod
    def airgapped(cls) -> bool:
        """Tell whether the user's `/airgapped` default is `on`.

        Returns:
            bool: Whether the level for a command with no session is `on`, which stops every
            outbound mirror (§5.4): as the one reader resolves it
            (`DreamferenceConfig.resolve_airgapped_level`, specs/DREAMFERENCE_MIGHTLING_AIRGAPPED.md
            §6.5), and `on` whenever `ling` cannot answer.
        """
        from dreamference.config.dreamference_config import DreamferenceConfig

        return DreamferenceConfig.resolve_airgapped_level() == AIRGAPPED_ON

    @classmethod
    def destinations(cls) -> list[str]:
        """List each configured GitHub destination, for the egress audit and `forge status`.

        Returns:
            list[str]: Each configured destination, for the egress audit and `forge status`.
        """
        return sorted(
            {
                entry.get("github")
                for entry in ForgeRepositories.config()["repositories"].values()
                if entry.get("github")
            }
        )

    @classmethod
    def refs(cls, work: Path, prefix: str) -> dict[str, str]:
        """Read the refs below a prefix in the work clone.

        Args:
            work: The work clone.
            prefix: A ref namespace.

        Returns:
            dict[str, str]: Ref name below the prefix → commit (tags peeled to their object).
        """
        result = ForgeRepositories.git(
            work, ["for-each-ref", "--format=%(refname) %(objectname)", prefix]
        )
        found = {}
        for line in result.stdout.splitlines():
            name, _, sha = line.partition(" ")
            found[name[len(prefix) :]] = sha
        return found

    @classmethod
    def is_ancestor(cls, work: Path, older: str, newer: str) -> bool:
        """Tell whether one commit is an ancestor of another.

        Args:
            work: The work clone.
            older: A commit.
            newer: Another commit.

        Returns:
            bool: Whether `older` is an ancestor of (or equal to) `newer`.
        """
        return (
            ForgeRepositories.git(work, ["merge-base", "--is-ancestor", older, newer]).returncode
            == 0
        )

    @classmethod
    def plan(cls, work: Path) -> dict[str, list[str]]:
        """Compare the forge's refs with GitHub's in the work clone.

        Args:
            work: The work clone, fetched (`refs/forge/…`, `refs/github/…`).

        Returns:
            dict[str, list[str]]: `to_github` and `to_forge` (refspecs) and `diverged` (refs).
        """
        forge = {
            **{f"heads/{k}": v for k, v in cls.refs(work, "refs/forge/heads/").items()},
            **{f"tags/{k}": v for k, v in cls.refs(work, "refs/forge/tags/").items()},
        }
        github = {
            **{f"heads/{k}": v for k, v in cls.refs(work, "refs/github/heads/").items()},
            **{f"tags/{k}": v for k, v in cls.refs(work, "refs/github/tags/").items()},
        }
        plan: dict[str, list[str]] = {"to_github": [], "to_forge": [], "diverged": []}
        for name in sorted(set(forge) | set(github)):
            mine, theirs = forge.get(name), github.get(name)
            if mine == theirs:
                continue
            if theirs is None or (
                mine and name.startswith("heads/") and cls.is_ancestor(work, theirs, mine)
            ):
                plan["to_github"].append(f"refs/forge/{name}:refs/{name}")
            elif mine is None or (
                name.startswith("heads/") and cls.is_ancestor(work, mine, theirs)
            ):
                plan["to_forge"].append(f"refs/github/{name}:refs/{name}")
            else:
                plan["diverged"].append(f"refs/{name}")
        return plan

    @classmethod
    def mirror_one(cls, full_name: str, destination: str, token: str) -> dict[str, Any]:
        """Mirror one repository.

        Args:
            full_name: `owner/name` on the forge.
            destination: The GitHub repository's URL.
            token: `forge-admin`'s token.

        Returns:
            dict[str, Any]: `ok`, `sent`, `received`, `diverged` and `error`.
        """
        settings = ForgeRepositories.settings(full_name)
        work = ForgeRepositories.work_dir(full_name)
        if not work.is_dir():
            work.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(work)], check=False)
        forge_environment = ForgeRepositories.git_environment(token)
        github_environment = cls.github_environment()
        error = cls.fetch(work, full_name, destination, forge_environment, github_environment)
        if error:
            return {"ok": False, "error": error}
        plan = cls.plan(work)
        failures: dict[str, str] = {}
        held: dict[str, str] = {}
        sent = received = 0
        backoff = ""
        if plan["to_github"]:
            outcome = cls.send_to_github(
                work, plan["to_github"], destination, settings, github_environment
            )
            if outcome.get("stop"):
                return outcome
            sent, failures = outcome["pushed"], outcome["failures"]
            backoff = outcome["backoff"]
        if plan["to_forge"]:
            outcome = cls.bring_back(work, full_name, plan["to_forge"], forge_environment)
            received = outcome["pushed"]
            for ref, cause in outcome["failures"].items():
                # The forge's own anonymity check refusing what GitHub already has is the owner's
                # to resolve (nothing is forced): reported, but it does not fail the run.
                (held if ANONYMITY_REFUSAL in cause else failures)[ref] = cause
        for ref in plan["diverged"]:
            held[ref] = "diverged both ways: never forced"
        return {
            "ok": not failures,
            "sent": sent,
            "received": received,
            "diverged": plan["diverged"][:20],
            "failures": failures,
            "held": held,
            "backoff": backoff,
            "error": "; ".join(f"{ref}: {cause}" for ref, cause in failures.items())[:600],
        }

    @classmethod
    def push_cause(cls, stderr: str) -> str:
        """Boil git's push output down to a one-line cause.

        Args:
            stderr: What `git push` wrote.

        Returns:
            str: The remote's own lines (the anonymity hook's quote cut to `added:<line>:<file>`),
            else git's last `error:`/`fatal:` line, else the output's tail; never an identifier.
        """
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        remote = [
            HOOK_QUOTE.sub(r"\1", line[len("remote:") :].strip())
            for line in lines
            if line.startswith("remote:")
        ]
        remote = [line for line in remote if line]
        if remote:
            return " ".join(remote)[:300]
        errors = [line for line in lines if line.startswith(("error:", "fatal:"))]
        return (errors[0] if errors else (lines[-1] if lines else "git push failed"))[:300]

    @classmethod
    def push_each(
        cls, work: Path, arguments: list[str], refspecs: list[str], environment: dict[str, str]
    ) -> dict[str, Any]:
        """Push the refspecs one at a time, so one refused branch holds back no other.

        Args:
            work: The work clone.
            arguments: `git` arguments up to and including the destination.
            refspecs: The refspecs to push.
            environment: git's environment.

        Returns:
            dict[str, Any]: `pushed` (a count), `failures` (branch → cause) and `backoff` (the
            cause when the destination is throttling: the rest is left for the next tick).
        """
        pushed = 0
        failures: dict[str, str] = {}
        for spec in refspecs:
            result = ForgeRepositories.git(work, [*arguments, spec], environment)
            if result.returncode == 0:
                pushed += 1
                continue
            stderr = result.stderr.lower()
            if any(marker in stderr for marker in RATE_LIMIT_MARKERS) and not any(
                marker in stderr for marker in REFUSAL_MARKERS
            ):
                return {
                    "pushed": pushed,
                    "failures": failures,
                    "backoff": cls.push_cause(result.stderr),
                }
            failures[spec.rsplit(":", 1)[-1]] = cls.push_cause(result.stderr)
        return {"pushed": pushed, "failures": failures, "backoff": ""}

    @classmethod
    def fetch(
        cls,
        work: Path,
        full_name: str,
        destination: str,
        forge_environment: dict[str, str],
        github_environment: dict[str, str],
    ) -> str | None:
        """Fetch the forge's refs and then GitHub's into the work clone.

        Args:
            work: The work clone.
            full_name: `owner/name` on the forge.
            destination: The GitHub repository's URL.
            forge_environment: git's environment for the forge.
            github_environment: git's environment for GitHub.

        Returns:
            str | None: Why a fetch failed, or None when both worked.
        """
        fetched = ForgeRepositories.git(
            work,
            [
                *ForgeRepositories.forge_credential_arguments(),
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                ForgeRepositories.forge_url(full_name),
                "+refs/heads/*:refs/forge/heads/*",
                "+refs/tags/*:refs/forge/tags/*",
            ],
            forge_environment,
        )
        if fetched.returncode != 0:
            return f"fetch from the forge failed: {fetched.stderr.strip()[-300:]}"
        fetched = ForgeRepositories.git(
            work,
            [
                *cls.github_credential_arguments(),
                "fetch",
                "--quiet",
                "--prune",
                "--no-tags",
                destination,
                "+refs/heads/*:refs/github/heads/*",
                "+refs/tags/*:refs/github/tags/*",
            ],
            github_environment,
        )
        if fetched.returncode != 0:
            return f"fetch from GitHub failed: {fetched.stderr.strip()[-300:]}"
        return None

    @classmethod
    def send_to_github(
        cls,
        work: Path,
        refspecs: list[str],
        destination: str,
        settings: dict[str, Any],
        environment: dict[str, str],
    ) -> dict[str, Any]:
        """Re-check the commits GitHub does not have yet, then push them, never forcing.

        Args:
            work: The work clone.
            refspecs: The plan's `to_github` refspecs.
            destination: The GitHub repository's URL.
            settings: The repository's settings (`scope`, `exempt`).
            environment: git's environment for GitHub.

        Returns:
            dict[str, Any]: `push_each`'s result, or a failed result with `stop` set when the
            anonymity re-check found something or could not run (the whole run stops).
        """
        tips = [spec.split(":", 1)[0] for spec in refspecs]
        known = list(cls.refs(work, "refs/github/").values())
        try:
            text = ForgeAnonymity.added_text(work, tips, known, settings["exempt"])
        except RuntimeError as error:
            return {
                "ok": False,
                "stop": True,
                "error": f"the anonymity re-check could not run: {error}",
            }
        hits = ForgeAnonymity.findings(
            text, ForgeAnonymity.patterns(settings["scope"], ForgeAnonymity.read_denylist())
        )
        if hits:
            return {
                "ok": False,
                "stop": True,
                "error": f"the anonymity re-check found {len(hits)} identifiers in commits about to "
                "be sent; nothing was pushed",
            }
        return cls.push_each(
            work,
            [*cls.github_credential_arguments(), "push", "--quiet", "--no-verify", destination],
            refspecs,
            environment,
        )

    @classmethod
    def bring_back(
        cls, work: Path, full_name: str, refspecs: list[str], environment: dict[str, str]
    ) -> dict[str, Any]:
        """Push into the forge what exists only on GitHub or is ahead there, one ref at a time.

        Args:
            work: The work clone.
            full_name: `owner/name` on the forge.
            refspecs: The plan's `to_forge` refspecs.
            environment: git's environment for the forge.

        Returns:
            dict[str, Any]: `push_each`'s result.
        """
        return cls.push_each(
            work,
            [
                *ForgeRepositories.forge_credential_arguments(),
                "push",
                "--quiet",
                ForgeRepositories.forge_url(full_name),
            ],
            refspecs,
            environment,
        )

    @classmethod
    def run(cls) -> int:
        """Run the mirror once over every repository with a GitHub destination.

        Returns:
            int: 0 when everything was mirrored, 1 when a repository failed, 2 when the run was
            stopped (a visibility it could not read, an anonymity finding, `/airgapped on`).
        """
        if ForgeSettings.role() != ROLE_RECORD or not cls.settings()["enabled"]:
            return 0
        lock_path = ForgeSettings.share_dir() / "github-work" / ".lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with Path(lock_path).open("w") as lock:
            # The path unit and the timer may both start a run; the second waits for the first.
            fcntl.flock(lock, fcntl.LOCK_EX)
            return cls.run_locked()

    @classmethod
    def run_locked(cls) -> int:
        """Do `run`'s work while holding the work folder's lock.

        Returns:
            int: See `run`.
        """
        if cls.airgapped():
            cls.write_status({"stopped": "/airgapped is on: no outbound mirror runs"})
            print("⚠️  GitHub mirror: /airgapped is on: no outbound mirror runs")
            return 2
        token = ForgeAccounts.admin_token()
        try:
            repositories = {repo["full_name"]: repo for repo in ForgeRepositories.all(token)}
        except RuntimeError as error:
            cls.write_status({"stopped": f"could not read the repositories' visibility: {error}"})
            print(f"❌ GitHub mirror stopped: could not read the repositories' visibility: {error}")
            return 2
        results: dict[str, Any] = {}
        code = 0
        for full_name, entry in sorted(ForgeRepositories.config()["repositories"].items()):
            destination = entry.get("github")
            if not destination:
                continue
            repository = repositories.get(full_name)
            if repository is None or ForgeRepositories.never_mirrored(repository):
                results[full_name] = {
                    "ok": False,
                    "error": "private, never mirrored, or not on the forge: skipped",
                }
                cls.report(full_name, results[full_name])
                continue
            if not str(destination).startswith(GITHUB_PREFIX):
                results[full_name] = {"ok": False, "error": "not a GitHub repository URL: skipped"}
                cls.report(full_name, results[full_name])
                continue
            result = cls.mirror_one(full_name, destination, token or "")
            results[full_name] = result
            cls.report(full_name, result)
            if result.get("stop"):
                cls.write_status(
                    {"stopped": f"{full_name}: {result['error']}", "repositories": results}
                )
                cls.open_issue(full_name, result["error"])
                return 2
            if not result["ok"]:
                code = 1
        cls.write_status({"repositories": results})
        return code

    @classmethod
    def report(cls, full_name: str, result: dict[str, Any]) -> None:
        """Print one line per repository, branch and cause, so a failed run is never silent.

        Args:
            full_name: The repository.
            result: What `mirror_one` returned.
        """
        if result.get("stop"):
            print(f"❌ {full_name}: STOPPED: {result['error']}")
        elif result.get("failures"):
            for ref, cause in result["failures"].items():
                print(f"❌ {full_name} {ref}: {cause}")
        elif not result.get("ok"):
            print(f"❌ {full_name}: {result.get('error')}")
        for ref, cause in (result.get("held") or {}).items():
            print(f"⚠️  {full_name} {ref}: held: {cause}")
        if result.get("backoff"):
            print(f"⚠️  {full_name}: GitHub is throttling, retrying next tick: {result['backoff']}")

    @classmethod
    def open_issue(cls, full_name: str, message: str) -> None:
        """Open an issue on the forge saying why the mirror stopped, unless one is open.

        Args:
            full_name: The repository.
            message: The reason, which never repeats an identifier.
        """
        from dreamference.forge.forge_container import ForgeContainer

        title = "GitHub mirror stopped"
        token = ForgeAccounts.admin_token()
        status, issues = ForgeContainer.api(
            "GET", f"/repos/{full_name}/issues?state=open&type=issues&q=GitHub%20mirror", token
        )
        if (
            status == HTTPStatus.OK
            and isinstance(issues, list)
            and any(issue.get("title") == title for issue in issues)
        ):
            return
        ForgeContainer.api(
            "POST",
            f"/repos/{full_name}/issues",
            token,
            {
                "title": title,
                "body": f"{message}\n\nThe mirror resumes by itself once the cause is "
                "gone; `ling-admin forge status` shows its state.",
            },
        )

    @classmethod
    def write_status(cls, status: dict[str, Any]) -> None:
        """Record what the run did, with the time, in the secrets folder.

        Args:
            status: What the run did.
        """
        ForgeSettings.write_secret(
            STATUS_NAME, json.dumps({"at": int(time.time()), **status}, indent=2)
        )

    @classmethod
    def status(cls) -> dict[str, Any] | None:
        """Read the last run's status.

        Returns:
            dict[str, Any] | None: The last run's status, or None when it never ran.
        """
        try:
            return json.loads(ForgeSettings.secret_path(STATUS_NAME).read_text())
        except (OSError, ValueError):
            return None
