import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, commands
from redbot.core.bot import Red


PRIMARY_SOURCE = "https://api.discordquest.com/api/quests"
FALLBACK_SOURCE = "https://raw.githubusercontent.com/aamiaa/discord-api-diff/refs/heads/main/quests.json"
DISCORD_SPONSORED_APP_ID = "545364944258990091"

TASK_LABELS = {
    "WATCH_VIDEO": "Guarda un video",
    "WATCH_VIDEO_ON_DESKTOP": "Guarda un video da PC",
    "WATCH_VIDEO_ON_MOBILE": "Guarda un video da mobile",
    "PLAY_ON_DESKTOP": "Gioca da PC",
    "STREAM_ON_DESKTOP": "Trasmetti il gioco da PC",
    "PLAY_ON_MOBILE": "Gioca da mobile",
    "PLAY_ON_PLAYSTATION": "Gioca su PlayStation",
    "PLAY_ON_XBOX": "Gioca su Xbox",
    "PLAY_ON_SWITCH": "Gioca su Nintendo Switch",
    "COMPLETE_ACHIEVEMENT": "Completa un obiettivo",
    "PLAY_ACTIVITY": "Avvia l'attivita",
    "ACHIEVEMENT_IN_ACTIVITY": "Completa un obiettivo nell'attivita",
}

PLATFORM_LABELS = {
    "WATCH_VIDEO_ON_DESKTOP": "PC",
    "PLAY_ON_DESKTOP": "PC",
    "STREAM_ON_DESKTOP": "PC",
    "WATCH_VIDEO_ON_MOBILE": "Mobile",
    "PLAY_ON_MOBILE": "Mobile",
    "PLAY_ON_PLAYSTATION": "PlayStation",
    "PLAY_ON_XBOX": "Xbox",
    "PLAY_ON_SWITCH": "Nintendo Switch",
}


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _date_it(value: Optional[str]) -> str:
    dt = _parse_iso(value)
    return dt.strftime("%d/%m/%y") if dt else "Sconosciuta"


def _cdn_url(quest_id: str, asset: Optional[str]) -> Optional[str]:
    if not asset:
        return None
    asset = str(asset)
    if asset.startswith(("http://", "https://")):
        return asset
    if asset.startswith("quests/"):
        return f"https://cdn.discordapp.com/{asset}"
    return f"https://cdn.discordapp.com/quests/{quest_id}/{asset}"


