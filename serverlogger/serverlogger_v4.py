import discord
from redbot.core import commands

from .serverlogger_v3 import ServerLogger as BaseServerLogger


CATEGORY_META = {
    "messages": ("Messaggi", "Eliminazioni, modifiche e bulk delete"),
    "voice": ("Vocali", "Ingressi, uscite, spostamenti, mute/deafen, stream e webcam"),
    "members": ("Membri", "Ingressi, uscite volontarie e nickname"),
    "moderation": ("Moderazione", "Ban, unban, kick e timeout"),
    "roles": ("Ruoli", "Ruoli creati/modificati/eliminati e assegnati/rimossi"),
    "channels": ("Canali", "Canali creati, modificati ed eliminati"),
    "threads": ("Thread", "Thread creati, modificati ed eliminati"),
    "server": ("Server", "Modifiche alle impostazioni generali del server"),
    "media": ("Emoji e sticker", "Emoji e sticker creati, modificati ed eliminati"),
    "invites": ("Inviti", "Inviti creati ed eliminati"),
    "webhooks": ("Webhook", "Webhook creati, modificati ed eliminati"),
    "events": ("Eventi", "Eventi programmati creati, modificati ed eliminati"),
    "other": ("Altro", "Eventi futuri o non classificati nelle categorie precedenti"),
}

DEFAULT_CATEGORIES = {key: True for key in CATEGORY_META}


class LogCategorySelect(discord.ui.Select):
    def __init__(self, view: "LogSettingsView"):
        self.settings_view = view
        options = []
        for key, (label, description) in CATEGORY_META.items():
            options.append(
                discord.SelectOption(
                    label=label,
                    value=key,
                    description=description[:100],
                    default=bool(view.staged.get(key, True)),
                )
            )
        super().__init__(
            placeholder="Spunta le categorie da registrare",
            min_values=0,
            max_values=len(options),
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        if not await self.settings_view.check_owner(interaction):
            return
        selected = set(self.values)
        self.settings_view.staged = {key: key in selected for key in CATEGORY_META}
        await interaction.response.edit_message(
            embed=self.settings_view.cog._settings_embed(self.settings_view.staged, saved=False),
            view=self.settings_view.rebuild(),
        )


class LogSettingsView(discord.ui.View):
    def __init__(self, cog: "ServerLogger", guild: discord.Guild, owner_id: int, staged):
        super().__init__(timeout=300)
        self.cog = cog
        self.guild = guild
        self.owner_id = owner_id
        self.staged = dict(staged)
        self.message = None
        self.add_item(LogCategorySelect(self))

    def rebuild(self):
        self.clear_items()
        self.add_item(LogCategorySelect(self))
        self.add_item(self.save_button)
        self.add_item(self.enable_all_button)
        self.add_item(self.disable_all_button)
        self.add_item(self.cancel_button)
        return self

    async def check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                "Questo pannello impostazioni non e tuo. Usa `.log settings` per aprirne uno personale.",
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @discord.ui.button(label="Salva", style=discord.ButtonStyle.success, row=1)
    async def save_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction):
            return
        await self.cog.config.guild(self.guild).log_categories.set(dict(self.staged))
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.cog._settings_embed(self.staged, saved=True),
            view=self,
        )
        self.stop()

    @discord.ui.button(label="Abilita tutto", style=discord.ButtonStyle.primary, row=1)
    async def enable_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction):
            return
        self.staged = {key: True for key in CATEGORY_META}
        await interaction.response.edit_message(
            embed=self.cog._settings_embed(self.staged, saved=False),
            view=self.rebuild(),
        )

    @discord.ui.button(label="Disabilita tutto", style=discord.ButtonStyle.secondary, row=1)
    async def disable_all_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction):
            return
        self.staged = {key: False for key in CATEGORY_META}
        await interaction.response.edit_message(
            embed=self.cog._settings_embed(self.staged, saved=False),
            view=self.rebuild(),
        )

    @discord.ui.button(label="Annulla", style=discord.ButtonStyle.danger, row=1)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self.check_owner(interaction):
            return
        current = await self.cog._get_category_settings(self.guild)
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=self.cog._settings_embed(current, saved=True, cancelled=True),
            view=self,
        )
        self.stop()


class ServerLogger(BaseServerLogger):
    """ServerLogger v1.7: pannello interattivo per scegliere cosa loggare."""

    __version__ = "1.7.1"

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(log_categories=DEFAULT_CATEGORIES)

    async def _get_category_settings(self, guild: discord.Guild):
        stored = await self.config.guild(guild).log_categories()
        merged = dict(DEFAULT_CATEGORIES)
        if isinstance(stored, dict):
            for key in merged:
                if key in stored:
                    merged[key] = bool(stored[key])
        if stored != merged:
            await self.config.guild(guild).log_categories.set(merged)
        return merged

    @staticmethod
    def _category_for_action(action: str) -> str:
        text = str(action or "").casefold()

        if any(word in text for word in ("messaggio", "eliminazione massiva")):
            return "messages"
        if any(word in text for word in (
            "vocale", "server mute", "server unmute", "server deafen", "server undeafen",
            "self mute", "self unmute", "self deafen", "self undeafen", "streaming", "webcam",
        )):
            return "voice"
        if any(word in text for word in ("ban", "unban", "kick", "timeout")):
            return "moderation"
        if "ruolo" in text:
            return "roles"
        if "thread" in text:
            return "threads"
        if "canale" in text:
            return "channels"
        if any(word in text for word in ("emoji", "sticker")):
            return "media"
        if "invito" in text:
            return "invites"
        if "webhook" in text:
            return "webhooks"
        if "evento programmato" in text:
            return "events"
        if text == "server modificato":
            return "server"
        if any(word in text for word in ("ingresso nel server", "uscita dal server", "nickname modificato")):
            return "members"
        return "other"

    def _settings_embed(self, settings, *, saved=False, cancelled=False):
        lines = []
        for key, (label, description) in CATEGORY_META.items():
            mark = "✅" if settings.get(key, True) else "⬜"
            lines.append(f"{mark} **{label}** — {description}")

        if cancelled:
            footer = "Modifiche annullate. Le impostazioni salvate non sono cambiate."
        elif saved:
            footer = "Impostazioni salvate."
        else:
            footer = "Modifiche non ancora salvate: usa il menu e poi premi Salva."

        embed = discord.Embed(
            title="Impostazioni ServerLogger",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        embed.set_footer(text=footer)
        return embed

    async def _emit(
        self,
        guild,
        action,
        *,
        staffer=None,
        user=None,
        channel=None,
        details=None,
        when=None,
    ):
        if guild is None:
            return
        settings = await self._get_category_settings(guild)
        category = self._category_for_action(action)
        if not settings.get(category, True):
            return
        await super()._emit(
            guild,
            action,
            staffer=staffer,
            user=user,
            channel=channel,
            details=details,
            when=when,
        )

    @BaseServerLogger.log_group.command(name="settings", aliases=["setting"])
    @commands.admin_or_permissions(manage_guild=True)
    async def log_settings(self, ctx: commands.Context):
        """Apre un pannello interattivo per scegliere quali categorie di eventi registrare."""
        settings = await self._get_category_settings(ctx.guild)
        view = LogSettingsView(self, ctx.guild, ctx.author.id, settings)
        view.rebuild()
        message = await ctx.send(embed=self._settings_embed(settings, saved=True), view=view)
        view.message = message
