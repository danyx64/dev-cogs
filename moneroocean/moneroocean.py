import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.bot import Red

ATOMIC = Decimal("1000000000000")
DEFAULT_EMBED = {
    "title": "", "description": "", "footer": "", "color": "#F26822", "layout": "list",
    "show_total_pending": True, "show_new_pending": True, "show_detection_time": True,
    "total_label": "XMR totali pending",
    "new_label": "Nuovi XMR aggiunti al pending",
    "time_label": "Ora rilevamento", "timestamp": True,
}


class MoneroOcean(commands.Cog):
    """Notifiche Discord per i nuovi importi pending di un wallet MoneroOcean."""

    __author__ = "danyx64"
    __version__ = "1.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=918274650341782612, force_registration=True)
        self.config.register_global(
            enabled=True, wallet="", channel_id=None, interval=60, timezone="Europe/Rome",
            last_pending=None, last_detection=None, embed=DEFAULT_EMBED,
        )
        self.task = None
        self.session = None

    async def cog_load(self):
        self.task = asyncio.create_task(self._runner())

    def cog_unload(self):
        if self.task and not self.task.done():
            self.task.cancel()
        if self.session and not self.session.closed:
            asyncio.create_task(self.session.close())

    async def _get_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20),
                headers={"User-Agent": "danyx64-dev-cogs/1.0"},
            )
        return self.session

    async def _fetch_pending(self, wallet):
        url = f"https://api.moneroocean.stream/miner/{wallet}/stats"
        async with (await self._get_session()).get(url) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        if not isinstance(data, dict) or "amtDue" not in data:
            raise ValueError("Risposta API senza campo amtDue.")
        return Decimal(str(data["amtDue"])) / ATOMIC

    async def _now(self):
        try:
            tz = ZoneInfo(await self.config.timezone())
        except ZoneInfoNotFoundError:
            tz = ZoneInfo("UTC")
        return datetime.now(tz)

    @staticmethod
    def _amount(value):
        return f"{value:.12f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _color(value):
        try:
            raw = str(value).strip().lstrip("#")
            return int(raw, 16) if len(raw) in (6, 8) else 0xF26822
        except (TypeError, ValueError):
            return 0xF26822

    async def _make_embed(self, total, added, detected_at):
        cfg = {**DEFAULT_EMBED, **(await self.config.embed() or {})}
        values = {
            "total_xmr": self._amount(total), "new_xmr": self._amount(added),
            "time": detected_at.strftime("%d/%m/%Y %H:%M:%S"),
            "date": detected_at.strftime("%d/%m/%Y"),
            "wallet": await self.config.wallet(),
        }

        def render(template):
            result = str(template or "")
            for key, value in values.items():
                result = result.replace("{" + key + "}", str(value))
            return result[:4096]

        title, custom_description, footer = (render(cfg.get(k)) for k in ("title", "description", "footer"))
        layout = str(cfg.get("layout", "list")).lower()
        lines = []
        if cfg.get("show_total_pending", True):
            lines.append(f'{render(cfg.get("total_label"))}: {values["total_xmr"]} XMR')
        if cfg.get("show_new_pending", True):
            lines.append(f'{render(cfg.get("new_label"))}: +{values["new_xmr"]} XMR')
        if cfg.get("show_detection_time", True):
            lines.append(f'{render(cfg.get("time_label"))}: {values["time"]}')

        embed = discord.Embed(
            title=title or None,
            description="\n".join(lines) if layout == "list" else (custom_description or None),
            color=self._color(cfg.get("color")),
            timestamp=detected_at if cfg.get("timestamp", True) else None,
        )
        if layout != "list":
            if cfg.get("show_total_pending", True):
                embed.add_field(name=render(cfg.get("total_label")), value=f'{values["total_xmr"]} XMR', inline=False)
            if cfg.get("show_new_pending", True):
                embed.add_field(name=render(cfg.get("new_label")), value=f'+{values["new_xmr"]} XMR', inline=False)
            if cfg.get("show_detection_time", True):
                embed.add_field(name=render(cfg.get("time_label")), value=values["time"], inline=False)
        if footer:
            embed.set_footer(text=footer[:2048])
        return embed

    async def _notify(self, total, added):
        channel_id = await self.config.channel_id()
        if not channel_id:
            return
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                return
        detected_at = await self._now()
        await channel.send(embed=await self._make_embed(total, added, detected_at))
        await self.config.last_detection.set(detected_at.isoformat())

    async def _check_once(self):
        wallet = (await self.config.wallet()).strip()
        if not wallet:
            return
        current = await self._fetch_pending(wallet)
        old_raw = await self.config.last_pending()
        if old_raw is None:
            await self.config.last_pending.set(str(current))
            return
        try:
            previous = Decimal(str(old_raw))
        except InvalidOperation:
            previous = current
        await self.config.last_pending.set(str(current))
        delta = current - previous
        if delta > 0:
            await self._notify(current, delta)

    async def _runner(self):
        await self.bot.wait_until_red_ready()
        while True:
            try:
                if await self.config.enabled():
                    await self._check_once()
                await asyncio.sleep(max(30, min(int(await self.config.interval()), 3600)))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(30)

    @commands.group(name="moneroocean", aliases=["mo"], invoke_without_command=True)
    @commands.is_owner()
    async def moneroocean(self, ctx):
        """Configura il monitor MoneroOcean."""
        await ctx.send_help(ctx.command)

    @moneroocean.command(name="helpme")
    async def mo_helpme(self, ctx):
        p = ctx.clean_prefix
        await ctx.send(
            "**MoneroOcean – comandi**\n"
            f"`{p}moneroocean wallet <indirizzo_XMR>` – imposta il wallet\n"
            f"`{p}moneroocean channel` – usa il canale corrente\n"
            f"`{p}moneroocean interval <secondi>` – intervallo 30–3600\n"
            f"`{p}moneroocean timezone <zona>` – es. Europe/Rome\n"
            f"`{p}moneroocean enable` / `{p}moneroocean disable`\n"
            f"`{p}moneroocean status` / `{p}moneroocean check`\n"
            f"`{p}moneroocean embed title|description|footer <testo>`\n"
            f"`{p}moneroocean embed label total|new|time <testo>`\n"
            f"`{p}moneroocean embed color <hex>`\n"
            f"`{p}moneroocean embed reset` / `{p}moneroocean embed preview`\n"
            "Placeholder: `{total_xmr}`, `{new_xmr}`, `{time}`, `{date}`, `{wallet}`"
        )

    @moneroocean.command(name="wallet")
    async def mo_wallet(self, ctx, *, wallet: str):
        wallet = wallet.strip()
        if not 50 <= len(wallet) <= 120:
            return await ctx.send("Indirizzo wallet non valido: verifica l'indirizzo XMR completo.")
        await self.config.wallet.set(wallet)
        await self.config.last_pending.set(None)
        await ctx.send("Wallet impostato. Il prossimo controllo inizializza il saldo; gli aumenti successivi genereranno avvisi.")

    @moneroocean.command(name="channel")
    async def mo_channel(self, ctx):
        await self.config.channel_id.set(ctx.channel.id)
        await ctx.send(f"Canale notifiche impostato su {ctx.channel.mention}.")

    @moneroocean.command(name="interval")
    async def mo_interval(self, ctx, seconds: int):
        if not 30 <= seconds <= 3600:
            return await ctx.send("Intervallo consentito: 30–3600 secondi.")
        await self.config.interval.set(seconds)
        await ctx.send(f"Intervallo impostato a **{seconds}s**.")

    @moneroocean.command(name="timezone")
    async def mo_timezone(self, ctx, *, timezone: str):
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError:
            return await ctx.send("Fuso orario non valido. Esempio: `Europe/Rome`.")
        await self.config.timezone.set(timezone)
        await ctx.send(f"Fuso orario impostato su **{timezone}**.")

    @moneroocean.command(name="enable")
    async def mo_enable(self, ctx):
        await self.config.enabled.set(True)
        await ctx.send("Monitor MoneroOcean **abilitato**.")

    @moneroocean.command(name="disable")
    async def mo_disable(self, ctx):
        await self.config.enabled.set(False)
        await ctx.send("Monitor MoneroOcean **disabilitato**.")

    @moneroocean.command(name="status")
    async def mo_status(self, ctx):
        wallet, channel = await self.config.wallet(), await self.config.channel_id()
        pending = await self.config.last_pending()
        await ctx.send(
            f"**MoneroOcean Monitor**\nAttivo: **{'sì' if await self.config.enabled() else 'no'}**\n"
            f"Wallet: `{wallet[:12] + '…' if wallet else 'non configurato'}`\n"
            f"Canale ID: **{channel or 'non configurato'}**\n"
            f"Intervallo: **{await self.config.interval()}s**\n"
            f"Ultimo pending: **{pending if pending is not None else 'in attesa'} XMR**"
        )

    @moneroocean.command(name="check")
    async def mo_check(self, ctx):
        try:
            await self._check_once()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            return await ctx.send(f"Errore API MoneroOcean: `{exc}`")
        await ctx.send("Controllo completato. Se il pending è aumentato, l'avviso è stato inviato nel canale configurato.")

    @moneroocean.group(name="embed", invoke_without_command=True)
    async def mo_embed(self, ctx):
        await ctx.send_help(ctx.command)

    async def _set_embed_value(self, key, value):
        async with self.config.embed() as cfg:
            cfg[key] = value[:4000]

    @mo_embed.command(name="title")
    async def mo_embed_title(self, ctx, *, value: str):
        await self._set_embed_value("title", value)
        await ctx.send("Titolo embed aggiornato.")

    @mo_embed.command(name="description")
    async def mo_embed_description(self, ctx, *, value: str):
        await self._set_embed_value("description", value)
        await ctx.send("Descrizione embed aggiornata.")

    @mo_embed.command(name="footer")
    async def mo_embed_footer(self, ctx, *, value: str):
        await self._set_embed_value("footer", value)
        await ctx.send("Firma embed aggiornata.")

    @mo_embed.command(name="label")
    async def mo_embed_label(self, ctx, field: str, *, value: str):
        labels = {
            "total": "total_label",
            "new": "new_label",
            "time": "time_label",
        }
        field = field.lower()
        if field not in labels:
            return await ctx.send(
                "Etichette disponibili: `total`, `new`, `time`. "
                "Esempio: `moneroocean embed label new Nuovi guadagni`."
            )
        value = value.strip()
        if not value:
            return await ctx.send("Il testo dell'etichetta non può essere vuoto.")
        await self._set_embed_value(labels[field], value)
        await ctx.send(
            f"Etichetta **{field}** aggiornata. "
            f"Usa `{ctx.clean_prefix}moneroocean embed preview` per vedere il risultato."
        )

    @mo_embed.command(name="labelsreset")
    async def mo_embed_labels_reset(self, ctx):
        async with self.config.embed() as cfg:
            for key in ("total_label", "new_label", "time_label"):
                cfg[key] = DEFAULT_EMBED[key]
        await ctx.send("Le tre etichette sono state ripristinate ai valori predefiniti.")

    @mo_embed.command(name="layout")
    async def mo_embed_layout(self, ctx, layout: str):
        layout = layout.lower()
        if layout not in ("list", "fields"):
            return await ctx.send("Layout disponibili: `list` oppure `fields`.")
        await self._set_embed_value("layout", layout)
        await ctx.send(f"Layout embed impostato su **{layout}**. Usa `{ctx.clean_prefix}moneroocean embed preview` per vedere il risultato.")

    @mo_embed.command(name="color")
    async def mo_embed_color(self, ctx, value: str):
        if not value.startswith("#") or len(value) not in (7, 9):
            return await ctx.send("Usa un colore esadecimale, ad esempio `#F26822`.")
        try:
            int(value[1:], 16)
        except ValueError:
            return await ctx.send("Codice colore non valido.")
        await self._set_embed_value("color", value)
        await ctx.send("Colore embed aggiornato.")

    @mo_embed.command(name="reset")
    async def mo_embed_reset(self, ctx):
        await self.config.embed.set(DEFAULT_EMBED)
        await ctx.send("Embed ripristinato ai valori predefiniti.")

    @mo_embed.command(name="preview")
    async def mo_embed_preview(self, ctx):
        raw = await self.config.last_pending()
        total = Decimal(str(raw)) if raw is not None else Decimal("0")
        now = await self._now()
        await ctx.send(embed=await self._make_embed(total, Decimal("0.000001"), now))
