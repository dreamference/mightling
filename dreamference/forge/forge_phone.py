"""The phone account, `mightling-phone`: what `ling web`'s repository service uses on the forge.

The spec: specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §2.2 and §2.7, and
specs/DREAMFERENCE_MIGHTLING_CHAT_REPOS.md §3.3 and §4.2.

On the forge of record only. The account has write access to every repository (read on an
archived one), private ones included (CHAT_REPOS R2). Its token lies in `ling web`'s state folder
(`~/.mightling/web/forge-phone-token`, 0600), the file `chat_repos.rs` reads; it is never written
anywhere else. Every repository's default branch is protected so that only `owner` and
`forge-admin` push to it, plus the phone account where `[chat_repos] allow_main` in the user's own
config names the repository; merging a pull request stays open to anyone with write access.
"""

import os
import time
import tomllib
from http import HTTPStatus
from pathlib import Path
from typing import Any, Final

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_settings import ADMIN, OWNER, ForgeSettings

PHONE_ACCOUNT: Final[str] = "mightling-phone"
# `ling web`'s state folder and the file in it (CHAT_REPOS §4.2; `chat_repos::TOKEN_FILE`).
TOKEN_FILE: Final[str] = "forge-phone-token"
RULE_NAME: Final[str] = "rule_name"
TOKEN_SCOPES: Final[str] = "write:repository,write:issue,read:user,read:organization"


