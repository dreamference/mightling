"""The forge's repositories and what each is allowed (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §3, §5.3).

`~/.config/dreamference/forge/repositories.json` names, per `owner/name`, the anonymity scope its
push hook checks (`full`, `denylist` or `off`), the paths exempt from it, and the GitHub
repository its branches are mirrored to, if any. A repository the file does not name gets the
file's `default_scope` (`off` in the product). A private repository can never be given a GitHub
destination, and `import` from GitHub goes through a work clone of the GitHub repository's
branches and tags, never through a local checkout, whose branches may still carry a rewritten
history.
"""

import json
import os
import subprocess
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_anonymity import SCOPE_FULL, SCOPE_OFF, SCOPES, ForgeAnonymity
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_settings import ADMIN, OWNER, ForgeSettings

CONFIG_NAME: Final[str] = "repositories.json"
BRANCHES_AND_TAGS: Final[tuple] = ("refs/heads/*:refs/heads/*", "refs/tags/*:refs/tags/*")
# Repositories that never leave the forge of record, whatever their visibility says (§5.3).
NEVER_MIRRORED: Final[tuple] = ("archive-pre-rewrite", "memos")
# Repositories per page of the API's search.
PAGE_SIZE: Final[int] = 50


class ForgeRepositories:
    """Reads the repository settings, imports repositories and installs their hooks."""

    @classmethod
    def config(cls) -> dict[str, Any]:
        """Read the repository settings file.

        Returns:
            dict[str, Any]: `default_scope` and `repositories` (by `owner/name`).
        """
        try:
            data = json.loads(ForgeSettings.secret_path(CONFIG_NAME).read_text())
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("default_scope", SCOPE_OFF)
        if not isinstance(data.get("repositories"), dict):
            data["repositories"] = {}
        return data

    @classmethod
    def save_config(cls, data: dict[str, Any]) -> None:
        """Save the repository settings file.

        Args:
            data: The whole settings file.
        """
        ForgeSettings.write_secret(CONFIG_NAME, json.dumps(data, indent=2, sort_keys=True))

    @classmethod
    def settings(cls, full_name: str) -> dict[str, Any]:
        """Return one repository's settings, with the file's defaults filled in.

        Args:
            full_name: `owner/name`.

        Returns:
            dict[str, Any]: `scope`, `exempt` and `github` (None when not mirrored).
        """
        data = cls.config()
        entry = data["repositories"].get(full_name, {})
        scope = entry.get("scope", data["default_scope"])
        return {
            "scope": scope if scope in SCOPES else SCOPE_FULL,
            "exempt": list(entry.get("exempt", [])),
            "github": entry.get("github") or None,
        }

    @classmethod
    def set_settings(cls, full_name: str, **changes: object) -> None:
        """Change one repository's settings.

        Args:
            full_name: `owner/name`.
            **changes: `scope`, `exempt` or `github` (None removes it).
        """
        data = cls.config()
        entry = dict(data["repositories"].get(full_name, {}))
        for key, value in changes.items():
            if value is None:
                entry.pop(key, None)
            else:
                entry[key] = value
        data["repositories"][full_name] = entry
        cls.save_config(data)

    @classmethod
    def never_mirrored(cls, repository: dict[str, Any]) -> bool:
        """Tell whether a repository may never be mirrored out.

        Args:
            repository: The API's repository object.

        Returns:
            bool: Whether the repository may never be mirrored out: private, or named in the
            never-mirror list. A missing visibility counts as private (fail closed).
        """
        return repository.get("private") is not False or repository.get("name") in NEVER_MIRRORED

    @classmethod
    def all(cls, token: str | None = None) -> list[dict[str, Any]]:
        """List every repository on this forge, private ones included.

        Args:
            token: An administrator's token; `forge-admin`'s when None.

        Returns:
            list[dict[str, Any]]: Every repository on this forge, private ones included.

        Raises:
            RuntimeError: The forge could not list them.
        """
        token = token or ForgeAccounts.admin_token()
        found: list[dict[str, Any]] = []
        page = 1
        while True:
            status, body = ForgeContainer.api(
                "GET", f"/repos/search?limit={PAGE_SIZE}&page={page}&private=true", token
            )
            items = body.get("data") if isinstance(body, dict) else None
            if status != HTTPStatus.OK or not isinstance(items, list):
                raise RuntimeError(f"the forge did not list its repositories (HTTP {status})")
            found += items
            if len(items) < PAGE_SIZE:
                return found
            page += 1

    @classmethod
    def remove_push_mirrors(cls) -> list[str]:
        """Remove every Forgejo push mirror, reporting one on a private repository loudly.

        This is the nightly check of §5.3, run by every tick: Forgejo's own push mirrors are never
        used (the GitHub mirror job replaces them).

        Returns:
            list[str]: One line per push mirror removed; empty when there was none.

        Raises:
            RuntimeError: The repositories could not be listed.
        """
        token = ForgeAccounts.admin_token()
        lines = []
        for repository in cls.all(token):
            full_name = repository["full_name"]
            status, mirrors = ForgeContainer.api("GET", f"/repos/{full_name}/push_mirrors", token)
            if status != HTTPStatus.OK or not isinstance(mirrors, list):
                continue
            for mirror in mirrors:
                deleted, _ = ForgeContainer.api(
                    "DELETE", f"/repos/{full_name}/push_mirrors/{mirror.get('remote_name')}", token
                )
                kind = "PRIVATE repository" if cls.never_mirrored(repository) else "repository"
                if deleted not in (HTTPStatus.OK, HTTPStatus.NO_CONTENT):
                    lines.append(
                        f"❌ Could not remove a Forgejo push mirror from the {kind} {full_name} "
                        f"({deleted}): it may still send code out."
                    )
                    continue
                lines.append(
                    f"❌ Removed a Forgejo push mirror from the {kind} {full_name}: only the GitHub "
                    "mirror job may send code out."
                )
        return lines

    @classmethod
    def repository_dir(cls, full_name: str) -> Path:
        """Return a repository's bare folder on the host side of the data folder.

        Args:
            full_name: `owner/name`.

        Returns:
            Path: The bare repository on the host side of the data folder.
        """
        owner, name = full_name.lower().split("/", 1)
        return ForgeSettings.data_dir() / "git" / "repositories" / owner / f"{name}.git"

    @classmethod
    def install_hooks(cls, mirror_trigger: bool) -> int:
        """Write the pattern files and put the hooks into every repository of this forge.

        Args:
            mirror_trigger: Whether repositories with a GitHub destination get the trigger.

        Returns:
            int: How many repositories got them.
        """
        ForgeAnonymity.write_patterns(ForgeSettings.data_dir())
        count = 0
        for repository in cls.all():
            full_name = repository["full_name"]
            folder = cls.repository_dir(full_name)
            if not folder.is_dir():
                continue
            settings = cls.settings(full_name)
            private = cls.never_mirrored(repository)
            ForgeAnonymity.install(
                folder,
                SCOPE_OFF if private else settings["scope"],
                settings["exempt"],
                check_roots=not private and settings["scope"] != SCOPE_OFF,
                trigger=mirror_trigger and bool(settings["github"]) and not private,
            )
            count += 1
        return count

    @classmethod
    def git_environment(cls, token: str, username: str = ADMIN) -> dict[str, str]:
        """Return the environment git authenticates to the forge with.

        Args:
            token: The forge token git authenticates with.
            username: The account it belongs to.

        Returns:
            dict[str, str]: An environment whose credential helper answers for the forge with
            the token, read from the environment, so it is never on a command line or in a URL
            git could print.
        """
        environment = dict(os.environ)
        environment["MIGHTLING_FORGE_TOKEN"] = token
        environment["MIGHTLING_FORGE_USER"] = username
        environment["GIT_TERMINAL_PROMPT"] = "0"
        return environment

    @classmethod
    def forge_credential_arguments(cls) -> list[str]:
        """Return `git -c` arguments for the forge's credential helper.

        Returns:
            list[str]: `git -c` arguments for the forge's credential helper (see `git_environment`).
        """
        helper = (
            "!f() { echo username=$MIGHTLING_FORGE_USER; echo password=$MIGHTLING_FORGE_TOKEN; }; f"
        )
        return [
            "-c",
            f"credential.{ForgeContainer.base_url()}.helper=",
            "-c",
            f"credential.{ForgeContainer.base_url()}.helper={helper}",
        ]

    @classmethod
    def forge_url(cls, full_name: str) -> str:
        """Return the repository's git URL on loopback.

        Args:
            full_name: `owner/name`.

        Returns:
            str: The repository's git URL on loopback.
        """
        return f"{ForgeContainer.base_url()}/{full_name}.git"

    @classmethod
    def create(cls, owner: str, name: str, private: bool, description: str = "") -> bool:
        """Create an empty repository for a user or an organisation, as `forge-admin`.

        Args:
            owner: The user or organisation.
            name: The repository's name.
            private: Whether it is private.
            description: Its description.

        Returns:
            bool: Whether it exists now.
        """
        token = ForgeAccounts.admin_token()
        status, _ = ForgeContainer.api("GET", f"/repos/{owner}/{name}", token)
        if status == HTTPStatus.OK:
            return True
        body = {
            "name": name,
            "private": private,
            "description": description,
            "default_branch": "main",
        }
        status, _ = ForgeContainer.api("GET", f"/orgs/{owner}", token)
        path = f"/orgs/{owner}/repos" if status == HTTPStatus.OK else f"/admin/users/{owner}/repos"
        status, answer = ForgeContainer.api("POST", path, token, body)
        if status != HTTPStatus.CREATED:
            print(
                f"❌ Could not create {owner}/{name} (HTTP {status}): "
                f"{answer.get('message') if isinstance(answer, dict) else answer}"
            )
            return False
        return True

    @classmethod
    def ensure_organisation(cls, name: str) -> bool:
        """Create an organisation owned by `owner` (visible to signed-in accounts only).

        Args:
            name: The organisation's name.

        Returns:
            bool: Whether it exists now.
        """
        token = ForgeAccounts.admin_token()
        status, _ = ForgeContainer.api("GET", f"/orgs/{name}", token)
        if status == HTTPStatus.OK:
            return True
        status, _ = ForgeContainer.api(
            "POST", f"/admin/users/{OWNER}/orgs", token, {"username": name, "visibility": "limited"}
        )
        return status == HTTPStatus.CREATED

    @classmethod
    def work_dir(cls, full_name: str) -> Path:
        """Return a repository's bare work clone, which the GitHub mirror and the import use.

        Args:
            full_name: `owner/name`.

        Returns:
            Path: The bare work clone the GitHub mirror and the import use.
        """
        return ForgeSettings.share_dir() / "github-work" / f"{full_name.replace('/', '__')}.git"

    @classmethod
    def git(
        cls,
        repository: Path,
        arguments: list[str],
        environment: dict[str, str] | None = None,
        timeout: int = 3600,
    ) -> subprocess.CompletedProcess:
        """Run git in a repository.

        Args:
            repository: A bare repository.
            arguments: git's arguments.
            environment: The environment, if not this process's.
            timeout: Seconds to allow.

        Returns:
            subprocess.CompletedProcess: The result (exit 127 when git could not run).
        """
        try:
            return subprocess.run(
                ["git", "-C", str(repository), *arguments],
                capture_output=True,
                text=True,
                env=environment,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return subprocess.CompletedProcess(arguments, 127, "", str(error))

    @classmethod
    def import_github(
        cls, source: str, full_name: str, scope: str, exempt: list[str], mirror: bool
    ) -> bool:
        """Import a GitHub repository's branches and tags into a new forge repository.

        Args:
            source: The GitHub repository's URL.
            full_name: `owner/name` on the forge; an organisation is created when missing.
            scope: The anonymity scope of its hook.
            exempt: Paths exempt from the check.
            mirror: Whether the forge's branches are then mirrored back to `source`.

        Returns:
            bool: Whether every branch and tag arrived.
        """
        from dreamference.forge.forge_github_mirror import ForgeGithubMirror

        owner, name = full_name.split("/", 1)
        if owner != OWNER and not cls.ensure_organisation(owner):
            print(f"❌ Could not create the organisation {owner}.")
            return False
        if not cls.create(owner, name, private=False, description=f"Imported from {source}"):
            return False
        work = cls.work_dir(full_name)
        if not work.is_dir():
            work.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(work)], check=False)
        environment = ForgeGithubMirror.github_environment()
        print(f"⬇️  Fetching {source}'s branches and tags…")
        fetched = cls.git(
            work,
            [
                *ForgeGithubMirror.github_credential_arguments(),
                "fetch",
                "--quiet",
                "--no-tags",
                source,
                *BRANCHES_AND_TAGS,
            ],
            environment,
        )
        if fetched.returncode != 0:
            print(f"❌ Could not fetch {source}: {fetched.stderr.strip()[-400:]}")
            return False
        token = ForgeAccounts.admin_token() or ""
        pushed = cls.git(
            work,
            [
                *cls.forge_credential_arguments(),
                "push",
                "--quiet",
                cls.forge_url(full_name),
                *BRANCHES_AND_TAGS,
            ],
            cls.git_environment(token),
        )
        if pushed.returncode != 0:
            print(f"❌ Could not push into the forge: {pushed.stderr.strip()[-400:]}")
            return False
        cls.set_settings(full_name, scope=scope, exempt=exempt, github=source if mirror else None)
        print(f"✅ Imported {source} as {full_name}.")
        return True

    @classmethod
    def import_bundles(cls, bundles: list[Path], full_name: str) -> bool:
        """Import git bundles into a private, archived (read-only) repository.

        Each bundle's refs go under its own prefix (`refs/heads/<bundle>/…`, `refs/tags/<bundle>/…`).

        Args:
            bundles: The bundle files.
            full_name: `owner/name`; it is created private and is never mirrored.

        Returns:
            bool: Whether every bundle arrived.
        """
        owner, name = full_name.split("/", 1)
        if owner != OWNER and not cls.ensure_organisation(owner):
            return False
        if not cls.create(
            owner, name, private=True, description="Pre-rewrite history; never leaves this forge"
        ):
            return False
        work = ForgeSettings.share_dir() / "archive-work" / f"{full_name.replace('/', '__')}.git"
        work.parent.mkdir(parents=True, exist_ok=True)
        if not work.is_dir():
            subprocess.run(["git", "init", "-q", "--bare", str(work)], check=False)
        for bundle in bundles:
            prefix = bundle.name.removesuffix(".bundle")
            fetched = cls.git(
                work,
                [
                    "fetch",
                    "--quiet",
                    "--no-tags",
                    str(bundle),
                    f"refs/heads/*:refs/heads/{prefix}/*",
                    f"refs/tags/*:refs/tags/{prefix}/*",
                ],
            )
            if fetched.returncode != 0:
                print(f"❌ Could not read {bundle.name}: {fetched.stderr.strip()[-300:]}")
                return False
        token = ForgeAccounts.admin_token() or ""
        pushed = cls.git(
            work,
            [
                *cls.forge_credential_arguments(),
                "push",
                "--quiet",
                cls.forge_url(full_name),
                *BRANCHES_AND_TAGS,
            ],
            cls.git_environment(token),
        )
        if pushed.returncode != 0:
            print(f"❌ Could not push the archive into the forge: {pushed.stderr.strip()[-400:]}")
            return False
        cls.set_settings(full_name, scope=SCOPE_OFF, github=None)
        status, _ = ForgeContainer.api(
            "PATCH",
            f"/repos/{full_name}",
            ForgeAccounts.admin_token(),
            {"archived": True, "private": True},
        )
        if status != HTTPStatus.OK:
            print(
                f"❌ {len(bundles)} bundles are in {full_name}, but Forgejo did not make it private "
                f"and archived ({status})."
            )
            return False
        print(f"✅ {len(bundles)} bundles are in {full_name} (private, archived, never mirrored).")
        return True
