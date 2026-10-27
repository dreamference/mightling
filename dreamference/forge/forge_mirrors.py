"""Mirror nodes (specs/DREAMFERENCE_MIGHTLING_FORGEJO.md §0.2, §2.1, §3.1).

Every node that is not the forge of record runs a read-only mirror: a Forgejo pull mirror of
each repository the forge of record holds that is not private, pulled every ten minutes with the
read token of the record's `mirror` account. A private repository is invisible to that account,
so it can never become a mirror.

Managing goes over the pairing, as every node operation does: the node `forge mirrors sync` runs
on asks the forge of record for the *plan* (its node id, its host name, the token and the
repositories; `forge-mirror-plan` over SSH, or locally on the record) and hands it to each paired
mirror node (`forge-mirror-apply`, the plan on standard input), which creates the pull mirrors it
lacks. The record's own timer does the same for the nodes it is paired with, so a repository
created on the record reaches the mirrors within the hour.
"""

import json
import sys
from http import HTTPStatus
from typing import Any

from dreamference.forge.forge_accounts import ForgeAccounts
from dreamference.forge.forge_app_ini import RECORD_ALIAS
from dreamference.forge.forge_container import ForgeContainer
from dreamference.forge.forge_repositories import ForgeRepositories
from dreamference.forge.forge_settings import (
    HTTP_PORT,
    MIRROR_ACCOUNT,
    OWNER,
    ROLE_MIRROR,
    ROLE_RECORD,
    ForgeSettings,
)


