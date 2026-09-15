import re
from typing import Any, Dict, Optional

import discord
from redbot.core import commands

from .v44 import QuestTracker as BaseQuestTracker


DEFAULT_MESSAGE_TEMPLATE = "{role} {link}"
ALLOWED_PLACEHOLDERS = {"role", "link"}
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


# Sostituiamo il vecchio `.quest messaggio`, che nelle versioni precedenti
# dichiarava il layout fisso, mantenendo lo stesso nome e gli stessi alias.
for _command_name in ("messaggio", "message", "testo"):
    BaseQuestTracker.quest.remove_command(_command_name)


class QuestTracker(BaseQuestTracker):
    """QuestTracker 4.5.0: messaggio Quest personalizzabile con due placeholder."""

    __version__ = "4.5.0"

    def __init__(self, bot):
        super().__init__(bot)
        # Stesso Config identifier storico. Aggiungiamo solo una nuova chiave,
        # quindi canale, ruolo, Quest viste e tutte le vecchie impostazioni restano intatte.
        self.config.register_guild(message_template=DEFAULT_MESSAGE_TEMPLATE)

    @staticmethod
    def _unknown_placeholders(template: str):
        return sorted(
            {
                name
                for name in PLACEHOLDER_RE.findall(template)
                if name not in ALLOWED_PLACEHOLDERS
            }
        )

    @staticmethod
    def _render_message(template: str, role_text: str, link_text: str) -> str:
        return (
            str(template)
            .replace("{role}", role_text)
            .replace("{link}", link_text)
            .strip()
        )

    async def _send_quest(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> None:
        url = self._quest_share_url(entry, config)
        if url is None:
            return

        settings = await self.config.guild(guild).all()
        template = str(settings.get("message_template") or DEFAULT_MESSAGE_TEMPLATE)
        role = guild.get_role(settings.get("role_id") or 0)

        # `{link}` mantiene il link pulito come richiesto: un singolo punto
        # cliccabile che punta al vero URL /quests/<id> e permette a Discord
        # di generare la sua card Quest nativa.
        link_text = f"[.]({url})"

        show_role = role is not None and settings.get("ping_role", True)
        role_text = role.mention if show_role else ""
        content = self._render_message(template, role_text, link_text)

        # Protezione nel caso in cui in Config finisca manualmente un template
        # vuoto o senza link: la Quest deve comunque restare apribile.
        if "{link}" not in template or not content:
            content = self._render_message(DEFAULT_MESSAGE_TEMPLATE, role_text, link_text)

        allow_role = bool(show_role and not test)
        kwargs = {
            "content": content[:2000],
            "allowed_mentions": discord.AllowedMentions(
                roles=allow_role,
                users=False,
                everyone=False,
            ),
        }

        if test:
            try:
                await channel.send(**kwargs, silent=True)
                return
            except TypeError:
                pass

        await channel.send(**kwargs)

    @BaseQuestTracker.quest.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_message(self, ctx: commands.Context, *, testo: Optional[str] = None):
        """Visualizza o modifica il testo inviato quando viene trovata una Quest."""
        conf = self.config.guild(ctx.guild)

        if testo is None:
            template = await conf.message_template()
            return await ctx.send(
                "**Messaggio Quest attuale:**\n"
                f"```\n{template}\n```\n"
                "Placeholder disponibili:\n"
                "`{role}` = ruolo Quest configurato\n"
                "`{link}` = link Quest Discord mascherato come `.`\n\n"
                "Esempio:\n"
                "`.quest messaggio 🔔 Nuova missione per {role}! {link}`\n"
                "Per tornare al predefinito: `.quest messaggio reset`"
            )

        template = testo.strip()
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE_TEMPLATE)
            return await ctx.send(
                "✅ Messaggio Quest ripristinato a `{role} {link}`."
            )

        if not template:
            return await ctx.send("❌ Il messaggio non puo essere vuoto.")
        if len(template) > 1800:
            return await ctx.send("❌ Il messaggio puo avere massimo 1800 caratteri.")

        unknown = self._unknown_placeholders(template)
        if unknown:
            return await ctx.send(
                "❌ Placeholder non validi: "
                + ", ".join(f"`{{{name}}}`" for name in unknown)
                + ". Sono disponibili solo `{role}` e `{link}`."
            )

        if "{link}" not in template:
            return await ctx.send(
                "❌ Devi inserire `{link}` nel messaggio, altrimenti Discord non puo mostrare/aprire la Quest."
            )

        await conf.message_template.set(template)
        await ctx.send(
            "✅ Messaggio Quest aggiornato. Usa `.quest test` per vedere l'anteprima."
        )
