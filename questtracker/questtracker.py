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
DEFAULT_TEMPLATE = "🎯 **Nuova Quest Discord disponibile!**\n{mention}"
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
    "PLAY_ACTIVITY": "Avvia l'attività",
    "ACHIEVEMENT_IN_ACTIVITY": "Completa un obiettivo nell'attività",
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


class SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _date_it(value: Optional[str]) -> str:
    date = _parse_iso(value)
    return date.strftime("%d/%m/%y") if date else "Sconosciuta"


def _cdn_url(quest_id: str, asset: Optional[str]) -> Optional[str]:
    if not asset:
        return None
    if asset.startswith("http://") or asset.startswith("https://"):
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


def _reward_data(config: Dict[str, Any]) -> Tuple[str, Optional[str], Optional[str]]:
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    if not rewards:
        return "Ricompensa sconosciuta", None, None

    reward = rewards[0] if isinstance(rewards[0], dict) else {}
    sku = str(reward.get("sku_id")) if reward.get("sku_id") is not None else None

    if reward.get("orb_quantity") is not None:
        amount = reward.get("orb_quantity")
        return f"{amount} Orbs", sku, None

    messages = reward.get("messages") or {}
    name = messages.get("name") or reward.get("name") or "Ricompensa"
    asset = reward.get("asset")
    return str(name), sku, asset


def _task_items(config: Dict[str, Any]) -> List[Tuple[str, Optional[int]]]:
    task_config = config.get("task_config_v2") or config.get("task_config") or {}
    tasks_data = task_config.get("tasks") or {}
    if not isinstance(tasks_data, dict):
        return []

    keys = list(tasks_data.keys())
    has_specific_video = any(k in keys for k in ("WATCH_VIDEO_ON_DESKTOP", "WATCH_VIDEO_ON_MOBILE"))
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
        suffix = f" ({target} secondi)" if target is not None and target > 0 else ""
        lines.append(f"• {label}{suffix}")
    return "\n".join(lines)


def _platforms(config: Dict[str, Any]) -> str:
    platforms = []
    for key, _ in _task_items(config):
        label = PLATFORM_LABELS.get(key)
        if label and label not in platforms:
            platforms.append(label)
    return ", ".join(platforms) if platforms else "Multipiattaforma"


def _hero_image(quest_id: str, config: Dict[str, Any]) -> Optional[str]:
    assets = config.get("assets") or {}
    for key in ("hero", "quest_bar_hero", "game_tile_light", "game_tile"):
        url = _cdn_url(quest_id, assets.get(key))
        if url:
            return url
    return None


def _reward_image(quest_id: str, config: Dict[str, Any]) -> Optional[str]:
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    if not rewards or not isinstance(rewards[0], dict):
        return None
    reward = rewards[0]
    asset = reward.get("asset")
    if asset and not str(asset).lower().endswith((".mp4", ".webm")):
        return _cdn_url(quest_id, str(asset))
    if reward.get("orb_quantity") is not None:
        return "https://cdn.discordapp.com/assets/content/eff35518172b971fa47c521ca21c7576d3a245433a669a6765f63b744b7b733a.webm?format=png"
    return None


def _canonical_key(entry: Dict[str, Any], config: Dict[str, Any]) -> str:
    app = config.get("application") or {}
    app_id = str(app.get("id") or config.get("application_id") or "")
    reward_config = config.get("rewards_config") or {}
    rewards = reward_config.get("rewards") or config.get("rewards") or []
    reward_sig = []
    for reward in rewards:
        if not isinstance(reward, dict):
            continue
        reward_sig.append(
            f"{reward.get('sku_id', '')}:{reward.get('orb_quantity', '')}:{(reward.get('messages') or {}).get('name', reward.get('name', ''))}"
        )
    task_sig = ",".join(f"{name}:{target or ''}" for name, target in sorted(_task_items(config)))
    base = "|".join(
        [
            app_id,
            str(config.get("starts_at") or ""),
            str(config.get("expires_at") or ""),
            task_sig,
            ",".join(sorted(reward_sig)),
        ]
    )

    if app_id == DISCORD_SPONSORED_APP_ID:
        name = str((config.get("messages") or {}).get("quest_name") or "").strip().lower()
        return base + "|" + name
    return base or str(entry.get("id"))


