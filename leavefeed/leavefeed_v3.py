from zoneinfo import ZoneInfo

import discord
from redbot.core import commands

from .leavefeed import LeaveFeed as BaseLeaveFeed, LeaveFeedView


ITALY_TZ = ZoneInfo("Europe/Rome")


class LeaveFeed(BaseLeaveFeed):
    """LeaveFeed v2.1: placeholder avanzati e guida integrata per i messaggi."""

    __version__ = "2.1.1"

    @staticmethod
    def _render_message(template: str, user, guild: discord.Guild) -> str:
        now = discord.utils.utcnow().astimezone(ITALY_TZ)
        display_name = getattr(user, "display_name", None) or getattr(user, "name", str(user))
        username = getattr(user, "name", str(user))
        user_id = getattr(user, "id", "")
        created_at = getattr(user, "created_at", None)
        joined_at = getattr(user, "joined_at", None)
        avatar = getattr(getattr(user, "display_avatar", None), "url", "")

        values = {
            "{user}": f"<@{user_id}>" if user_id else username,
            "{user_mention}": f"<@{user_id}>" if user_id else username,
            "{username}": username,
            "{displayname}": display_name,
            "{user_tag}": str(user),
            "{user_id}": str(user_id),
            "{user_avatar}": str(avatar),
            "{server}": guild.name,
            "{server_id}": str(guild.id),
            "{member_count}": str(guild.member_count or 0),
            "{date}": now.strftime("%d/%m/%Y"),
            "{time}": now.strftime("%H:%M:%S"),
            "{datetime}": now.strftime("%d/%m/%Y %H:%M:%S"),
            "{account_created}": created_at.astimezone(ITALY_TZ).strftime("%d/%m/%Y %H:%M:%S") if created_at else "—",
            "{joined_at}": joined_at.astimezone(ITALY_TZ).strftime("%d/%m/%Y %H:%M:%S") if joined_at else "—",
        }

        text = str(template)
        for placeholder, value in values.items():
            text = text.replace(placeholder, value)
        return text

    async def _send_leave_dm(self, user, guild: discord.Guild) -> bool:
        data = await self._ensure_schema(guild)

        # Il test viene eseguito su un membro ancora presente nel server.
        # L'invio automatico avviene invece dopo on_member_remove, quando il
        # membro non è più nella cache della guild. In questo modo `.leavefeed
        # test` funziona anche da disattivato senza ignorare enable/disable per
        # le vere uscite.
        user_id = getattr(user, "id", None)
        is_test_target = bool(user_id and guild.get_member(user_id) is not None)
        if not data.get("enabled") and not is_test_target:
            return False

        message_key = data.get("active_message")
        message_data = data["messages"].get(message_key)
        if not message_data:
            return False

        content = self._render_message(message_data.get("content") or "", user, guild)
        view = None
        if message_data.get("buttons"):
            view = LeaveFeedView(self, guild.id, message_key, message_data, data["buttons"])

        try:
            await user.send(
                content=content or None,
                view=view,
                allowed_mentions=discord.AllowedMentions(
                    users=True,
                    roles=False,
                    everyone=False,
                    replied_user=False,
                ),
            )
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False

    @BaseLeaveFeed.message_group.command(name="usage")
    @commands.admin_or_permissions(manage_guild=True)
    async def message_usage(self, ctx: commands.Context):
        """Mostra tutti i placeholder disponibili nei messaggi LeaveFeed."""
        await ctx.send(
            "**Placeholder disponibili nei messaggi LeaveFeed**\n"
            "`{user}` — menzione cliccabile dell'utente (`@Utente`)\n"
            "`{user_mention}` — stessa menzione cliccabile dell'utente\n"
            "`{username}` — username dell'utente\n"
            "`{displayname}` — nome visualizzato nel server\n"
            "`{user_tag}` — nome Discord completo disponibile\n"
            "`{user_id}` — ID Discord dell'utente\n"
            "`{user_avatar}` — URL dell'avatar dell'utente\n"
            "`{server}` — nome del server\n"
            "`{server_id}` — ID del server\n"
            "`{member_count}` — numero membri del server\n"
            "`{date}` — data italiana, es. `20/08/2026`\n"
            "`{time}` — ora italiana, es. `01:45:30`\n"
            "`{datetime}` — data e ora insieme\n"
            "`{account_created}` — data/ora creazione account Discord\n"
            "`{joined_at}` — data/ora ingresso nel server, se disponibile\n\n"
            "**Esempio**\n"
            "`.leavefeed message add uscita Ciao {user}, ci dispiace che tu abbia lasciato {server}. Data: {date} Ora: {time}`"
        )
