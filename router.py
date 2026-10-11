"""Message routing based on @mentions with per-channel loop guard."""

import re


class Router:
    def __init__(self, agent_names: list[str], default_mention: str = "both",
                 max_hops: int = 4, online_checker=None):
        self.agent_names = set(n.lower() for n in agent_names)
        self.aliases = {}
        self.default_mention = default_mention
        self.max_hops = max_hops
        self._online_checker = online_checker  # callable() -> set of online agent names
        # Per-channel state: { channel: { hop_count, paused, guard_emitted } }
        self._channels: dict[str, dict] = {}
        self._build_pattern()

    def _get_ch(self, channel: str) -> dict:
        if channel not in self._channels:
            self._channels[channel] = {
                "hop_count": 0,
                "paused": False,
                "guard_emitted": False,
            }
        return self._channels[channel]

    def _build_pattern(self):
        # Sort longest-first so "gemini-2" is tried before "gemini"
        names = [re.escape(n) for n in sorted(self.agent_names | self.aliases.keys(), key=len, reverse=True)]
        alternatives = "|".join(names + ["both", "all"])
        self._mention_re = re.compile(
            rf"@({alternatives})(?![\w-])", re.IGNORECASE
        )

    def parse_mentions(self, text: str, broadcast: bool = True) -> list[str]:
        mentions = set()
        for match in self._mention_re.finditer(text):
            name = match.group(1).lower()
            if name in ("both", "all"):
                if not broadcast:
                    continue
                # Only tag online agents when using @all
                if self._online_checker:
                    online = self._online_checker()
                    mentions.update(n for n in self.agent_names if n in online)
                else:
                    mentions.update(self.agent_names)
            else:
                mentions.add(self.aliases.get(name, name))
        return list(mentions)

    def broadcast_kind(self, text: str) -> str | None:
        """'all' for @all/@both (the whole team, Zero #12950), else None."""
        names = {m.group(1).lower() for m in self._mention_re.finditer(text)}
        return 'all' if names & {'all', 'both'} else None

    def untagged(self, text: str) -> bool:
        """No @agent, @all or @both anywhere: a human message like this goes to the lead (Zero #12950)."""
        return self._mention_re.search(text or '') is None

    def named_mentions(self, text: str) -> list[str]:
        """Agents the message names directly, without expanding a broadcast."""
        return self.parse_mentions(text, broadcast=False)

    def _is_agent(self, sender: str) -> bool:
        return sender.lower() in self.agent_names

    def get_targets(self, sender: str, text: str, channel: str = "general", *, count_hop: bool = True) -> list[str]:
        """Determine which agents should receive this message."""
        if sender.lower() == "system":
            return []  # Provider errors/status notices cannot reset the human's hop budget.
        ch = self._get_ch(channel)
        # Only a human broadcast reaches everyone. Agents often name the human's broadcast in
        # prose ("your @all message"), and each such reply would re-wake the whole team and use up
        # the handoff budget; an agent hands off by naming the teammates who must act.
        mentions = self.parse_mentions(text, broadcast=not self._is_agent(sender))

        if not self._is_agent(sender):
            # Human message resets hop counter and unpauses
            ch["hop_count"] = 0
            ch["paused"] = False
            ch["guard_emitted"] = False
            if not mentions:
                if self.default_mention in ("both", "all"):
                    return list(self.agent_names)
                elif self.default_mention == "none":
                    return []
                return [self.default_mention]
            return mentions
        else:
            # Only route if explicit @mention. The agent-to-agent hop cap was removed at the user's
            # direction; hops are still counted for status, never used to block a handoff.
            if not mentions:
                return []
            if count_hop:
                ch["hop_count"] += 1
            # Don't route back to self
            return [m for m in mentions if m != sender]

    def continue_routing(self, channel: str = "general"):
        """Resume after loop guard pause."""
        ch = self._get_ch(channel)
        ch["hop_count"] = 0
        ch["paused"] = False
        ch["guard_emitted"] = False

    def is_paused(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["paused"]

    def is_guard_emitted(self, channel: str = "general") -> bool:
        return self._get_ch(channel)["guard_emitted"]

    def set_guard_emitted(self, channel: str = "general"):
        self._get_ch(channel)["guard_emitted"] = True

    def update_agents(self, names: list[str], aliases=None):
        """Replace the agent name set and rebuild the mention regex."""
        self.agent_names = set(n.lower() for n in names)
        self.aliases = {alias.lower(): target.lower() for alias, target in (aliases or {}).items()
                        if target.lower() in self.agent_names and alias.lower() not in self.agent_names}
        self._build_pattern()