class QuestTracker(commands.Cog):
    """Traccia le Discord Quest e pubblica le nuove quest in un canale configurato."""

    __author__ = "danyx64"
    __version__ = "1.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=84418305220260914, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            role_id=None,
            message_template=DEFAULT_TEMPLATE,
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
            key = str(entry.get("id") or "")
            if not key:
                continue
            if key not in merged:
                merged[key] = entry
        return list(merged.values())

    def _active_quests(self, entries: Iterable[Dict[str, Any]]) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        now = datetime.now(timezone.utc)
        active: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
        seen_canonical = set()

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
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)
            active.append((canonical, entry, config))
        return active

    def _quest_payload(self, entry: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, str]:
        quest_id = str(entry.get("id") or "Sconosciuto")
        messages = config.get("messages") or {}
        app = config.get("application") or {}
        quest_name = str(messages.get("quest_name") or messages.get("game_title") or app.get("name") or "Discord Quest")
        game_name = str(app.get("name") or messages.get("game_title") or "Discord")
        application_id = str(app.get("id") or config.get("application_id") or "Sconosciuto")
        reward, sku, _ = _reward_data(config)
        return {
            "quest": quest_name,
            "game": game_name,
            "reward": reward,
            "start": _date_it(config.get("starts_at")),
            "end": _date_it(config.get("expires_at")),
            "quest_id": quest_id,
            "application_id": application_id,
            "sku": sku or "Sconosciuto",
        }

    def _build_embed(self, entry: Dict[str, Any], config: Dict[str, Any], *, test: bool = False) -> discord.Embed:
        data = self._quest_payload(entry, config)
        prefix = "TEST • " if test else ""
        app = config.get("application") or {}
        cta = config.get("cta_config") or {}
        link = cta.get("link") or app.get("link") or app.get("store_link")
        if link and not str(link).startswith(("http://", "https://")):
            link = None

        embed = discord.Embed(
            title=f"{prefix}Nuova Quest - {data['quest']}",
            url=link,
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )
        info = (
            f"**Durata:** {data['start']} - {data['end']}\n"
            f"**Piattaforme:** {_platforms(config)}\n"
            f"**Gioco/App:** {data['game']} (`{data['application_id']}`)"
        )
        embed.add_field(name="📋 Informazioni Quest", value=info, inline=False)
        embed.add_field(name="✅ Obiettivi", value=_task_text(config)[:1024], inline=False)
        reward_text = f"**Tipo:** Ricompensa virtuale\n**Nome:** {data['reward']}"
        if data["sku"] != "Sconosciuto":
            reward_text += f"\n**SKU ID:** `{data['sku']}`"
        embed.add_field(name="🎁 Ricompense", value=reward_text[:1024], inline=False)

        hero = _hero_image(data["quest_id"], config)
        if hero:
            embed.set_image(url=hero)
        reward_image = _reward_image(data["quest_id"], config)
        if reward_image:
            embed.set_thumbnail(url=reward_image)

        embed.set_footer(text=f"Quest ID: {data['quest_id']} • Controllo automatico ogni 5 minuti")
        return embed

    async def _render_message(self, guild: discord.Guild, entry: Dict[str, Any], config: Dict[str, Any]) -> str:
        settings = await self.config.guild(guild).all()
        role = guild.get_role(settings.get("role_id") or 0)
        data = self._quest_payload(entry, config)
        values = SafeFormatDict(data)
        values["mention"] = role.mention if role else ""
        template = settings.get("message_template") or DEFAULT_TEMPLATE
        try:
            return template.format_map(values).strip()
        except (ValueError, KeyError):
            return DEFAULT_TEMPLATE.format_map(values).strip()

    async def _mark_initial_state(self, guild: discord.Guild) -> int:
        entries = await self._fetch_quests()
        active = self._active_quests(entries)
        keys = [canonical for canonical, _, _ in active]
        await self.config.guild(guild).seen_keys.set(keys)
        await self.config.guild(guild).initialized.set(True)
        return len(keys)

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
        sent = 0
        new_seen = set(seen)

        for canonical, entry, config in reversed(active):
            if canonical in seen:
                continue
            content = await self._render_message(guild, entry, config)
            embed = self._build_embed(entry, config)
            try:
                await channel.send(
                    content=content or None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True, users=False, everyone=False),
                )
            except (discord.Forbidden, discord.HTTPException):
                continue
            new_seen.add(canonical)
            sent += 1

        ordered = list(new_seen | current_keys)
        if len(ordered) > 500:
            ordered = ordered[-500:]
        await self.config.guild(guild).seen_keys.set(ordered)
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
        """Configura e controlla il tracker delle Discord Quest. Funziona sia con .quest che con .quests."""
        await ctx.send_help(ctx.command)

    @quest.command(name="setup")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_setup(self, ctx: commands.Context, channel: discord.TextChannel):
        """Configura il canale, abilita il tracker e registra le quest gia attive senza pubblicarle."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await self.config.guild(ctx.guild).enabled.set(True)
        async with ctx.typing():
            count = await self._mark_initial_state(ctx.guild)
        await ctx.send(
            f"✅ QuestTracker attivato in {channel.mention}. Ho registrato **{count}** quest attive; da ora pubblichero solo quelle nuove."
        )

    @quest.command(name="canale")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_channel(self, ctx: commands.Context, channel: discord.TextChannel):
        """Cambia il canale delle notifiche."""
        await self.config.guild(ctx.guild).channel_id.set(channel.id)
        await ctx.send(f"✅ Canale Quest impostato su {channel.mention}.")

    @quest.command(name="ruolo")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_role(self, ctx: commands.Context, role: discord.Role):
        """Imposta il ruolo da menzionare tramite {mention}."""
        await self.config.guild(ctx.guild).role_id.set(role.id)
        await ctx.send(f"✅ Le nuove Quest menzioneranno {role.mention} quando il messaggio contiene `{{mention}}`.")

    @quest.command(name="noruolo", aliases=["ruolooff"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_no_role(self, ctx: commands.Context):
        """Disattiva la menzione del ruolo."""
        await self.config.guild(ctx.guild).role_id.set(None)
        await ctx.send("✅ Menzione ruolo disattivata.")

    @quest.command(name="messaggio")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_message(self, ctx: commands.Context, *, testo: Optional[str] = None):
        """Mostra o modifica il messaggio sopra l'embed. Usa 'reset' per il predefinito."""
        if testo is None:
            current = await self.config.guild(ctx.guild).message_template()
            return await ctx.send(
                "**Messaggio attuale:**\n"
                f"```\n{current}\n```\n"
                "Variabili: `{mention}` `{quest}` `{game}` `{reward}` `{start}` `{end}` `{quest_id}` `{application_id}` `{sku}`"
            )
        if testo.strip().lower() == "reset":
            await self.config.guild(ctx.guild).message_template.set(DEFAULT_TEMPLATE)
            return await ctx.send("✅ Messaggio predefinito ripristinato.")
        if len(testo) > 1800:
            return await ctx.send("❌ Il messaggio e troppo lungo. Massimo 1800 caratteri.")
        await self.config.guild(ctx.guild).message_template.set(testo.replace("\\n", "\n"))
        await ctx.send("✅ Messaggio Quest aggiornato. Usa `.quest test` per vedere l'anteprima.")

    @quest.command(name="test")
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_test(self, ctx: commands.Context):
        """Invia un'anteprima usando una quest attiva, senza notificare il ruolo."""
        async with ctx.typing():
            active = self._active_quests(await self._fetch_quests())
        if not active:
            return await ctx.send("❌ Al momento non trovo Quest attive nei feed.")
        _, entry, config = active[0]
        content = await self._render_message(ctx.guild, entry, config)
        embed = self._build_embed(entry, config, test=True)
        await ctx.send(content=content or None, embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @quest.command(name="attive")
    async def quest_active(self, ctx: commands.Context):
        """Mostra un riepilogo delle quest attive rilevate."""
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
        embed = discord.Embed(
            title=f"🎯 Quest Discord attive: {len(active)}",
            description="\n".join(lines),
            colour=discord.Colour.blurple(),
        )
        await ctx.send(embed=embed)

    @quest.command(name="controlla", aliases=["check"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_check(self, ctx: commands.Context):
        """Forza subito un controllo delle nuove quest."""
        async with ctx.typing():
            sent = await self._scan_guild(ctx.guild, force=True)
        await ctx.send(f"✅ Controllo completato. Nuove Quest pubblicate: **{sent}**.")

    @quest.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_on(self, ctx: commands.Context):
        """Abilita il controllo automatico."""
        channel_id = await self.config.guild(ctx.guild).channel_id()
        if not channel_id:
            return await ctx.send("❌ Prima usa `.quest setup #canale`.")
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("✅ Controllo automatico delle Quest attivato.")

    @quest.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_off(self, ctx: commands.Context):
        """Disabilita il controllo automatico senza cancellare la configurazione."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ Controllo automatico delle Quest disattivato.")

    @quest.command(name="status")
    async def quest_status(self, ctx: commands.Context):
        """Mostra la configurazione del tracker."""
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        role = ctx.guild.get_role(settings.get("role_id") or 0)
        embed = discord.Embed(title="🎯 QuestTracker", colour=discord.Colour.blurple())
        embed.add_field(name="Stato", value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato", inline=True)
        embed.add_field(name="Canale", value=channel.mention if channel else "Non configurato", inline=True)
        embed.add_field(name="Ruolo", value=role.mention if role else "Nessuno", inline=True)
        embed.add_field(name="Controllo", value="Ogni 5 minuti", inline=True)
        embed.add_field(name="Comandi", value="`.quest ...` oppure `.quests ...`", inline=True)
        embed.add_field(
            name="Messaggio",
            value=f"```\n{settings.get('message_template') or DEFAULT_TEMPLATE}\n```"[:1024],
            inline=False,
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
