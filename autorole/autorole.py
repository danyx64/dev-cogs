from __future__ import annotations

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class AutoRole(commands.Cog):
    """Assegna automaticamente un ruolo ai nuovi membri."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=60231495872146380, force_registration=True)
        self.config.register_guild(enabled=False, role_id=None)

    @staticmethod
    def _bot_can_manage_role(guild: discord.Guild, role: discord.Role) -> bool:
        me = guild.me
        if me is None:
            return False
        return (
            me.guild_permissions.manage_roles
            and not role.managed
            and not role.is_default()
            and me.top_role > role
        )

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        settings = await self.config.guild(member.guild).all()
        if not settings.get("enabled"):
            return

        role = member.guild.get_role(int(settings.get("role_id") or 0))
        if role is None:
            return
        if not self._bot_can_manage_role(member.guild, role):
            return

        try:
            await member.add_roles(role, reason="AutoRole: nuovo membro")
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.group(name="autorole", invoke_without_command=True)
    @commands.guild_only()
    async def autorole(self, ctx: commands.Context):
        """Configura il ruolo automatico per i nuovi membri."""
        await ctx.send_help(ctx.command)

    @autorole.command(name="set", aliases=["role", "ruolo"])
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole_set(self, ctx: commands.Context, role: discord.Role):
        """Imposta il ruolo assegnato automaticamente ai nuovi membri."""
        if role.is_default() or role.managed:
            return await ctx.send("❌ Quel ruolo non puo essere assegnato automaticamente.")

        if not self._bot_can_manage_role(ctx.guild, role):
            return await ctx.send(
                "❌ Mi serve **Gestisci ruoli** e il mio ruolo deve stare sopra al ruolo da assegnare."
            )

        if isinstance(ctx.author, discord.Member):
            if not ctx.author.guild_permissions.administrator and ctx.author.top_role <= role:
                return await ctx.send("❌ Non puoi configurare un ruolo uguale o superiore al tuo ruolo piu alto.")

        conf = self.config.guild(ctx.guild)
        await conf.role_id.set(role.id)
        await conf.enabled.set(True)
        await ctx.send(f"✅ AutoRole attivo: i nuovi membri riceveranno {role.mention}.")

    @autorole.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole_on(self, ctx: commands.Context):
        role_id = await self.config.guild(ctx.guild).role_id()
        role = ctx.guild.get_role(role_id or 0)
        if role is None:
            return await ctx.send("❌ Prima imposta un ruolo con `.autorole set @Ruolo`.")
        if not self._bot_can_manage_role(ctx.guild, role):
            return await ctx.send(
                "❌ Non posso gestire quel ruolo. Controlla gerarchia e permesso **Gestisci ruoli**."
            )
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send(f"✅ AutoRole attivato con {role.mention}.")

    @autorole.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole_off(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ AutoRole disattivato. Il ruolo configurato resta salvato.")

    @autorole.command(name="clear", aliases=["reset", "rimuovi"])
    @commands.admin_or_permissions(manage_roles=True)
    async def autorole_clear(self, ctx: commands.Context):
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(False)
        await conf.role_id.set(None)
        await ctx.send("✅ Ruolo automatico rimosso e AutoRole disattivato.")

    @autorole.command(name="status")
    async def autorole_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        role = ctx.guild.get_role(int(settings.get("role_id") or 0))
        state = "✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato"
        role_text = role.mention if role else "Non configurato"
        await ctx.send(
            f"**AutoRole**\nStato: {state}\nRuolo: {role_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @autorole.command(name="version", aliases=["versione"])
    async def autorole_version(self, ctx: commands.Context):
        await ctx.send(f"AutoRole **v{self.__version__}**")
