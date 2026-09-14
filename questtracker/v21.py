import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import discord
from redbot.core import commands

from .questtracker import SafeFormatDict, _parse_iso, _task_items
from .template_placeholders import EXTRA_PLACEHOLDERS
from .v2 import (
    DEFAULT_EMBED_DESCRIPTION,
    DEFAULT_EMBED_FOOTER,
    DEFAULT_EMBED_TITLE,
    PLACEHOLDERS,
    QuestTracker as QuestTrackerV2,
)


TEMPLATE_FILE = "embed_template.txt"
SECTIONS = ("TITLE", "DESCRIPTION", "FOOTER", "COLOR", "URL", "IMAGE", "THUMBNAIL")
DEFAULT_FILE_SECTIONS = {
    "TITLE": "{test_prefix}Nuova Quest - {title}",
    "DESCRIPTION": DEFAULT_EMBED_DESCRIPTION,
    "FOOTER": DEFAULT_EMBED_FOOTER,
    "COLOR": "#5865F2",
    "URL": "{cta_url}",
    "IMAGE": "{hero_url}",
    "THUMBNAIL": "{reward_image_url}",
}


class QuestTracker(QuestTrackerV2):
    __version__ = "3.0.0"

    def __init__(self, bot):
        super().__init__(bot)
        self.template_path = Path(__file__).resolve().parent / TEMPLATE_FILE

    def _read_file_template(self) -> Dict[str, str]:
        result = dict(DEFAULT_FILE_SECTIONS)
        try:
            raw = self.template_path.read_text(encoding="utf-8")
        except OSError:
            return result
        current = None
        parsed: Dict[str, List[str]] = {}
        for line in raw.splitlines():
            match = re.fullmatch(r"\s*\[([A-Za-z_]+)\]\s*", line)
            if match:
                name = match.group(1).upper()
                current = name if name in SECTIONS else None
                if current:
                    parsed.setdefault(current, [])
                continue
            if current:
                parsed[current].append(line)
        for name, lines in parsed.items():
            result[name] = "\n".join(lines).strip("\n")
        return result

    def _write_file_template(self, sections: Dict[str, str]) -> bool:
        header = (
            "# QuestTracker embed template.\n"
            "# Sezioni: TITLE, DESCRIPTION, FOOTER, COLOR, URL, IMAGE, THUMBNAIL.\n"
            "# Usa .quest placeholders per vedere le variabili.\n\n"
        )
        text = header + "\n\n".join(f"[{name}]\n{sections.get(name, '')}" for name in SECTIONS) + "\n"
        try:
            self.template_path.write_text(text, encoding="utf-8")
            return True
        except OSError:
            return False

    def _set_file_section(self, name: str, value: str) -> bool:
        sections = self._read_file_template()
        sections[name] = value
        return self._write_file_template(sections)

    def _extended_payload(
        self,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        settings: Dict[str, Any],
        *,
        test: bool = False,
    ) -> SafeFormatDict:
        values = super()._extended_payload(guild, entry, config, settings, test=test)
        values["video_line"] = values.get("video_link", "")
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        title = str(values.get("quest") or "Discord Quest")
        end = _parse_iso(config.get("expires_at"))
        start = _parse_iso(config.get("starts_at"))
        seconds_left = max(0, int((end - datetime.now(timezone.utc)).total_seconds())) if end else 0
        targets = {key: "" if target is None else str(target) for key, target in _task_items(config)}
        member = guild.me
        values.update(
            {
                "title": title,
                "quest_title": title,
                "name": title,
                "description": str(messages.get("quest_description") or messages.get("description") or ""),
                "quest_description": str(messages.get("quest_description") or messages.get("description") or ""),
                "game_title": str(messages.get("game_title") or values.get("game") or ""),
                "application": str(app.get("name") or values.get("game") or ""),
                "app_id": str(values.get("application_id") or ""),
                "orbs": str(values.get("orb_amount") or ""),
                "orb_quantity": str(values.get("orb_amount") or ""),
                "role": str(values.get("mention") or ""),
                "server_name": guild.name,
                "server_id": str(guild.id),
                "guild_name": guild.name,
                "guild_id": str(guild.id),
                "bot_name": member.display_name if member else self.bot.user.name,
                "start_unix": str(int(start.timestamp())) if start else "",
                "end_unix": str(int(end.timestamp())) if end else "",
                "remaining": f"{seconds_left // 86400} giorni e {(seconds_left % 86400) // 3600} ore" if end else "",
                "remaining_days": str(seconds_left // 86400) if end else "",
                "remaining_hours": str(seconds_left // 3600) if end else "",
                "watch_video_seconds": targets.get("WATCH_VIDEO", ""),
                "watch_video_desktop_seconds": targets.get("WATCH_VIDEO_ON_DESKTOP", ""),
                "watch_video_mobile_seconds": targets.get("WATCH_VIDEO_ON_MOBILE", ""),
                "play_desktop_seconds": targets.get("PLAY_ON_DESKTOP", ""),
                "play_mobile_seconds": targets.get("PLAY_ON_MOBILE", ""),
                "stream_desktop_seconds": targets.get("STREAM_ON_DESKTOP", ""),
                "task_types": ", ".join(targets),
                "now_timestamp": f"<t:{int(datetime.now(timezone.utc).timestamp())}:f>",
                "is_test": "true" if test else "false",
            }
        )
        return values

    @staticmethod
    def _colour(value: str) -> discord.Colour:
        raw = value.strip().lstrip("#")
        try:
            return discord.Colour(int(raw, 16)) if re.fullmatch(r"[0-9A-Fa-f]{6}", raw) else discord.Colour.blurple()
        except ValueError:
            return discord.Colour.blurple()

    async def _build_embed_for_guild(self, guild, entry, config, *, test=False):
        settings = await self.config.guild(guild).all()
        values = self._extended_payload(guild, entry, config, settings, test=test)
        sections = self._read_file_template()
        title = self._format(sections["TITLE"], values, DEFAULT_FILE_SECTIONS["TITLE"])[:256]
        description = self._format(sections["DESCRIPTION"], values, DEFAULT_FILE_SECTIONS["DESCRIPTION"])[:4096]
        footer = self._format(sections["FOOTER"], values, DEFAULT_FILE_SECTIONS["FOOTER"])[:2048]
        url = self._format(sections["URL"], values, "")
        image = self._format(sections["IMAGE"], values, "")
        thumbnail = self._format(sections["THUMBNAIL"], values, "")
        embed = discord.Embed(
            title=title or None,
            description=description or None,
            url=url if url.startswith(("http://", "https://")) else None,
            colour=self._colour(self._format(sections["COLOR"], values, "#5865F2")),
        )
        if image.startswith(("http://", "https://")):
            embed.set_image(url=image)
        if thumbnail.startswith(("http://", "https://")):
            embed.set_thumbnail(url=thumbnail)
        if footer:
            embed.set_footer(text=footer)
        return embed, settings, values

    async def _send_quest_embed(self, channel, guild, entry, config, *, test=False):
        embed, settings, values = await self._build_embed_for_guild(guild, entry, config, test=test)
        role = guild.get_role(settings.get("role_id") or 0)
        rendered = "\n".join((embed.title or "", embed.description or "", embed.footer.text if embed.footer else ""))
        ping = not test and role and settings.get("ping_role", True) and role.mention in rendered and values.get("mention")
        if not ping:
            return await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        message = await channel.send(
            content=role.mention,
            embed=embed,
            allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
        )
        try:
            await message.edit(content=None, allowed_mentions=discord.AllowedMentions.none())
        except (discord.Forbidden, discord.HTTPException):
            pass
        return message

    async def command_message(self, ctx: commands.Context, testo: Optional[str] = None):
        sections = self._read_file_template()
        if testo is None:
            return await ctx.send(f"**DESCRIPTION in `questtracker/{TEMPLATE_FILE}`:**\n```\n{sections['DESCRIPTION'][:1700]}\n```")
        value = DEFAULT_FILE_SECTIONS["DESCRIPTION"] if testo.strip().lower() == "reset" else testo.replace("\\n", "\n")
        if not self._set_file_section("DESCRIPTION", value):
            return await ctx.send("❌ Non riesco a scrivere il file template.")
        await ctx.send("✅ DESCRIPTION aggiornata nel file template.")

    async def command_title(self, ctx: commands.Context, testo: Optional[str] = None):
        sections = self._read_file_template()
        if testo is None:
            return await ctx.send(f"**TITLE:**\n```\n{sections['TITLE']}\n```")
        value = DEFAULT_FILE_SECTIONS["TITLE"] if testo.strip().lower() == "reset" else testo.replace("\\n", "\n")
        if not self._set_file_section("TITLE", value):
            return await ctx.send("❌ Non riesco a scrivere il file template.")
        await ctx.send("✅ TITLE aggiornato nel file template.")

    async def command_footer(self, ctx: commands.Context, testo: Optional[str] = None):
        sections = self._read_file_template()
        if testo is None:
            return await ctx.send(f"**FOOTER:**\n```\n{sections['FOOTER']}\n```")
        value = DEFAULT_FILE_SECTIONS["FOOTER"] if testo.strip().lower() == "reset" else testo.replace("\\n", "\n")
        if not self._set_file_section("FOOTER", value):
            return await ctx.send("❌ Non riesco a scrivere il file template.")
        await ctx.send("✅ FOOTER aggiornato nel file template.")

    async def command_placeholders(self, ctx: commands.Context):
        placeholders = dict(PLACEHOLDERS)
        placeholders.update(EXTRA_PLACEHOLDERS)
        placeholders.update({
            "quest_title": "alias del titolo Quest", "name": "alias del titolo Quest",
            "quest_description": "descrizione Quest", "game_title": "titolo gioco",
            "role": "alias di {mention}", "guild_name": "nome server", "guild_id": "ID server",
            "bot_name": "nome bot", "orb_quantity": "quantita Orbs",
            "remaining_days": "giorni rimanenti", "remaining_hours": "ore rimanenti",
            "watch_video_seconds": "secondi video", "watch_video_desktop_seconds": "secondi video desktop",
            "watch_video_mobile_seconds": "secondi video mobile", "play_desktop_seconds": "target desktop",
            "play_mobile_seconds": "target mobile", "stream_desktop_seconds": "target streaming",
            "task_types": "tipi di task", "now_timestamp": "timestamp invio", "is_test": "stato test"
        })
        lines = [f"`{{{name}}}` — {desc}" for name, desc in placeholders.items()]
        chunk = ""
        for line in lines:
            if len(chunk) + len(line) + 1 > 1800:
                await ctx.send("**Placeholder QuestTracker**\n" + chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            await ctx.send("**Placeholder QuestTracker**\n" + chunk)
