import discord

from .questtracker import QuestTracker as BaseQuestTracker


class QuestTracker(BaseQuestTracker):
    """QuestTracker 4.1.0: embed e menzione nello stesso messaggio."""

    __version__ = "4.1.0"

    async def _send_quest(self, channel, guild, entry, config, *, test=False):
        settings = await self.config.guild(guild).all()
        role = guild.get_role(settings.get("role_id") or 0)

        content = None
        allow_role = False
        if role is not None and settings.get("ping_role", True):
            content = role.mention
            allow_role = True

        kwargs = {
            "content": content,
            "embed": self._build_embed(entry, config, test=test),
            "allowed_mentions": discord.AllowedMentions(
                roles=allow_role,
                users=False,
                everyone=False,
            ),
        }

        # In anteprima la menzione resta reale/visibile nello stesso messaggio,
        # ma Discord prova a sopprimere la notifica push se la versione della
        # libreria supporta il flag silent.
        if test:
            try:
                await channel.send(**kwargs, silent=True)
                return
            except TypeError:
                pass

        await channel.send(**kwargs)
