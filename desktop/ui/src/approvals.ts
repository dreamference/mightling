// What a command's approval card offers (DESKTOP_PARITY §9.5.9, DESKTOP §16 B8): beside Allow, Allow
// for this session and Decline, **Always allow commands starting with …** when the server proposes a
// rule for them (`proposedExecpolicyAmendment`, answered `acceptWithExecpolicyAmendment`; `ling` keeps
// it in its rules, so it holds in later sessions), and a host to allow or block from now on when it
// proposes one (`proposedNetworkPolicyAmendments`, answered `applyNetworkPolicyAmendment`), never a
// host to allow at `/airgapped on`. When the server lists `availableDecisions`, only those are offered.
// Pure: no DOM.

export type CommandDecision =
  | "accept"
  | "acceptForSession"
  | "decline"
  | "cancel"
  | { acceptWithExecpolicyAmendment: { execpolicy_amendment: string[] } }
  | { applyNetworkPolicyAmendment: { network_policy_amendment: { host: string; action: "allow" | "deny" } } };

export interface ApprovalChoice {
  label: string;
  /** A longer line for the button's title: what the choice keeps. */
  title: string;
  decision: CommandDecision;
  primary?: boolean;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** A command prefix as the button shows it: its words joined, quoted where they hold a space. */
export function prefixLabel(prefix: string[]): string {
  const words = prefix.map((word) => (/[\s"'\\]/.test(word) ? JSON.stringify(word) : word)).join(" ");
  return words.length > 60 ? `${words.slice(0, 59)}…` : words;
}

function amendmentOf(value: unknown): string[] | null {
  return Array.isArray(value) && value.length > 0 && value.every((word) => typeof word === "string") ? (value as string[]) : null;
}

function hostRule(value: unknown): { host: string; action: "allow" | "deny" } | null {
  if (!isObject(value) || typeof value.host !== "string" || !value.host.trim()) return null;
  return value.action === "allow" || value.action === "deny" ? { host: value.host, action: value.action } : null;
}

/** The choices a command approval offers, in order. */
export function commandChoices(params: unknown, airgappedOn: boolean): ApprovalChoice[] {
  const p = isObject(params) ? params : {};
  const always: ApprovalChoice[] = [];
  const hosts: ApprovalChoice[] = [];
  const addPrefix = (prefix: string[]) => {
    if (always.length) return;
    always.push({ label: `Always allow ${prefixLabel(prefix)} …`, title: `Run commands starting with ${prefix.join(" ")} without asking, in this and later sessions`, decision: { acceptWithExecpolicyAmendment: { execpolicy_amendment: prefix } } });
  };
  const addHost = (rule: { host: string; action: "allow" | "deny" }) => {
    if (rule.action === "allow" && airgappedOn) return;
    if (hosts.some((choice) => typeof choice.decision === "object" && "applyNetworkPolicyAmendment" in choice.decision && choice.decision.applyNetworkPolicyAmendment.network_policy_amendment.host === rule.host && choice.decision.applyNetworkPolicyAmendment.network_policy_amendment.action === rule.action)) return;
    hosts.push({
      label: rule.action === "allow" ? `Always allow ${rule.host}` : `Always block ${rule.host}`,
      title: rule.action === "allow" ? `Let commands reach ${rule.host} from now on without asking` : `Refuse ${rule.host} to commands from now on`,
      decision: { applyNetworkPolicyAmendment: { network_policy_amendment: rule } },
    });
  };
  const listed = Array.isArray(p.availableDecisions) ? p.availableDecisions : null;
  const plain = new Set<string>();
  if (listed) {
    for (const decision of listed) {
      if (typeof decision === "string") plain.add(decision);
      else if (isObject(decision) && isObject(decision.acceptWithExecpolicyAmendment)) {
        const prefix = amendmentOf(decision.acceptWithExecpolicyAmendment.execpolicy_amendment);
        if (prefix) addPrefix(prefix);
      } else if (isObject(decision) && isObject(decision.applyNetworkPolicyAmendment)) {
        const rule = hostRule(decision.applyNetworkPolicyAmendment.network_policy_amendment);
        if (rule) addHost(rule);
      }
    }
  } else {
    for (const decision of ["accept", "acceptForSession", "decline", "cancel"]) plain.add(decision);
    const prefix = amendmentOf(p.proposedExecpolicyAmendment);
    if (prefix) addPrefix(prefix);
    for (const rule of Array.isArray(p.proposedNetworkPolicyAmendments) ? p.proposedNetworkPolicyAmendments : []) {
      const parsed = hostRule(rule);
      if (parsed) addHost(parsed);
    }
  }
  const choices: ApprovalChoice[] = [];
  if (plain.has("accept")) choices.push({ label: "Allow", title: "Run it this once", decision: "accept", primary: true });
  if (plain.has("acceptForSession")) choices.push({ label: "Allow for this session", title: "Run it, and the same again, without asking until this session ends", decision: "acceptForSession" });
  choices.push(...always, ...hosts);
  if (plain.has("decline")) choices.push({ label: "Decline", title: "Do not run it; the agent goes on", decision: "decline" });
  if (plain.has("cancel")) choices.push({ label: "Stop the turn", title: "Do not run it, and stop the turn", decision: "cancel" });
  return choices;
}
