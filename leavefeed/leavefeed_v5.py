import asyncio

import discord
from redbot.core import commands

from .leavefeed_v4 import LeaveFeed as BaseLeaveFeed, ContinueView, ChoiceView


class FinalizingTextPageModal(discord.ui.Modal):
    def __init__(self, cog: "LeaveFeed", session_id: str, title: str, entries):
        super().__init__(title=title[:45] or "Feedback", timeout=600)
        self.cog = cog
        self.session_id = session_id
        self.entries = entries
        self.inputs = []

        for index, element in entries:
            style = discord.TextStyle.short if element.get("kind") == "short" else discord.TextStyle.paragraph
            min_length = int(element.get("min_length", 0) or 0)
            max_length = max(1, min(int(element.get("max_length", 1000) or 1000), 4000))
            item = discord.ui.TextInput(
                label=str(element.get("label") or f"Domanda {index + 1}")[:45],
                placeholder=(str(element.get("placeholder"))[:100] if element.get("placeholder") else None),
                style=style,
                required=bool(element.get("required", True)),
                min_length=(min_length if min_length > 0 else None),
                max_length=max_length,
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        session = self.cog._sessions.get(self.session_id)
        if not session or interaction.user.id != session["user_id"]:
            return await interaction.response.send_message("Sessione non valida o scaduta.", ephemeral=True)

        for (index, _), item in zip(self.entries, self.inputs):
            session["answers"][index] = str(item.value or "").strip()
        session["index"] = self.entries[-1][0] + 1

        guild = self.cog.bot.get_guild(session["guild_id"])
        if guild is None:
            return await interaction.response.send_message("Server non disponibile.", ephemeral=True)
        data = await self.cog._ensure_schema(guild)
        modal = data["modals"].get(session["modal_key"])
        if not modal:
            return await interaction.response.send_message("Modal non trovato.", ephemeral=True)

        if session["index"] >= len(modal.get("elements") or []):
            return await self.cog._finish_session(interaction, self.session_id, modal)

        await interaction.response.send_message(
            "Pagina completata. Premi Continua per proseguire.",
            view=ContinueView(self.cog, self.session_id),
            ephemeral=True,
        )


class LeaveFeed(BaseLeaveFeed):
    """LeaveFeed v2.2.3: consegna DM piu rapida e invio modal immediato."""

    __version__ = "2.2.4"

    async def _was_kicked_or_banned_fast(self, member: discord.Member) -> bool:
        guild = member.guild
        me = guild.me
        if me is None or not me.guild_permissions.view_audit_log:
            return False

        for delay in (0.0, 0.15, 0.25):
            if delay:
                await asyncio.sleep(delay)
            now = discord.utils.utcnow()
            for action in (discord.AuditLogAction.kick, discord.AuditLogAction.ban):
                try:
                    async for entry in guild.audit_logs(limit=8, action=action):
                        if getattr(getattr(entry, "target", None), "id", None) != member.id:
                            continue
                        age = (now - entry.created_at).total_seconds()
                        if 0 <= age <= 8:
                            return True
                except (discord.Forbidden, discord.HTTPException):
                    return False
        return False

    async def show_current_step(self, interaction: discord.Interaction, session_id: str):
        session = self._sessions.get(session_id)
        if not session or interaction.user.id != session["user_id"]:
            return await interaction.response.send_message("Sessione non valida o scaduta.", ephemeral=True)

        guild = self.bot.get_guild(session["guild_id"])
        if guild is None:
            return await interaction.response.send_message("Server non disponibile.", ephemeral=True)
        data = await self._ensure_schema(guild)
        modal = data["modals"].get(session["modal_key"])
        if not modal:
            return await interaction.response.send_message("Modal non trovato.", ephemeral=True)

        elements = modal.get("elements") or []
        index = session["index"]
        if index >= len(elements):
            return await self._finish_session(interaction, session_id, modal)

        element = elements[index]
        kind = element.get("kind", "long")
        if kind == "paragraph":
            session["index"] += 1
            return await interaction.response.send_message(
                str(element.get("text") or "")[:1900],
                view=ContinueView(self, session_id),
                ephemeral=True,
            )
        if kind in {"single", "multi"}:
            return await interaction.response.send_message(
                f"**{str(element.get('label') or 'Scegli un’opzione')}**",
                view=ChoiceView(self, session_id, element),
                ephemeral=True,
            )

        entries = []
        cursor = index
        while cursor < len(elements) and len(entries) < 5:
            current = elements[cursor]
            if current.get("kind", "long") not in {"short", "long"}:
                break
            entries.append((cursor, current))
            cursor += 1

        title = str(modal.get("title") or "Feedback")
        await interaction.response.send_modal(
            FinalizingTextPageModal(self, session_id, title, entries)
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return

        data = await self._ensure_schema(member.guild)
        if not data.get("enabled"):
            return

        try:
            await member.create_dm()
        except (discord.Forbidden, discord.HTTPException):
            pass

        if await self._was_kicked_or_banned_fast(member):
            return

        await self._send_leave_dm(member, member.guild)
