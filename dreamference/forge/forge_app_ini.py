"""Forgejo's `app.ini`, generated, never edited by hand (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §2.3).

The file lives in the data folder (0600) with the instance's secrets in it. It is rewritten at
every start with the node's current name, so a move or a rename needs no hand edit; the secrets
are read back from the file before it is rewritten, so they survive (a restored dump brings its
own, which must survive too: the database's encrypted fields are sealed with them).
"""

import base64
import configparser
import os
import secrets
from pathlib import Path
from typing import Final

from dreamference.forge.forge_settings import (
    ARTEFACT_DAYS_MIRROR,
    ARTEFACT_DAYS_RECORD,
    CONTAINER_HTTP_PORT,
    CONTAINER_SSH_PORT,
    ROLE_RECORD,
    SSH_PORT,
    WEB_PATH,
    WEB_PORT,
)

# Where the rootless image keeps everything (GITEA_WORK_DIR) and reads its config from.
WORK_DIR: Final[str] = "/var/lib/gitea"
APP_INI_RELATIVE: Final[str] = "custom/conf/app.ini"
DATABASE_RELATIVE: Final[str] = "data/forgejo.db"
# The name a mirror's container knows the forge of record by (`--add-host`, resolved at start).
RECORD_ALIAS: Final[str] = "forge-record"
# The secrets Forgejo seals data with: kept from the previous file, generated once.
SECRET_KEYS: Final[tuple] = (
    ("security", "SECRET_KEY"),
    ("security", "INTERNAL_TOKEN"),
    ("oauth2", "JWT_SECRET"),
    ("server", "LFS_JWT_SECRET"),
)