def _quest_config(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    config = entry.get("config")
    if isinstance(config, dict):
        return config
    if entry.get("starts_at") and entry.get("expires_at"):
        return entry
    return None


def _rewards(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    return [reward for reward in rewards if isinstance(reward, dict)]


def _reward_data(config: Dict[str, Any]) -> Tuple[str, str, Optional[int]]:
    rewards = _rewards(config)
    if not rewards:
        return "Ricompensa sconosciuta", "", None

    reward = rewards[0]
    sku = str(reward.get("sku_id") or "")
    orb_quantity = reward.get("orb_quantity")
    try:
        orb_quantity = int(orb_quantity) if orb_quantity is not None else None
    except (TypeError, ValueError):
        orb_quantity = None

    if orb_quantity is not None:
        return f"{orb_quantity} Orbs", sku, orb_quantity

    messages = reward.get("messages") or {}
    name = messages.get("name") or reward.get("name") or "Ricompensa"
    return str(name), sku, None


def _task_items(config: Dict[str, Any]) -> List[Tuple[str, Optional[int]]]:
    task_config = config.get("task_config_v2") or config.get("task_config") or {}
    tasks_data = task_config.get("tasks") or {}
    if not isinstance(tasks_data, dict):
        return []

    keys = list(tasks_data)
    has_specific_video = any(key in keys for key in ("WATCH_VIDEO_ON_DESKTOP", "WATCH_VIDEO_ON_MOBILE"))
    result: List[Tuple[str, Optional[int]]] = []
    for key, payload in tasks_data.items():
        if key == "WATCH_VIDEO" and has_specific_video:
            continue
        payload = payload if isinstance(payload, dict) else {}
        target = payload.get("target")
        try:
            target = int(target) if target is not None else None
        except (TypeError, ValueError):
            target = None
        result.append((key, target))
    return result


def _task_text(config: Dict[str, Any]) -> str:
    items = _task_items(config)
    if not items:
        return "• Obiettivo non specificato"

    lines = []
    for key, target in items:
        label = TASK_LABELS.get(key, key.replace("_", " ").title())
        suffix = f" ({target} secondi)" if target and target > 0 else ""
        lines.append(f"• {label}{suffix}")
    return "\n".join(lines)


def _platforms(config: Dict[str, Any]) -> str:
    platforms: List[str] = []
    for key, _ in _task_items(config):
        label = PLATFORM_LABELS.get(key)
        if label and label not in platforms:
            platforms.append(label)
    return ", ".join(platforms) if platforms else "Multipiattaforma"


def _hero_image(quest_id: str, config: Dict[str, Any]) -> Optional[str]:
    assets = config.get("assets") or {}
    if not isinstance(assets, dict):
        return None
    for key in ("hero", "quest_bar_hero", "game_tile_light", "game_tile"):
        url = _cdn_url(quest_id, assets.get(key))
        if url:
            return url
    return None


def _reward_image(quest_id: str, config: Dict[str, Any]) -> Optional[str]:
    rewards = _rewards(config)
    if not rewards:
        return None
    reward = rewards[0]
    asset = reward.get("asset")
    if asset and not str(asset).lower().split("?", 1)[0].endswith((".mp4", ".webm", ".mov")):
        return _cdn_url(quest_id, str(asset))
    if reward.get("orb_quantity") is not None:
        return "https://cdn.discordapp.com/assets/content/eff35518172b971fa47c521ca21c7576d3a245433a669a6765f63b744b7b733a.webm?format=png"
    return None


def _cta_url(config: Dict[str, Any]) -> Optional[str]:
    app = config.get("application") or {}
    cta = config.get("cta_config") or {}
    link = cta.get("link") or app.get("link") or app.get("store_link")
    if isinstance(link, str) and link.startswith(("http://", "https://")):
        return link
    return None


def _canonical_key(entry: Dict[str, Any], config: Dict[str, Any]) -> str:
    app = config.get("application") or {}
    app_id = str(app.get("id") or config.get("application_id") or "")
    rewards = _rewards(config)
    reward_sig = ",".join(
        f"{reward.get('sku_id', '')}:{reward.get('orb_quantity', '')}:{(reward.get('messages') or {}).get('name', reward.get('name', ''))}"
        for reward in rewards
    )
    task_sig = ",".join(f"{name}:{target or ''}" for name, target in sorted(_task_items(config)))
    base = "|".join(
        (
            app_id,
            str(config.get("starts_at") or ""),
            str(config.get("expires_at") or ""),
            task_sig,
            reward_sig,
        )
    )
    if app_id == DISCORD_SPONSORED_APP_ID:
        name = str((config.get("messages") or {}).get("quest_name") or "").strip().lower()
        return base + "|" + name
    return base or str(entry.get("id") or "")


class QuestTracker(commands.Cog):
    """Tracker Discord Quest con embed fisso e menzione del ruolo sotto l'embed."""

    __author__ = "danyx64"
    __version__ = "4.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=84418305220260914, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            role_id=None,
            ping_role=True,
            seen_keys=[],
            initialized=False,
        )
        self.session: Optional[aiohttp.ClientSession] = None
        self._scan_lock = asyncio.Lock()

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.quest_scan.start()

    def cog_unload(self):
        self.quest_scan.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def _fetch_source(self, url: str) -> List[Dict[str, Any]]:
        if not self.session or self.session.closed:
            return []
        try:
            async with self.session.get(url) as response:
                if response.status != 200:
                    return []
                data = await response.json(content_type=None)
                return data if isinstance(data, list) else []
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            return []

    async def _fetch_quests(self) -> List[Dict[str, Any]]:
        primary, fallback = await asyncio.gather(
            self._fetch_source(PRIMARY_SOURCE),
            self._fetch_source(FALLBACK_SOURCE),
        )
        merged: Dict[str, Dict[str, Any]] = {}
        for entry in primary + fallback:
            if not isinstance(entry, dict):
                continue
            quest_id = str(entry.get("id") or "")
            if quest_id and quest_id not in merged:
                merged[quest_id] = entry
        return list(merged.values())

    def _active_quests(self, entries: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        now = datetime.now(timezone.utc)
        active: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        canonical_seen = set()

        def start_key(item: Dict[str, Any]) -> datetime:
            cfg = _quest_config(item) or {}
            return _parse_iso(cfg.get("starts_at")) or datetime.min.replace(tzinfo=timezone.utc)

        for entry in sorted(entries, key=start_key, reverse=True):
            config = _quest_config(entry)
            if not config:
                continue
            starts = _parse_iso(config.get("starts_at"))
            expires = _parse_iso(config.get("expires_at"))
            if not starts or not expires or starts > now or expires <= now:
                continue
            name = str((config.get("messages") or {}).get("quest_name") or "")
            if name.upper().startswith("[TEST]"):
                continue
            canonical = _canonical_key(entry, config)
            if canonical in canonical_seen:
                continue
            canonical_seen.add(canonical)
            active.append((canonical, entry, config))
        return active

    def _quest_payload(self, entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, str]:
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        reward, sku, orb_quantity = _reward_data(config)
        return {
            "quest_id": str(entry.get("id") or "Sconosciuto"),
            "quest": str(messages.get("quest_name") or messages.get("game_title") or app.get("name") or "Discord Quest"),
            "game": str(app.get("name") or messages.get("game_title") or "Discord"),
            "application_id": str(app.get("id") or config.get("application_id") or "Sconosciuto"),
            "start": _date_it(config.get("starts_at")),
            "end": _date_it(config.get("expires_at")),
            "reward": reward,
            "sku": sku,
            "orb_amount": "" if orb_quantity is None else str(orb_quantity),
        }

    def _build_embed(self, entry: Dict[str, Any], config: Dict[str, Any], *, test: bool = False) -> discord.Embed:
        data = self._quest_payload(entry, config)
        prefix = "TEST • " if test else ""
        embed = discord.Embed(
            title=f"{prefix}Nuova Quest - {data['quest']}",
            url=_cta_url(config),
            colour=discord.Colour.blurple(),
        )

        info = (
            f"**Durata:** {data['start']} - {data['end']}\n"
            f"**Piattaforme:** {_platforms(config)}\n"
            f"**Gioco/App:** {data['game']} (`{data['application_id']}`)"
        )
        embed.add_field(name="📋 Informazioni Quest", value=info, inline=False)
        embed.add_field(name="✅ Obiettivi", value=_task_text(config)[:1024], inline=False)

        reward_text = f"**Tipo:** Ricompensa virtuale\n**Nome:** {data['reward']}"
        if data["orb_amount"]:
            reward_text += f"\n**Orb Amount:** {data['orb_amount']}"
        if data["sku"]:
            reward_text += f"\n**SKU ID:** `{data['sku']}`"
        embed.add_field(name="🎁 Ricompense", value=reward_text[:1024], inline=False)

        hero = _hero_image(data["quest_id"], config)
        if hero:
            embed.set_image(url=hero)
        reward_image = _reward_image(data["quest_id"], config)
        if reward_image:
            embed.set_thumbnail(url=reward_image)

        # Volutamente nessun footer e nessun timestamp.
        return embed

    async def _send_role_below(self, channel: discord.TextChannel, guild: discord.Guild, *, test: bool) -> None:
        settings = await self.config.guild(guild).all()
        role = guild.get_role(settings.get("role_id") or 0)
        if role is None:
            return

        if test:
            await channel.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if settings.get("ping_role", True):
            await channel.send(
                role.mention,
                allowed_mentions=discord.AllowedMentions(
                    roles=True,
                    users=False,
                    everyone=False,
                ),
            )

    async def _send_quest(self, channel: discord.TextChannel, guild: discord.Guild, entry: Dict[str, Any], config: Dict[str, Any], *, test: bool = False) -> None:
        await channel.send(
            embed=self._build_embed(entry, config, test=test),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._send_role_below(channel, guild, test=test)

    async def _mark_initial_state(self, guild: discord.Guild) -> int:
        active = self._active_quests(await self._fetch_quests())
        await self.config.guild(guild).seen_keys.set([canonical for canonical, _, _ in active])
        await self.config.guild(guild).initialized.set(True)
        return len(active)

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        settings = await self.config.guild(guild).all()
        if not settings.get("enabled") and not force:
            return 0

        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            return 0

        active = self._active_quests(await self._fetch_quests())
        if not settings.get("initialized"):
            await self.config.guild(guild).seen_keys.set([canonical for canonical, _, _ in active])
            await self.config.guild(guild).initialized.set(True)
            return 0

        seen = set(settings.get("seen_keys") or [])
        current = {canonical for canonical, _, _ in active}
        new_seen = set(seen)
        sent = 0

        for canonical, entry, config in reversed(active):
            if canonical in seen:
                continue
            try:
                await self._send_quest(channel, guild, entry, config, test=False)
            except (discord.Forbidden, discord.HTTPException):
                continue
            new_seen.add(canonical)
            sent += 1

        ordered = list(new_seen | current)
        await self.config.guild(guild).seen_keys.set(ordered[-500:])
        return sent

    @tasks.loop(minutes=5)
    async def quest_scan(self):
        if self._scan_lock.locked():
            return
        async with self._scan_lock:
            for guild in list(self.bot.guilds):
                try:
                    await self._scan_guild(guild)
                except Exception:
                    continue

    @quest_scan.before_loop
    async def before_quest_scan(self):
        await self.bot.wait_until_red_ready()
        await asyncio.sleep(15)

    @commands.group(name="quest", aliases=["quests"], invoke_without_command=True)
    @commands.guild_only()
    async def quest(self, ctx: commands.Context):
        """Comandi QuestTracker."""
        await ctx.send_help(ctx.command)

    @quest.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_setup(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).enabled.set(True)
        async with ctx.typing():
            count = await self._mark_initial_state(ctx.guild)
        await ctx.send(f"✅ QuestTracker attivato in {channel.mention}. Quest attive registrate: **{count}**.")

    @quest.command(name="canale")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"✅ Canale Quest impostato su {channel.mention}.")

    @quest.command(name="ruolo")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_role(self, ctx: commands.Context, role: discord.Role):
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"✅ Ruolo Quest impostato su {role.mention}.")

    @quest.command(name="noruolo", aliases=["ruolooff"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_no_role(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).role_id.set(None)
        await ctx.send("✅ Ruolo Quest rimosso.")

    @quest.command(name="ping")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_ping(self, ctx: commands.Context, state: Optional[str] = None):
        conf = self.config.guild(ctx.guild)
        if state is None:
            enabled = await conf.ping_role()
            return await ctx.send(f"Ping ruolo: **{'attivo' if enabled else 'disattivato'}**.")
        value = state.lower().strip()
        if value in {"on", "si", "yes", "true", "1", "attiva", "attivo"}:
            await conf.ping_role.set(True)
            return await ctx.send("✅ Ping ruolo attivato.")
        if value in {"off", "no", "false", "0", "disattiva", "disattivo"}:
            await conf.ping_role.set(False)
            return await ctx.send("✅ Ping ruolo disattivato.")
        await ctx.send("Usa `.quest ping on` oppure `.quest ping off`.")

    @quest.command(name="test", aliases=["preview", "anteprima"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_test(self, ctx: commands.Context):
        """Mostra la stessa grafica della notifica normale e il ruolo sotto l'embed."""
        async with ctx.typing():
            active = self._active_quests(await self._fetch_quests())
        if not active:
            return await ctx.send("❌ Al momento non trovo Quest attive nei feed.")
        _, entry, config = active[0]
        await self._send_quest(ctx.channel, ctx.guild, entry, config, test=True)

    @quest.command(name="attive")
    async def quest_active(self, ctx: commands.Context):
        async with ctx.typing():
            active = self._active_quests(await self._fetch_quests())
        if not active:
            return await ctx.send("Al momento non risultano Quest Discord attive.")
        lines = []
        for _, entry, config in active[:15]:
            data = self._quest_payload(entry, config)
            lines.append(f"• **{data['quest']}** — {data['reward']} — fino al `{data['end']}`")
        if len(active) > 15:
            lines.append(f"…e altre {len(active) - 15}.")
        await ctx.send(embed=discord.Embed(title=f"🎯 Quest Discord attive: {len(active)}", description="\n".join(lines), colour=discord.Colour.blurple()))

    @quest.command(name="controlla", aliases=["check"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_check(self, ctx: commands.Context):
        async with ctx.typing():
            sent = await self._scan_guild(ctx.guild, force=True)
        await ctx.send(f"✅ Controllo completato. Nuove Quest pubblicate: **{sent}**.")

    @quest.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_on(self, ctx: commands.Context):
        if not await self.config.guild(ctx.guild).channel_id():
            return await ctx.send("❌ Prima usa `.quest setup #canale`.")
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ QuestTracker attivato.")

    @quest.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_off(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ QuestTracker disattivato.")

    @quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        embed = discord.Embed(title="🎯 QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Versione", value=self.__version__, inline=True)
        embed.add_field(name="Stato", value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Ping ruolo", value="✅ Attivo" if settings.get("ping_role", True) else "⛔ Disattivato", inline=True)
        embed.add_field(name="Controllo", value="Ogni 5 minuti", inline=True)
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @quest.command(name="version", aliases=["versione"])
    async def quest_version(self, ctx: commands.Context):
        await ctx.send(f"QuestTracker **v{self.__version__}** — file principale `questtracker/questtracker.py`.")

    @quest.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_message(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await ctx.send("ℹ️ Il layout e fisso nel codice: niente TXT e niente template esterni.")

    @quest.command(name="titolo", aliases=["title"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_title(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await ctx.send("ℹ️ Il titolo e fisso nel codice: `Nuova Quest - <nome quest>`. ")

    @quest.command(name="footer")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_footer(self, ctx: commands.Context, *, testo: Optional[str] = None):
        await ctx.send("✅ Il footer e disattivato: dopo l'immagine finisce l'embed.")

    @quest.command(name="placeholders", aliases=["placeholder", "vars", "variabili"])
    async def quest_placeholders(self, ctx: commands.Context):
        await ctx.send("ℹ️ I placeholder non vengono piu usati: il layout e fisso direttamente nel cog.")