class ForgePhone:
    """Creates and maintains the phone account, its token and the default branches' protection."""

    @classmethod
    def token_path(cls) -> Path:
        """Return where the phone account's token lies.

        Returns:
            Path: `$CODEX_HOME/web/forge-phone-token` (`~/.mightling/web/…` by default).
        """
        home = os.environ.get("CODEX_HOME") or Path("~/.mightling").expanduser()
        return Path(home) / "web" / TOKEN_FILE

    @classmethod
    def allow_main(cls) -> set[str]:
        """Return the repositories whose default branch the phone may push to.

        Returns:
            set[str]: `owner/name`s from `[chat_repos] allow_main` in the user's own config file;
            empty when it names none or cannot be read.
        """
        try:
            data = tomllib.loads(ForgeSettings.config_path().read_text())
        except (OSError, tomllib.TOMLDecodeError):
            return set()
        table = data.get("chat_repos")
        names = table.get("allow_main") if isinstance(table, dict) else None
        return {str(name) for name in names} if isinstance(names, list) else set()

    @classmethod
    def write_token(cls, token: str) -> Path:
        """Write the token 0600 into `ling web`'s 0700 state folder.

        Args:
            token: The access token.

        Returns:
            Path: Where it was written.
        """
        path = cls.token_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        staging = path.parent / f".{TOKEN_FILE}.{os.getpid()}.tmp"
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(token + "\n")
        staging.replace(path)
        return path

    @classmethod
    def ensure_account(cls, admin: str) -> bool:
        """Create the account and its token when either is missing.

        Args:
            admin: `forge-admin`'s token.

        Returns:
            bool: Whether the account and the token file exist now.
        """
        status, _ = ForgeContainer.api("GET", f"/users/{PHONE_ACCOUNT}", admin)
        if status == HTTPStatus.NOT_FOUND:
            # The password is generated and dropped: the account signs in by token only.
            if ForgeAccounts.create_user(PHONE_ACCOUNT, admin=False) is None:
                print(f"❌ Could not create the forge's `{PHONE_ACCOUNT}` account.")
                return False
        elif status != HTTPStatus.OK:
            return False
        if cls.token_path().exists():
            return True
        token = ForgeAccounts.generate_token(
            PHONE_ACCOUNT, f"ling-web-{int(time.time())}", TOKEN_SCOPES
        )
        if token is None:
            print(f"❌ Could not make `{PHONE_ACCOUNT}`'s access token.")
            return False
        cls.write_token(token)
        return True

    @classmethod
    def protection(cls, full_name: str, branch: str, phone_on_main: bool) -> dict[str, Any]:
        """Return the protection rule the default branch gets.

        Args:
            full_name: `owner/name`.
            branch: The default branch.
            phone_on_main: Whether `[chat_repos] allow_main` names the repository.

        Returns:
            dict[str, Any]: The API's branch protection body.
        """
        users = [OWNER, ADMIN] + ([PHONE_ACCOUNT] if phone_on_main else [])
        return {
            RULE_NAME: branch,
            "enable_push": True,
            "enable_push_whitelist": True,
            "push_whitelist_usernames": users,
            "enable_merge_whitelist": False,
            "block_on_rejected_reviews": False,
        }

    @classmethod
    def ensure_repository(
        cls, repository: dict[str, Any], admin: str, allowed: set[str]
    ) -> list[str]:
        """Give the phone its access to one repository and protect the default branch.

        Args:
            repository: The API's repository object.
            admin: `forge-admin`'s token.
            allowed: The repositories whose default branch the phone may push to.

        Returns:
            list[str]: Lines for what failed; empty when all is in place.
        """
        full_name = str(repository.get("full_name") or "")
        if not full_name:
            return []
        archived = bool(repository.get("archived"))
        problems = []
        permission = "read" if archived else "write"
        status, _ = ForgeContainer.api(
            "PUT",
            f"/repos/{full_name}/collaborators/{PHONE_ACCOUNT}",
            admin,
            {"permission": permission},
        )
        if status not in (HTTPStatus.NO_CONTENT, HTTPStatus.OK):
            problems.append(
                f"⚠️  {full_name}: `{PHONE_ACCOUNT}` not given {permission} access (HTTP {status})."
            )
        if archived or repository.get("mirror"):
            return problems
        branch = str(repository.get("default_branch") or "main")
        wanted = cls.protection(full_name, branch, full_name in allowed)
        status, body = ForgeContainer.api("GET", f"/repos/{full_name}/branch_protections", admin)
        rules = body if status == HTTPStatus.OK and isinstance(body, list) else None
        if rules is None:
            return [*problems, f"⚠️  {full_name}: branch protection not readable (HTTP {status})."]
        rule = next((item for item in rules if item.get(RULE_NAME) == branch), None)
        if rule is None:
            status, _ = ForgeContainer.api(
                "POST", f"/repos/{full_name}/branch_protections", admin, wanted
            )
        elif sorted(rule.get("push_whitelist_usernames") or []) != sorted(
            wanted["push_whitelist_usernames"]
        ) or not (rule.get("enable_push") and rule.get("enable_push_whitelist")):
            changes = {key: value for key, value in wanted.items() if key != RULE_NAME}
            status, _ = ForgeContainer.api(
                "PATCH", f"/repos/{full_name}/branch_protections/{branch}", admin, changes
            )
        else:
            return problems
        if status not in (HTTPStatus.OK, HTTPStatus.CREATED):
            problems.append(f"⚠️  {full_name}: `{branch}` not protected (HTTP {status}).")
        return problems

    @classmethod
    def ensure(cls) -> list[str]:
        """Bring the account, its token and every repository's access and protection in line.

        Returns:
            list[str]: Lines for what failed; empty when all is in place.

        Raises:
            RuntimeError: The forge could not list its repositories.
        """
        from dreamference.forge.forge_repositories import ForgeRepositories

        admin = ForgeAccounts.admin_token()
        if not admin:
            return ["⚠️  The phone account was not checked: no `forge-admin` token yet."]
        if not cls.ensure_account(admin):
            return [f"⚠️  The forge's `{PHONE_ACCOUNT}` account is not ready."]
        allowed = cls.allow_main()
        problems = []
        for repository in ForgeRepositories.all(admin):
            problems += cls.ensure_repository(repository, admin, allowed)
        return problems
