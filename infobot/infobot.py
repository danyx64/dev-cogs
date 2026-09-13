import discord
from redbot.core import Config, commands


class InfoBot(commands.Cog):
    """Owner-only information about guild and known user installations."""

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=734920184, force_registration=True)
        self.config.register_global(user_installs={})

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        installs = await self.config.user_installs()
        key = str(user_id)
        if key in installs:
            del installs[key]
            await self.config.user_installs.set(installs)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Remember user-install owners when Discord exposes that information."""
        owners = getattr(interaction, "authorizing_integration_owners", None)
        if not owners:
            return

        user_id = None
        if isinstance(owners, dict):
            user_id = owners.get(1) or owners.get("1")
        else:
            user_id = getattr(owners, "user_id", None)

        if not user_id or interaction.user is None:
            return

        user = interaction.user
        installs = await self.config.user_installs()
        installs[str(user_id)] = {
            "name": getattr(user, "global_name", None) or user.name,
            "username": user.name,
        }
        await self.config.user_installs.set(installs)

    async def _application_counts(self):
        guild_count = None
        user_count = None
        try:
            app = await self.bot.application_info()
            guild_count = getattr(app, "approximate_guild_count", None)
            user_count = getattr(app, "approximate_user_install_count", None)
        except (discord.HTTPException, AttributeError):
            pass
        return guild_count, user_count

    @commands.command(name="infobot")
    @commands.is_owner()
    async def infobot(self, ctx: commands.Context):
        """Show real guilds and user installs known to the bot."""
        guilds = sorted(self.bot.guilds, key=lambda g: g.name.lower())
        installs = await self.config.user_installs()
        approx_guilds, approx_users = await self._application_counts()

        lines = ["**INFO BOT**", ""]
        lines.append(f"**Guild reali viste dal bot:** `{len(guilds)}`")
        if approx_guilds is not None:
            lines.append(f"**Guild approssimative Discord:** `{approx_guilds}`")
        if approx_users is not None:
            lines.append(f"**User installs approssimative Discord:** `{approx_users}`")

        lines.extend(["", "**SERVER**"])
        if guilds:
            for guild in guilds:
                owner = guild.owner
                owner_text = f"{owner} (`{owner.id}`)" if owner else "Sconosciuto"
                lines.append(
                    f"- **{guild.name}** | `{guild.id}` | "
                    f"{guild.member_count or 0} membri | Owner: {owner_text}"
                )
        else:
            lines.append("- Nessun server.")

        lines.extend(["", "**USER INSTALL CONOSCIUTI**"])
        if installs:
            ordered = sorted(installs.items(), key=lambda item: item[1].get("name", "").lower())
            for user_id, entry in ordered:
                name = entry.get("name") or entry.get("username") or "Sconosciuto"
                username = entry.get("username") or name
                if username != name:
                    lines.append(f"- **{name}** (@{username}) | `{user_id}`")
                else:
                    lines.append(f"- **{name}** | `{user_id}`")
        else:
            lines.append("- Nessun user install ancora rilevato.")

        lines.extend([
            "",
            "_Discord non fornisce al bot una lista globale con nome e ID di tutti gli utenti che hanno installato l'app. Questa sezione mostra solo gli User Install che Discord espone al bot durante le interazioni._",
        ])

        chunks = []
        current = ""
        for line in lines:
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) > 1900:
                chunks.append(current)
                current = line
            else:
                current = candidate
        if current:
            chunks.append(current)

        for chunk in chunks:
            await ctx.send(chunk)