class ForgeMirrors:
    """Builds, sends and applies the mirror plan."""

    @classmethod
    def plan(cls) -> dict[str, Any] | None:
        """Build, on the forge of record, the plan a mirror node needs.

        Returns:
            dict[str, Any] | None: `record_node`, `record_name`, `token` and `repositories`
            (`owner/name` of each repository that is not private), or None when this node is not
            the forge of record or its forge does not answer.
        """
        from dreamference.forge.forge_backup import ForgeBackup

        if ForgeSettings.role() != ROLE_RECORD or not ForgeContainer.running():
            return None
        token = ForgeAccounts.mirror_token()
        if token is None:
            return None
        try:
            repositories = ForgeRepositories.all()
        except RuntimeError:
            return None
        return {
            "record_node": ForgeBackup.node_id(),
            "record_name": ForgeBackup.host_name(),
            "token": token,
            "repositories": sorted(
                repo["full_name"]
                for repo in repositories
                if not ForgeRepositories.never_mirrored(repo)
            ),
        }

    @classmethod
    def serve_plan(cls) -> int:
        """Print the plan as JSON (the forced command's `forge-mirror-plan`).

        Returns:
            int: 0, or 1 when this node cannot give one.
        """
        plan = cls.plan()
        if plan is None:
            print("this node is not a running forge of record", file=sys.stderr)
            return 1
        print(json.dumps(plan))
        return 0

    @classmethod
    def apply(cls, plan: dict[str, Any]) -> list[str]:
        """Record the forge of record on a mirror node and create the pull mirrors it lacks.

        Args:
            plan: The record's plan.

        Returns:
            list[str]: One line per repository: mirrored now, already there, or why not.
        """
        from dreamference.forge.forge_backup import ForgeBackup

        if plan.get("record_node") == ForgeBackup.node_id():
            return ["this node is the forge of record"]
        state = ForgeSettings.state()
        if state.get("role") == ROLE_RECORD:
            return [
                "refused: this node holds the forge of record; `forge move` hands the role over first"
            ]
        changed = state.get("record_node") != plan.get("record_node")
        ForgeSettings.update_state(
            role=ROLE_MIRROR,
            record_node=plan.get("record_node"),
            record_name=plan.get("record_name"),
        )
        ForgeSettings.write_secret("record-mirror-token", str(plan.get("token", "")))
        if changed:
            # The container learns the record's address (`--add-host`) when it starts.
            from dreamference.forge.forge_service import ForgeService

            ForgeService.restart()
            ForgeContainer.wait_until_answering()
        admin = ForgeAccounts.admin_token()
        lines = []
        for full_name in plan.get("repositories", []):
            owner, name = full_name.split("/", 1)
            status, _ = ForgeContainer.api("GET", f"/repos/{full_name}", admin)
            if status == HTTPStatus.OK:
                lines.append(f"{full_name}: mirrored")
                continue
            if owner != OWNER:
                ForgeRepositories.ensure_organisation(owner)
            body = {
                "clone_addr": f"http://{RECORD_ALIAS}:{HTTP_PORT}/{full_name}.git",
                "repo_name": name,
                "repo_owner": owner,
                "mirror": True,
                "mirror_interval": "10m",
                "private": False,
                "auth_username": MIRROR_ACCOUNT,
                "auth_token": plan.get("token", ""),
                "service": "git",
                "description": f"Read-only mirror of {plan.get('record_name')}'s {full_name}",
            }
            status, answer = ForgeContainer.api("POST", "/repos/migrate", admin, body, timeout=1800)
            if status == HTTPStatus.CREATED:
                lines.append(f"{full_name}: mirror created")
            else:
                message = answer.get("message") if isinstance(answer, dict) else answer
                lines.append(f"{full_name}: not mirrored (HTTP {status}: {message})")
        return lines

    @classmethod
    def serve_apply(cls, text: str) -> int:
        """Apply a plan read from standard input (the forced command's `forge-mirror-apply`).

        Args:
            text: The plan as JSON.

        Returns:
            int: 0 when every repository is mirrored, 1 otherwise, 2 when the plan is unreadable.
        """
        try:
            plan = json.loads(text)
        except ValueError:
            print("refused: not a mirror plan", file=sys.stderr)
            return 2
        if not isinstance(plan, dict) or not isinstance(plan.get("repositories"), list):
            print("refused: not a mirror plan", file=sys.stderr)
            return 2
        if not ForgeSettings.enabled():
            print("the forge is off on this node", file=sys.stderr)
            return 1
        lines = cls.apply(plan)
        print("\n".join(lines))
        return 0 if all(line.endswith(("mirrored", "mirror created")) for line in lines) else 1

    @classmethod
    def fetch_plan(cls) -> dict[str, Any] | None:
        """Get the mirror plan, from this node or from the forge of record.

        Returns:
            dict[str, Any] | None: The plan, from this node when it is the record, else from
            the forge of record over the pairing (when this node is paired with it).
        """
        from dreamference.node.node_pairing import NodePairing

        if ForgeSettings.role() == ROLE_RECORD:
            return cls.plan()
        record_node = ForgeSettings.state().get("record_node")
        record = NodePairing.find(record_node) if record_node else None
        if record is None:
            return None
        result = NodePairing.run(record, "forge-mirror-plan")
        try:
            plan = json.loads(result.stdout) if result.returncode == 0 else None
        except ValueError:
            return None
        return plan if isinstance(plan, dict) else None

    @classmethod
    def sync(cls, quiet: bool = False) -> int:
        """Hand the plan to this node (when it is a mirror) and every paired node.

        This is `forge mirrors sync`, and the timer's run.

        Args:
            quiet: Print problems only (the timer's run).

        Returns:
            int: 0 when every node took it, 1 otherwise.
        """
        from dreamference.node.node_pairing import NodePairing

        plan = cls.fetch_plan()
        if plan is None:
            print(
                "❌ No mirror plan: this node is not the forge of record, and is not paired with it."
            )
            return 1
        code = 0
        if ForgeSettings.role() == ROLE_MIRROR:
            lines = cls.apply(plan)
            if not quiet:
                print("\n".join(f"   here: {line}" for line in lines))
        for record in NodePairing.paired():
            if record["node"] == plan["record_node"]:
                continue
            result = NodePairing.run(record, "forge-mirror-apply", input_text=json.dumps(plan))
            name = record.get("name") or record["node"]
            if result.returncode != 0:
                code = 1
                print(f"⚠️  {name}: {(result.stdout + result.stderr).strip()[-300:] or 'no answer'}")
            elif not quiet:
                print("\n".join(f"   {name}: {line}" for line in result.stdout.splitlines()))
        return code