class ForgeAppIni:
    """Renders and writes `app.ini`."""

    @classmethod
    def path(cls, data_dir: Path) -> Path:
        """Return where `app.ini` lies in the data folder.

        Args:
            data_dir: The forge's data folder (the container's `/var/lib/gitea`).

        Returns:
            Path: `app.ini` in the data folder.
        """
        return data_dir / APP_INI_RELATIVE

    @classmethod
    def read_secrets(cls, data_dir: Path) -> dict[str, str]:
        """Read the secrets the current file holds.

        Args:
            data_dir: The forge's data folder.

        Returns:
            dict[str, str]: The secrets the current file holds, by `section.KEY`.
        """
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str  # type: ignore[assignment,method-assign]
        try:
            parser.read_string("[DEFAULT]\n" + cls.path(data_dir).read_text())
        except configparser.ParsingError:
            pass  # The lines that did parse are kept, so a stray line does not cost the secrets.
        except (OSError, configparser.Error):
            return {}
        found = {}
        for section, key in SECRET_KEYS:
            if parser.has_option(section, key) and parser.get(section, key):
                found[f"{section}.{key}"] = parser.get(section, key)
        return found

    @classmethod
    def new_secrets(cls) -> dict[str, str]:
        """Generate fresh secrets.

        Returns:
            dict[str, str]: Fresh secrets; the JWT ones are 32 random bytes in unpadded base64url,
            the form Forgejo itself generates.
        """

        def jwt() -> str:
            return base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()

        return {
            "security.SECRET_KEY": secrets.token_urlsafe(48),
            "security.INTERNAL_TOKEN": secrets.token_urlsafe(64),
            "oauth2.JWT_SECRET": jwt(),
            "server.LFS_JWT_SECRET": jwt(),
        }

    @classmethod
    def render(cls, host: str, role: str, secret_values: dict[str, str]) -> str:
        """Render the text of `app.ini`.

        Args:
            host: The node's mDNS name without `.local` (the name clients reach it by).
            role: `record` or `mirror`.
            secret_values: The secrets by `section.KEY` (see `SECRET_KEYS`).

        Returns:
            str: The file's text.
        """
        domain = f"{host}.local"
        record = role == ROLE_RECORD
        days = ARTEFACT_DAYS_RECORD if record else ARTEFACT_DAYS_MIRROR
        sections: list[tuple[str, dict[str, str]]] = [
            (
                "",
                {
                    "APP_NAME": "Mightling forge",
                    "RUN_MODE": "prod",
                    "RUN_USER": "git",
                    "WORK_PATH": WORK_DIR,
                },
            ),
            (
                "server",
                {
                    "PROTOCOL": "http",
                    "DOMAIN": domain,
                    # The pages link under `ling web`'s `/forge/`, which takes the prefix off on
                    # the way in: Forgejo serves at its own root, so git, the API and the mirrors
                    # keep using `http://<node>:3200/<owner>/<repo>.git` (measured on 15.0.11).
                    "ROOT_URL": f"http://{domain}:{WEB_PORT}{WEB_PATH}",
                    # Inside the container; where it is reachable from is decided by `docker run -p`,
                    # one address at a time (ForgeBind).
                    "HTTP_ADDR": "0.0.0.0",
                    "HTTP_PORT": str(CONTAINER_HTTP_PORT),
                    "DISABLE_SSH": "false",
                    "START_SSH_SERVER": "true",
                    "SSH_DOMAIN": domain,
                    "SSH_PORT": str(SSH_PORT),
                    "SSH_LISTEN_HOST": "0.0.0.0",
                    "SSH_LISTEN_PORT": str(CONTAINER_SSH_PORT),
                    "BUILTIN_SSH_SERVER_USER": "git",
                    "LFS_START_SERVER": "false",
                    "OFFLINE_MODE": "true",
                    "APP_DATA_PATH": f"{WORK_DIR}/data",
                    "LANDING_PAGE": "login",
                    "LFS_JWT_SECRET": secret_values["server.LFS_JWT_SECRET"],
                },
            ),
            (
                "database",
                {
                    "DB_TYPE": "sqlite3",
                    "PATH": f"{WORK_DIR}/{DATABASE_RELATIVE}",
                    "SQLITE_JOURNAL_MODE": "WAL",
                },
            ),
            (
                "repository",
                {
                    "ROOT": f"{WORK_DIR}/git/repositories",
                    "DEFAULT_PRIVATE": "private",
                    "DEFAULT_BRANCH": "main",
                    "DISABLE_STARS": "true",
                },
            ),
            (
                "security",
                {
                    "INSTALL_LOCK": "true",
                    "SECRET_KEY": secret_values["security.SECRET_KEY"],
                    "INTERNAL_TOKEN": secret_values["security.INTERNAL_TOKEN"],
                    "DISABLE_GIT_HOOKS": "true",
                    "MIN_PASSWORD_LENGTH": "12",
                },
            ),
            (
                "service",
                {
                    "DISABLE_REGISTRATION": "true",
                    "REQUIRE_SIGNIN_VIEW": "true",
                    "DEFAULT_KEEP_EMAIL_PRIVATE": "true",
                    "ENABLE_NOTIFY_MAIL": "false",
                    "DEFAULT_ALLOW_CREATE_ORGANIZATION": "true",
                    "DEFAULT_ORG_VISIBILITY": "limited",
                    "DEFAULT_USER_VISIBILITY": "limited",
                },
            ),
            ("oauth2", {"JWT_SECRET": secret_values["oauth2.JWT_SECRET"]}),
            ("openid", {"ENABLE_OPENID_SIGNIN": "false", "ENABLE_OPENID_SIGNUP": "false"}),
            ("mailer", {"ENABLED": "false"}),
            ("picture", {"DISABLE_GRAVATAR": "true", "ENABLE_FEDERATED_AVATAR": "false"}),
            ("cron.update_checker", {"ENABLED": "false"}),
            ("federation", {"ENABLED": "false"}),
            ("packages", {"ENABLED": "false"}),
            ("indexer", {"REPO_INDEXER_ENABLED": "false"}),
            ("other", {"SHOW_FOOTER_VERSION": "false"}),
            ("log", {"MODE": "console", "LEVEL": "Warn"}),
            # Forgejo's own push mirrors push with --mirror, which deletes on the remote what the
            # forge deleted, and a private repository could get one through the API. Off on every
            # node: the forge's only way out is the GitHub mirror job, which skips private
            # repositories (§3.2, §5.3).
            # Forgejo's API still takes a push mirror with DISABLE_NEW_PUSH set (measured on
            # 15.0.11), so the forge of record, which needs no pull mirror either, has mirroring
            # off altogether; a mirror node keeps it for its pull mirrors, and the tick removes
            # any push mirror it finds (ForgeRepositories.remove_push_mirrors).
            (
                "mirror",
                {
                    "ENABLED": "false" if record else "true",
                    "DISABLE_NEW_PUSH": "true",
                    "DEFAULT_INTERVAL": "10m",
                    "MIN_INTERVAL": "10m",
                },
            ),
            # A mirror pulls from the forge of record, an address on the LAN.
            (
                "migrations",
                {
                    "ALLOW_LOCALNETWORKS": "false" if record else "true",
                    "ALLOWED_DOMAINS": "" if record else RECORD_ALIAS,
                },
            ),
            (
                "actions",
                {
                    "ENABLED": "true" if record else "false",
                    "ARTIFACT_RETENTION_DAYS": str(days),
                    "LOG_RETENTION_DAYS": str(days),
                },
            ),
        ]
        lines: list[str] = [
            "; Written by `ling-admin forge` at every start; edits are overwritten."
        ]
        for name, values in sections:
            if name:
                lines += ["", f"[{name}]"]
            lines += [f"{key} = {value}" for key, value in values.items()]
        return "\n".join(lines) + "\n"

    @classmethod
    def write(cls, data_dir: Path, host: str, role: str) -> Path:
        """Write the file 0600, keeping the secrets the previous one held.

        Args:
            data_dir: The forge's data folder.
            host: The node's mDNS name without `.local`.
            role: `record` or `mirror`.

        Returns:
            Path: The file written.
        """
        values = {**cls.new_secrets(), **cls.read_secrets(data_dir)}
        path = cls.path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        staging = path.with_name(f".app.ini.{os.getpid()}.tmp")
        descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(cls.render(host, role, values))
            # On disk before it replaces the old file: an empty one after a power cut reads as no
            # secrets, and new ones would be made.
            handle.flush()
            os.fsync(handle.fileno())
        Path(staging).replace(path)
        return path
