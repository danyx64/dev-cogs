import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import discord
from redbot.core import commands

from .questtracker import (
    DEFAULT_TEMPLATE as LEGACY_DEFAULT_TEMPLATE,
    QuestTracker as BaseQuestTracker,
    SafeFormatDict,
    _cdn_url,
    _hero_image,
    _parse_iso,
    _platforms,
    _reward_image,
    _task_items,
    _task_text,
)


DEFAULT_EMBED_TITLE = "{test_prefix}Nuova Quest - {quest}"
DEFAULT_EMBED_BODY = (
    "📋 **Informazioni Quest**\n"
    "**Nome:** {quest}\n"
    "**Durata:** {start} - {end}\n"
    "**Piattaforme:** {platforms}\n"
    "**Gioco/App:** {game} (`{application_id}`)\n\n"
    "✅ **Obiettivi**\n"
    "{tasks}\n\n"
    "🎁 **Ricompense**\n"
    "**Tipo:** {reward_type}\n"
    "**Nome:** {reward}\n"
    "{orb_line}\n"
    "{sku_line}\n"
    "{video_line}"
)
DEFAULT_EMBED_DESCRIPTION = "{mention}\n\n" + DEFAULT_EMBED_BODY
DEFAULT_EMBED_FOOTER = "Quest ID: {quest_id}"
TEMPLATE_VERSION = 2

PLACEHOLDERS = {
    "mention": "menzione del ruolo configurato",
    "role_mention": "menzione del ruolo configurato",
    "role_name": "nome del ruolo configurato",
    "role_id": "ID del ruolo configurato",
    "quest": "nome della Quest",
    "quest_name": "nome della Quest",
    "quest_id": "ID della Quest",
    "game": "nome del gioco/app",
    "game_name": "nome del gioco/app",
    "application_name": "nome dell'applicazione",
    "application_id": "ID applicazione Discord",
    "start": "data inizio in formato GG/MM/AA",
    "start_date": "data inizio in formato GG/MM/AA",
    "end": "data fine in formato GG/MM/AA",
    "end_date": "data fine in formato GG/MM/AA",
    "start_iso": "data/ora inizio ISO",
    "end_iso": "data/ora fine ISO",
    "start_timestamp": "timestamp Discord completo dell'inizio",
    "end_timestamp": "timestamp Discord completo della fine",
    "start_relative": "timestamp Discord relativo dell'inizio",
    "end_relative": "timestamp Discord relativo della fine",
    "duration": "durata complessiva della Quest",
    "duration_days": "durata in giorni interi",
    "duration_hours": "durata totale in ore intere",
    "platforms": "piattaforme richieste",
    "tasks": "elenco obiettivi già formattato",
    "objectives": "alias di {tasks}",
    "task_count": "numero degli obiettivi",
    "video_seconds": "secondi richiesti per il task video",
    "video_duration": "durata del task video già formattata",
    "video_url": "URL del video della Quest se disponibile",
    "video_link": "link Markdown al video se disponibile",
    "reward": "nome/valore della ricompensa",
    "reward_name": "nome/valore della ricompensa",
    "reward_type": "tipo di ricompensa",
    "reward_count": "numero di ricompense presenti",
    "orb_amount": "quantità di Orbs, vuoto se non applicabile",
    "orb_line": "riga Orb Amount già formattata oppure vuota",
    "sku": "SKU ID",
    "sku_id": "SKU ID",
    "sku_line": "riga SKU già formattata oppure vuota",
    "reward_asset_url": "URL asset grezzo della ricompensa",
    "hero_url": "URL immagine principale",
    "image_url": "alias di {hero_url}",
    "reward_image_url": "URL thumbnail ricompensa",
    "thumbnail_url": "alias di {reward_image_url}",
    "cta_url": "URL CTA/store della Quest",
    "link": "alias di {cta_url}",
    "features": "feature della Quest separate da virgole",
    "features_count": "numero feature",
    "test_prefix": "TEST • durante .quest test, altrimenti vuoto",
}


def _first_reward(config: Dict[str, Any]) -> Dict[str, Any]:
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    if not rewards or not isinstance(rewards[0], dict):
        return {}
    return rewards[0]


