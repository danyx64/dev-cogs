from __future__ import annotations

from typing import Any, Dict, Optional

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class ReactionRoles(commands.Cog):
    """Reaction roles semplici e persistenti."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=89234127516384029, force_registration=True)
        self.config.register_guild(bindings={})

    @staticmethod
    def _emoji_key_from_partial(emoji: discord.PartialEmoji) -> str:
        if emoji.id is not None:
            return f"custom:{emoji.id}"
        return f"unicode:{emoji.name or str(emoji)}"

    @classmethod
    def _emoji_key_from_text(cls, value: str) -> str:
        partial = discord.PartialEmoji.from_str(value.strip())
        return cls._emoji_key_from_partial(partial)

    @staticmethod
    def _emoji_for_api(value: str):
        partial = discord.PartialEmoji.from_str(value.strip())
        if partial.id is not None:
            return partial
        return partial.name or value.strip()

    @staticmethod
    def _binding_key(channel_id: int, message_id: int, emoji_key: str) -> str:
        return f"{channel_id}:{message_id}:{emoji_key}"

    @staticmethod
    def _member_can_manage_role(member: discord.Member, role: discord.Role) -> bool:
        if member.guild.owner_id == member.id:
            return True
        return member.guild_permissions.administrator or (
            member.guild_permissions.manage_roles and member.top_role > role
        )

    @staticmethod
    def _bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if me is None:
            return False
        return me.guild_permissions.manage_roles and me.top_role > role and not role.managed

    async def _get_binding(
        self,
        guild: discord.Guild,
        channel_id: int,
        message_id: int,
        emoji: discord.PartialEmoji,
    ) -> Optional[Dict[str, Any]]:
        bindings = await self.config.guild(guild).bindings()
        key = self._binding_key(channel_id, message_id, self._emoji_key_from_partial(emoji))
        value = bindings.get(key)
        return value if isinstance(value, dict) else None

    async def _member_from_payload(self, payload: discord.RawReactionActionEvent) -> Optional[discord.Member]:
        guild = self.bot.get_guild(payload.guild_id or 0)
        if guild is None:
            return None

        if payload.member is not None:
            return payload.member

        member = guild.get_member(payload.user_id)
        if member is not None:
            return member

        try:
            return await guild.fetch_member(payload.user_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None

    async def _apply_role(self, payload: discord.RawReactionActionEvent, *, add: bool) -> None:
        if payload.guild_id is None:
            return
        if self.bot.user and payload.user_id == self.bot.user.id:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        binding = await self._get_binding(
            guild,
            payload.channel_id,
            payload.message_id,
            payload.emoji,
        )
        if not binding:
            return

        role = guild.get_role(int(binding.get("role_id") or 0))
        if role is None or not self._bot_can_manage_role(guild, role):
            return

        member = await self._member_from_payload(payload)
        if member is None or member.bot:
            return

        try:
            if add:
                if role not in member.roles:
                    await member.add_roles(role, reason="Reaction role aggiunto")
            else:
                if role in member.roles:
                    await member.remove_roles(role, reason="Reaction role rimosso")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._apply_role(payload, add=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._apply_role(payload, add=False)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        async with self.config.guild(guild).bindings() as bindings:
            prefix = f"{payload.channel_id}:{payload.message_id}:"
            for key in [key for key in bindings if key.startswith(prefix)]:
                bindings.pop(key, None)

    @commands.group(name="reactionrole", aliases=["reactionroles", "rr"], invoke_without_command=True)
    @commands.guild_only()
    async def reactionrole(self, ctx: commands.Context):
        """Gestisce i reaction role."""
        await ctx.send_help(ctx.command)

    @reactionrole.command(name="add", aliases=["set", "crea"])
    @commands.admin_or_permissions(manage_roles=True)
    async def rr_add(
        self,
        ctx: commands.Context,
        canale: discord.TextChannel,
        message_id: int,
        emoji: str,
        ruolo: discord.Role,
    ):
        """Aggiunge un reaction role.

        Esempio: .rr add #ruoli 123456789012345678 ✅ @Gaming
        """
        if not isinstance(ctx.author, discord.Member):
            return

        if ruolo.is_default() or ruolo.managed:
            return await ctx.send("❌ Quel ruolo non puo essere assegnato automaticamente.")
        if not self._member_can_manage_role(ctx.author, ruolo):
            return await ctx.send("❌ Non puoi configurare un ruolo uguale o superiore al tuo ruolo piu alto.")
        if not self._bot_can_manage_role(ctx.guild, ruolo):
            return await ctx.send("❌ Il mio ruolo deve stare sopra al ruolo da assegnare e devo avere **Gestisci ruoli**.")

        me = ctx.guild.me
        if me is None:
            return await ctx.send("❌ Non riesco a controllare i miei permessi.")
        perms = canale.permissions_for(me)
        if not (perms.view_channel and perms.read_message_history and perms.add_reactions):
            return await ctx.send(
                "❌ In quel canale mi servono **Visualizza canale**, **Leggi cronologia messaggi** e **Aggiungi reazioni**."
            )

        try:
            message = await canale.fetch_message(message_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return await ctx.send("❌ Non trovo quel messaggio in quel canale.")

        try:
            api_emoji = self._emoji_for_api(emoji)
            await message.add_reaction(api_emoji)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException, TypeError):
            return await ctx.send("❌ Non riesco a usare quella reazione. Controlla emoji e permessi.")

        emoji_key = self._emoji_key_from_text(emoji)
        key = self._binding_key(canale.id, message_id, emoji_key)
        async with self.config.guild(ctx.guild).bindings() as bindings:
            bindings[key] = {
                "channel_id": canale.id,
                "message_id": message_id,
                "emoji": emoji.strip(),
                "emoji_key": emoji_key,
                "role_id": ruolo.id,
            }

        await ctx.send(
            f"✅ Reaction role configurato: {emoji} → {ruolo.mention}\n"
            f"Messaggio: {message.jump_url}"
        )

    @reactionrole.command(name="remove", aliases=["del", "delete", "rimuovi"])
    @commands.admin_or_permissions(manage_roles=True)
    async def rr_remove(
        self,
        ctx: commands.Context,
        canale: discord.TextChannel,
        message_id: int,
        emoji: str,
    ):
        """Rimuove un'associazione reaction role."""
        key = self._binding_key(canale.id, message_id, self._emoji_key_from_text(emoji))
        async with self.config.guild(ctx.guild).bindings() as bindings:
            removed = bindings.pop(key, None)

        if removed is None:
            return await ctx.send("❌ Non esiste nessun reaction role con quei dati.")

        await ctx.send("✅ Reaction role rimosso.")

    @reactionrole.command(name="clear", aliases=["pulisci"])
    @commands.admin_or_permissions(manage_roles=True)
    async def rr_clear(self, ctx: commands.Context, canale: discord.TextChannel, message_id: int):
        """Rimuove tutti i reaction role configurati su un messaggio."""
        prefix = f"{canale.id}:{message_id}:"
        removed = 0
        async with self.config.guild(ctx.guild).bindings() as bindings:
            for key in [key for key in bindings if key.startswith(prefix)]:
                bindings.pop(key, None)
                removed += 1

        await ctx.send(f"✅ Rimossi **{removed}** reaction role da quel messaggio.")

    @reactionrole.command(name="list", aliases=["lista"])
    @commands.admin_or_permissions(manage_roles=True)
    async def rr_list(self, ctx: commands.Context):
        """Mostra tutti i reaction role configurati nel server."""
        bindings = await self.config.guild(ctx.guild).bindings()
        if not bindings:
            return await ctx.send("ℹ️ Non ci sono reaction role configurati.")

        lines = []
        for binding in bindings.values():
            if not isinstance(binding, dict):
                continue
            channel_id = int(binding.get("channel_id") or 0)
            message_id = int(binding.get("message_id") or 0)
            role_id = int(binding.get("role_id") or 0)
            emoji = str(binding.get("emoji") or "❓")
            channel = ctx.guild.get_channel(channel_id)
            role = ctx.guild.get_role(role_id)
            channel_text = channel.mention if isinstance(channel, discord.TextChannel) else f"`{channel_id}`"
            role_text = role.mention if role else f"`{role_id}`"
            jump = f"https://discord.com/channels/{ctx.guild.id}/{channel_id}/{message_id}"
            lines.append(f"{emoji} → {role_text} • {channel_text} • [messaggio]({jump})")

        description = "\n".join(lines)
        if len(description) > 3900:
            description = description[:3890] + "\n…"

        await ctx.send(
            embed=discord.Embed(
                title="🎭 Reaction Roles",
                description=description,
                colour=discord.Colour.blurple(),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