def _rewards(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    return [item for item in rewards if isinstance(item, dict)]


def _video_asset_url(quest_id: str, config: Dict[str, Any]) -> str:
    reward = _first_reward(config)
    reward_asset = reward.get("asset")
    if isinstance(reward_asset, str) and reward_asset.lower().split("?", 1)[0].endswith((".mp4", ".webm", ".mov")):
        return _cdn_url(quest_id, reward_asset) or ""

    assets = config.get("assets") or {}
    if isinstance(assets, dict):
        preferred: List[Tuple[int, str]] = []
        for key, value in assets.items():
            if not isinstance(value, str):
                continue
            low_key = str(key).lower()
            low_value = value.lower().split("?", 1)[0]
            if "video" in low_key or "trailer" in low_key:
                preferred.append((0, value))
            elif low_value.endswith((".mp4", ".webm", ".mov")):
                preferred.append((1, value))
        if preferred:
            preferred.sort(key=lambda item: item[0])
            return _cdn_url(quest_id, preferred[0][1]) or ""
    return ""


def _cta_url(config: Dict[str, Any]) -> str:
    app = config.get("application") or {}
    cta = config.get("cta_config") or {}
    link = cta.get("link") or app.get("link") or app.get("store_link") or ""
    if isinstance(link, str) and link.startswith(("http://", "https://")):
        return link
    return ""


def _discord_timestamp(value: Optional[str], style: str) -> str:
    dt = _parse_iso(value)
    if not dt:
        return ""
    return f"<t:{int(dt.timestamp())}:{style}>"


def _duration_values(start_value: Optional[str], end_value: Optional[str]) -> Tuple[str, str, str]:
    start = _parse_iso(start_value)
    end = _parse_iso(end_value)
    if not start or not end or end <= start:
        return "Sconosciuta", "", ""
    seconds = int((end - start).total_seconds())
    hours = seconds // 3600
    days = hours // 24
    remaining_hours = hours % 24
    if days and remaining_hours:
        text = f"{days} giorni e {remaining_hours} ore"
    elif days:
        text = f"{days} giorni"
    else:
        text = f"{hours} ore"
    return text, str(days), str(hours)


class QuestTracker(BaseQuestTracker):
    """QuestTracker v2: messaggio completamente dentro l'embed con template e placeholder."""

    __author__ = "danyx64"
    __version__ = "2.0.0"

    def __init__(self, bot):
        super().__init__(bot)
        self.config.register_guild(
            embed_title_template=DEFAULT_EMBED_TITLE,
            embed_description_template=DEFAULT_EMBED_DESCRIPTION,
            embed_footer_template=DEFAULT_EMBED_FOOTER,
            ping_role=True,
            template_version=0,
        )

    async def cog_load(self):
        await self._migrate_embed_templates()
        await super().cog_load()

    async def _migrate_embed_templates(self):
        for guild in self.bot.guilds:
            conf = self.config.guild(guild)
            version = int(await conf.template_version() or 0)
            if version >= TEMPLATE_VERSION:
                continue

            legacy = await conf.message_template()
            if legacy and legacy != LEGACY_DEFAULT_TEMPLATE:
                legacy = str(legacy).replace("\\n", "\n").strip()
                description = f"{legacy}\n\n{DEFAULT_EMBED_BODY}" if legacy else DEFAULT_EMBED_DESCRIPTION
            else:
                description = DEFAULT_EMBED_DESCRIPTION

            await conf.embed_title_template.set(DEFAULT_EMBED_TITLE)
            await conf.embed_description_template.set(description)
            await conf.embed_footer_template.set(DEFAULT_EMBED_FOOTER)
            await conf.template_version.set(TEMPLATE_VERSION)

    def _extended_payload(
        self,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        settings: Dict[str, Any],
        *,
        test: bool = False,
    ) -> SafeFormatDict:
        base = self._quest_payload(entry, config)
        quest_id = base["quest_id"]
        app = config.get("application") or {}
        rewards = _rewards(config)
        reward = rewards[0] if rewards else {}
        reward_messages = reward.get("messages") or {}
        orb_raw = reward.get("orb_quantity")
        orb_amount = "" if orb_raw is None else str(orb_raw)
        reward_name = str(
            reward_messages.get("name")
            or reward.get("name")
            or base.get("reward")
            or "Ricompensa sconosciuta"
        )
        if orb_amount:
            reward_name = base.get("reward") or f"{orb_amount} Orbs"
            reward_type = "Orbs"
        else:
            reward_type = str(reward.get("type") or "Ricompensa virtuale")

        sku = base.get("sku") or ""
        if sku == "Sconosciuto":
            sku = ""

        tasks = _task_items(config)
        video_seconds_value = ""
        for key, target in tasks:
            if "VIDEO" in key and target:
                video_seconds_value = str(target)
                break

        start_value = config.get("starts_at")
        end_value = config.get("expires_at")
        duration, duration_days, duration_hours = _duration_values(start_value, end_value)

        hero_url = _hero_image(quest_id, config) or ""
        reward_image = _reward_image(quest_id, config) or ""
        video_url = _video_asset_url(quest_id, config)
        cta_url = _cta_url(config)

        reward_asset = reward.get("asset")
        reward_asset_url = _cdn_url(quest_id, str(reward_asset)) if reward_asset else ""
        features_raw = config.get("features") or []
        if isinstance(features_raw, (list, tuple, set)):
            features_list = [str(item) for item in features_raw]
        elif features_raw:
            features_list = [str(features_raw)]
        else:
            features_list = []

        role = guild.get_role(settings.get("role_id") or 0)
        role_mention = role.mention if role else ""
        role_name = role.name if role else ""
        role_id = str(role.id) if role else ""

        values = SafeFormatDict(base)
        values.update(
            {
                "mention": role_mention,
                "role_mention": role_mention,
                "role_name": role_name,
                "role_id": role_id,
                "quest_name": base["quest"],
                "game_name": base["game"],
                "application_name": str(app.get("name") or base["game"]),
                "start_date": base["start"],
                "end_date": base["end"],
                "start_iso": str(start_value or ""),
                "end_iso": str(end_value or ""),
                "start_timestamp": _discord_timestamp(start_value, "f"),
                "end_timestamp": _discord_timestamp(end_value, "f"),
                "start_relative": _discord_timestamp(start_value, "R"),
                "end_relative": _discord_timestamp(end_value, "R"),
                "duration": duration,
                "duration_days": duration_days,
                "duration_hours": duration_hours,
                "platforms": _platforms(config),
                "tasks": _task_text(config),
                "objectives": _task_text(config),
                "task_count": str(len(tasks)),
                "video_seconds": video_seconds_value,
                "video_duration": f"{video_seconds_value} secondi" if video_seconds_value else "",
                "video_url": video_url,
                "video_link": f"[▶️ Guarda il video]({video_url})" if video_url else "",
                "reward": base["reward"],
                "reward_name": reward_name,
                "reward_type": reward_type,
                "reward_count": str(len(rewards)),
                "orb_amount": orb_amount,
                "orb_line": f"**Orb Amount:** {orb_amount}" if orb_amount else "",
                "sku": sku,
                "sku_id": sku,
                "sku_line": f"**SKU ID:** `{sku}`" if sku else "",
                "reward_asset_url": reward_asset_url or "",
                "hero_url": hero_url,
                "image_url": hero_url,
                "reward_image_url": reward_image,
                "thumbnail_url": reward_image,
                "cta_url": cta_url,
                "link": cta_url,
                "features": ", ".join(features_list),
                "features_count": str(len(features_list)),
                "test_prefix": "TEST • " if test else "",
            }
        )
        return values

    @staticmethod
    def _format(template: str, values: SafeFormatDict, fallback: str) -> str:
        source = template or fallback
        try:
            return source.format_map(values).strip()
        except (ValueError, KeyError, IndexError):
            return fallback.format_map(values).strip()

    async def _build_embed_for_guild(
        self,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> Tuple[discord.Embed, Dict[str, Any], SafeFormatDict]:
        settings = await self.config.guild(guild).all()
        values = self._extended_payload(guild, entry, config, settings, test=test)

        title = self._format(
            settings.get("embed_title_template") or DEFAULT_EMBED_TITLE,
            values,
            DEFAULT_EMBED_TITLE,
        )[:256]
        description = self._format(
            settings.get("embed_description_template") or DEFAULT_EMBED_DESCRIPTION,
            values,
            DEFAULT_EMBED_DESCRIPTION,
        )[:4096]
        footer = self._format(
            settings.get("embed_footer_template") or DEFAULT_EMBED_FOOTER,
            values,
            DEFAULT_EMBED_FOOTER,
        )[:2048]

        embed = discord.Embed(
            title=title or None,
            description=description or None,
            url=values.get("cta_url") or None,
            colour=discord.Colour.blurple(),
        )
        if values.get("hero_url"):
            embed.set_image(url=values["hero_url"])
        if values.get("reward_image_url"):
            embed.set_thumbnail(url=values["reward_image_url"])
        if footer:
            embed.set_footer(text=footer)
        return embed, settings, values

    @staticmethod
    def _templates_request_role_ping(settings: Dict[str, Any]) -> bool:
        templates = "\n".join(
            str(settings.get(key) or "")
            for key in ("embed_title_template", "embed_description_template", "embed_footer_template")
        )
        return "{mention}" in templates or "{role_mention}" in templates

    async def _send_quest_embed(
        self,
        channel: discord.TextChannel,
        guild: discord.Guild,
        entry: Dict[str, Any],
        config: Dict[str, Any],
        *,
        test: bool = False,
    ) -> discord.Message:
        embed, settings, _ = await self._build_embed_for_guild(guild, entry, config, test=test)
        role = guild.get_role(settings.get("role_id") or 0)
        should_ping = (
            not test
            and role is not None
            and bool(settings.get("ping_role", True))
            and self._templates_request_role_ping(settings)
        )

        if not should_ping:
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

    async def _render_message(self, guild: discord.Guild, entry: Dict[str, Any], config: Dict[str, Any]) -> str:
        return ""

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0

        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return 0

        entries = await self._fetch_quests()
        active = self._active_quests(entries)
        if not settings.get("initialized"):
            await self.config.guild(guild).seen_keys.set([canonical for canonical, _, _ in active])
            await self.config.guild(guild).initialized.set(True)
            return 0

        seen = set(settings.get("seen_keys") or [])
        current_keys = {canonical for canonical, _, _ in active}
        new_seen = set(seen)
        sent = 0

        for canonical, entry, config in reversed(active):
            if canonical in seen:
                continue
            try:
                await self._send_quest_embed(channel, guild, entry, config)
            except (discord.Forbidden, discord.HTTPException):
                continue
            new_seen.add(canonical)
            sent += 1

        ordered = list(new_seen | current_keys)
        if len(ordered) > 500:
            ordered = ordered[-500:]
        await self.config.guild(guild).seen_keys.set(ordered)
        return sent

    async def command_message(self, ctx: commands.Context, testo: Optional[str] = None):
        conf = self.config.guild(ctx.guild)
        if testo is None:
            current = await conf.embed_description_template()
            return await ctx.send(
                "**Testo dell'embed attuale:**\n"
                f"```\n{current}\n```\n"
                f"Usa `{ctx.clean_prefix}quest placeholders` per vedere tutte le variabili."
            )
        if testo.strip().lower() == "reset":
            await conf.embed_description_template.set(DEFAULT_EMBED_DESCRIPTION)
            return await ctx.send("✅ Testo dell'embed ripristinato.")
        testo = testo.replace("\\n", "\n")
        if len(testo) > 4096:
            return await ctx.send("❌ Il testo dell'embed non può superare 4096 caratteri.")
        await conf.embed_description_template.set(testo)
        await ctx.send("✅ Testo dell'embed aggiornato. Usa `.quest test` per l'anteprima.")

    async def command_title(self, ctx: commands.Context, testo: Optional[str] = None):
        conf = self.config.guild(ctx.guild)
        if testo is None:
            return await ctx.send(f"Titolo attuale:\n```\n{await conf.embed_title_template()}\n```")
        if testo.strip().lower() == "reset":
            await conf.embed_title_template.set(DEFAULT_EMBED_TITLE)
            return await ctx.send("✅ Titolo ripristinato.")
        testo = testo.replace("\\n", "\n").strip()
        if len(testo) > 256:
            return await ctx.send("❌ Il titolo non può superare 256 caratteri.")
        await conf.embed_title_template.set(testo)
        await ctx.send("✅ Titolo dell'embed aggiornato.")

    async def command_footer(self, ctx: commands.Context, testo: Optional[str] = None):
        conf = self.config.guild(ctx.guild)
        if testo is None:
            return await ctx.send(f"Footer attuale:\n```\n{await conf.embed_footer_template()}\n```")
        if testo.strip().lower() == "reset":
            await conf.embed_footer_template.set(DEFAULT_EMBED_FOOTER)
            return await ctx.send("✅ Footer ripristinato.")
        testo = testo.replace("\\n", "\n").strip()
        if len(testo) > 2048:
            return await ctx.send("❌ Il footer non può superare 2048 caratteri.")
        await conf.embed_footer_template.set(testo)
        await ctx.send("✅ Footer dell'embed aggiornato.")

    async def command_placeholders(self, ctx: commands.Context):
        lines = [f"`{{{name}}}` — {description}" for name, description in PLACEHOLDERS.items()]
        chunks: List[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1800:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)
        for index, chunk in enumerate(chunks, 1):
            prefix = "**Placeholder QuestTracker**\n" if index == 1 else "**Placeholder QuestTracker — continua**\n"
            await ctx.send(prefix + chunk)

    async def command_ping(self, ctx: commands.Context, stato: Optional[str] = None):
        conf = self.config.guild(ctx.guild)
        if stato is None:
            enabled = await conf.ping_role()
            return await ctx.send(f"Ping reale del ruolo: **{'attivo' if enabled else 'disattivato'}**.")
        value = stato.strip().lower()
        if value in {"on", "yes", "si", "sì", "1", "true", "attiva", "attivo"}:
            await conf.ping_role.set(True)
            return await ctx.send("✅ Ping reale del ruolo attivato.")
        if value in {"off", "no", "0", "false", "disattiva", "disattivo"}:
            await conf.ping_role.set(False)
            return await ctx.send("✅ Ping reale del ruolo disattivato.")
        await ctx.send("Usa `.quest ping on` oppure `.quest ping off`.")

    async def command_test(self, ctx: commands.Context):
        async with ctx.typing():
            active = self._active_quests(await self._fetch_quests())
        if not active:
            return await ctx.send("❌ Al momento non trovo Quest attive nei feed.")
        _, entry, config = active[0]
        embed, _, _ = await self._build_embed_for_guild(ctx.guild, entry, config, test=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    async def command_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        embed = discord.Embed(title="🎯 QuestTracker v2", colour=discord.Colour.blurple())
        embed.add_field(name="Stato", value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Ping ruolo", value="✅ Attivo" if settings.get("ping_role", True) else "⛔ Disattivato", inline=True)
        embed.add_field(name="Controllo", value="Ogni 5 minuti", inline=True)
        embed.add_field(name="Comandi", value="`.quest ...` oppure `.quests ...`", inline=True)
        embed.add_field(name="Titolo", value=f"```\n{settings.get('embed_title_template') or DEFAULT_EMBED_TITLE}\n```"[:1024], inline=False)
        embed.add_field(name="Testo embed", value=f"```\n{settings.get('embed_description_template') or DEFAULT_EMBED_DESCRIPTION}\n```"[:1024], inline=False)
        embed.add_field(name="Footer", value=f"```\n{settings.get('embed_footer_template') or DEFAULT_EMBED_FOOTER}\n```"[:1024], inline=False)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())


def install_v2_commands():
    group = QuestTracker.quest

    for name in ("messaggio", "test", "status", "titolo", "footer", "placeholders", "placeholder", "ping"):
        existing = group.get_command(name)
        if existing is not None:
            group.remove_command(existing.name)

    @commands.admin_or_permissions(manage_guild=True)
    async def message_callback(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await self.command_message(ctx, testo)

    @commands.admin_or_permissions(manage_guild=True)
    async def title_callback(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await self.command_title(ctx, testo)

    @commands.admin_or_permissions(manage_guild=True)
    async def footer_callback(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await self.command_footer(ctx, testo)

    @commands.admin_or_permissions(manage_guild=True)
    async def placeholders_callback(self, ctx: commands.Context):
        await self.command_placeholders(ctx)

    @commands.admin_or_permissions(manage_guild=True)
    async def ping_callback(self, ctx: commands.Context, stato: Optional[str] = None):
        await self.command_ping(ctx, stato)

    @commands.admin_or_permissions(manage_guild=True)
    async def test_callback(self, ctx: commands.Context):
        await self.command_test(ctx)

    async def status_callback(self, ctx: commands.Context):
        await self.command_status(ctx)

    group.add_command(commands.command(name="messaggio", aliases=["message", "testo"])(message_callback))
    group.add_command(commands.command(name="titolo", aliases=["title"])(title_callback))
    group.add_command(commands.command(name="footer")(footer_callback))
    group.add_command(commands.command(name="placeholders", aliases=["placeholder", "vars", "variabili"])(placeholders_callback))
    group.add_command(commands.command(name="ping")(ping_callback))
    group.add_command(commands.command(name="test", aliases=["preview", "anteprima"])(test_callback))
    group.add_command(commands.command(name="status")(status_callback))
