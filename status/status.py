import asyncio
import random

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


class Status(commands.Cog):
    """Gestisce tutti gli status del bot: normali, streaming, ordine/random, durate e placeholder dinamici."""

    __author__ = "danyx64"
    __version__ = "1.4.0"

    VALID_TYPES = {"playing", "watching", "listening", "streaming", "custom"}

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=918274650341782611, force_registration=True)
        self.config.register_global(
            enabled=False,
            mode="order",
            default_duration=60,
            default_stream_url="https://www.twitch.tv/4vv0c4t0",
            statuses=[],
            current_index=0,
            member_guild_id=None,
        )
        self._task = None
        self._wake = asyncio.Event()
        self._last_random_index = None
        self._active_entry = None
        self._presence_refresh_task = None
        self._member_guild_id = None

    async def cog_load(self):
        # La guild usata per i placeholder dei membri viene salvata in Config.
        # Se ancora non esiste, il runner scegliera automaticamente la guild piu grande.
        self._member_guild_id = await self.config.member_guild_id()
        self._active_entry = None
        self._start_loop()

    def cog_unload(self):
        if self._task and not self._task.done():
            self._task.cancel()
        if self._presence_refresh_task and not self._presence_refresh_task.done():
            self._presence_refresh_task.cancel()

    def _start_loop(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._runner())

    @staticmethod
    def _guild_member_count(guild: discord.Guild) -> int:
        """Restituisce il totale corrente della guild senza mantenere contatori paralleli."""
        if guild.member_count is not None:
            return max(0, int(guild.member_count))
        return len(guild.members)

    def _member_source_guild(self):
        """Restituisce la guild sorgente dei conteggi, con fallback sicuro alla piu grande."""
        if self._member_guild_id:
            guild = self.bot.get_guild(int(self._member_guild_id))
            if guild is not None:
                return guild

        guilds = list(self.bot.guilds)
        if not guilds:
            return None
        return max(guilds, key=self._guild_member_count)

    async def _ensure_member_source_guild(self):
        """Assicura che esista una guild sorgente persistente per i placeholder dei membri."""
        guild = None
        if self._member_guild_id:
            guild = self.bot.get_guild(int(self._member_guild_id))

        if guild is None:
            guilds = list(self.bot.guilds)
            if not guilds:
                self._member_guild_id = None
                return None
            guild = max(guilds, key=self._guild_member_count)
            self._member_guild_id = guild.id
            await self.config.member_guild_id.set(guild.id)

        return guild

    def _placeholder_values(self):
        guilds = list(self.bot.guilds)
        source_guild = self._member_source_guild()

        if source_guild is not None:
            member_count = self._guild_member_count(source_guild)
            human_count = sum(1 for member in source_guild.members if not member.bot)
            bot_count = sum(1 for member in source_guild.members if member.bot)
        else:
            member_count = 0
            human_count = 0
            bot_count = 0

        channel_count = sum(len(guild.channels) for guild in guilds)
        user_count = len(self.bot.users)
        bot_name = self.bot.user.name if self.bot.user else "Bot"

        return {
            "member_count": str(member_count),
            "members": str(member_count),
            "human_count": str(human_count),
            "bot_count": str(bot_count),
            "guild_count": str(len(guilds)),
            "server_count": str(len(guilds)),
            "channel_count": str(channel_count),
            "user_count": str(user_count),
            "bot_name": bot_name,
        }

    def _format_text(self, text: str) -> str:
        result = str(text)
        for key, value in self._placeholder_values().items():
            result = result.replace("{" + key + "}", value)
        return result[:128]

    async def _activity_from_entry(self, entry):
        kind = str(entry.get("type", "playing")).lower()
        raw_text = str(entry.get("text", "")).strip() or "Hobby MC"
        text = self._format_text(raw_text)

        if kind == "playing":
            return discord.Game(name=text)
        if kind == "watching":
            return discord.Activity(type=discord.ActivityType.watching, name=text)
        if kind == "listening":
            return discord.Activity(type=discord.ActivityType.listening, name=text)
        if kind == "streaming":
            url = entry.get("url") or await self.config.default_stream_url()
            return discord.Streaming(name=text, url=url)
        if kind == "custom":
            try:
                return discord.CustomActivity(name=text)
            except AttributeError:
                return discord.Activity(type=discord.ActivityType.custom, name=text)
        return discord.Game(name=text)

    async def _apply_entry(self, entry):
        activity = await self._activity_from_entry(entry)
        await self.bot.change_presence(activity=activity)

    async def _refresh_current_presence(self):
        data = await self.config.all()
        if not data.get("enabled") or not self._active_entry:
            return
        await self._apply_entry(self._active_entry)

    def _schedule_presence_refresh(self):
        # Un solo refresh pendente: tutti gli eventi ravvicinati vengono accorpati.
        if self._presence_refresh_task is None or self._presence_refresh_task.done():
            self._presence_refresh_task = asyncio.create_task(self._debounced_presence_refresh())

    async def _debounced_presence_refresh(self):
        try:
            # Evita di martellare change_presence quando entrano/escono piu utenti insieme.
            await asyncio.sleep(5)
            await self._refresh_current_presence()
        except asyncio.CancelledError:
            raise
        except Exception:
            # Il ciclo principale continuera comunque ad aggiornare lo status.
            pass

    async def _choose_index(self, statuses, mode, current_index):
        if not statuses:
            return None
        if mode == "random":
            if len(statuses) == 1:
                idx = 0
            else:
                choices = list(range(len(statuses)))
                if self._last_random_index in choices:
                    choices.remove(self._last_random_index)
                idx = random.choice(choices)
            self._last_random_index = idx
            return idx
        return current_index % len(statuses)

    async def _runner(self):
        await self.bot.wait_until_ready()
        # Migrazione automatica dalla vecchia configurazione: al primo reload
        # sceglie la guild piu grande e ne salva l'ID per tutti i reload successivi.
        await self._ensure_member_source_guild()

        while True:
            try:
                data = await self.config.all()
                if not data.get("enabled") or not data.get("statuses"):
                    self._active_entry = None
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=30)
                    except asyncio.TimeoutError:
                        pass
                    continue

                statuses = data["statuses"]
                mode = str(data.get("mode", "order")).lower()
                current_index = int(data.get("current_index", 0))
                idx = await self._choose_index(statuses, mode, current_index)
                if idx is None:
                    await asyncio.sleep(5)
                    continue

                entry = statuses[idx]
                self._active_entry = dict(entry)
                await self._apply_entry(entry)

                if mode == "order":
                    await self.config.current_index.set((idx + 1) % len(statuses))

                duration = int(entry.get("duration") or data.get("default_duration") or 60)
                duration = max(5, min(duration, 86400))

                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=duration)
                except asyncio.TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(10)

    def _notify_loop(self):
        self._start_loop()
        self._wake.set()

    @commands.Cog.listener()
    async def on_ready(self):
        # Dopo reconnect verifica la guild sorgente e ripubblica lo status.
        await self._ensure_member_source_guild()
        self._schedule_presence_refresh()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        source = self._member_source_guild()
        if source is not None and member.guild.id == source.id:
            self._schedule_presence_refresh()

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        source = self._member_source_guild()
        if source is not None and member.guild.id == source.id:
            self._schedule_presence_refresh()

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload):
        # Arriva anche quando il membro non era presente nella cache.
        source = self._member_source_guild()
        if source is not None and payload.guild_id == source.id:
            self._schedule_presence_refresh()

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        if self._member_guild_id is None:
            await self._ensure_member_source_guild()
        self._schedule_presence_refresh()

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        if self._member_guild_id == guild.id:
            self._member_guild_id = None
            await self.config.member_guild_id.set(None)
            await self._ensure_member_source_guild()
        self._schedule_presence_refresh()

    @commands.group(name="status", invoke_without_command=True)
    @commands.is_owner()
    async def status(self, ctx: commands.Context):
        """Gestisce tutti gli status del bot."""
        await ctx.send_help(ctx.command)

    @status.command(name="helpme")
    async def status_helpme(self, ctx: commands.Context):
        p = ctx.clean_prefix
        await ctx.send(
            "**Status unificato**\n"
            f"`{p}status add watching 60 Membri: {{member_count}}`\n"
            f"`{p}status add playing 45 Su {{guild_count}} server`\n"
            f"`{p}status add listening 30 {{human_count}} utenti`\n"
            f"`{p}status add streaming 90 Live su Hobby MC`\n"
            f"`{p}status addstream 60 https://www.twitch.tv/4vv0c4t0 Live su Hobby MC`\n"
            f"`{p}status guild` per usare il server corrente come sorgente membri\n"
            f"`{p}status mode order` oppure `{p}status mode random`\n"
            f"`{p}status recount` per forzare il refresh del conteggio\n"
            f"`{p}status placeholders`\n"
            f"`{p}status enable`"
        )

    @status.command(name="placeholders", aliases=["vars", "variables"])
    async def status_placeholders(self, ctx: commands.Context):
        source = self._member_source_guild()
        source_name = source.name if source is not None else "nessuna"
        await ctx.send(
            "**Placeholder dinamici**\n"
            "`{member_count}` / `{members}` -> membri della guild sorgente\n"
            "`{human_count}` -> membri non-bot visibili nella guild sorgente\n"
            "`{bot_count}` -> bot visibili nella guild sorgente\n"
            "`{guild_count}` / `{server_count}` -> numero server del bot\n"
            "`{channel_count}` -> canali totali nelle guild del bot\n"
            "`{user_count}` -> utenti unici visibili al bot\n"
            "`{bot_name}` -> nome del bot\n\n"
            f"Guild sorgente attuale: **{source_name}**."
        )

    @status.command(name="guild", aliases=["memberguild", "sourceguild"])
    @commands.guild_only()
    async def status_guild(self, ctx: commands.Context, guild_id: int = None):
        """Imposta la guild da usare per member_count, human_count e bot_count."""
        guild = ctx.guild if guild_id is None else self.bot.get_guild(guild_id)
        if guild is None:
            return await ctx.send("Guild non trovata. Usa un ID di un server in cui il bot e presente.")

        self._member_guild_id = guild.id
        await self.config.member_guild_id.set(guild.id)

        if self._active_entry:
            await self._refresh_current_presence()
        else:
            self._notify_loop()

        count = self._guild_member_count(guild)
        await ctx.send(f"Guild sorgente impostata su **{guild.name}** (`{guild.id}`): **{count}** membri.")

    @status.command(name="add")
    async def status_add(self, ctx: commands.Context, kind: str, duration: int, *, text: str):
        kind = kind.lower()
        if kind not in self.VALID_TYPES:
            return await ctx.send("Tipo non valido. Usa: `playing`, `watching`, `listening`, `streaming`, `custom`.")
        if duration < 5 or duration > 86400:
            return await ctx.send("La durata deve essere tra 5 e 86400 secondi.")
        entry = {"type": kind, "text": text[:128], "duration": duration, "url": None}
        async with self.config.statuses() as statuses:
            statuses.append(entry)
            index = len(statuses)
        self._notify_loop()
        await ctx.send(f"Status #{index} aggiunto: **{kind}** per **{duration}s** -> `{text[:128]}`")

    @status.command(name="addstream")
    async def status_addstream(self, ctx: commands.Context, duration: int, url: str, *, text: str):
        if duration < 5 or duration > 86400:
            return await ctx.send("La durata deve essere tra 5 e 86400 secondi.")
        if not url.lower().startswith(("https://twitch.tv/", "https://www.twitch.tv/")):
            return await ctx.send("Usa un URL Twitch valido.")
        entry = {"type": "streaming", "text": text[:128], "duration": duration, "url": url}
        async with self.config.statuses() as statuses:
            statuses.append(entry)
            index = len(statuses)
        self._notify_loop()
        await ctx.send(f"Streaming #{index} aggiunto per **{duration}s** -> `{text[:128]}`")

    @status.command(name="list")
    async def status_list(self, ctx: commands.Context):
        data = await self.config.all()
        statuses = data.get("statuses", [])
        if not statuses:
            return await ctx.send("Nessuno status configurato.")
        lines = []
        for i, entry in enumerate(statuses, start=1):
            extra = ""
            if entry.get("type") == "streaming":
                extra = f" | {entry.get('url') or data.get('default_stream_url')}"
            rendered = self._format_text(entry.get("text", ""))
            lines.append(
                f"`#{i}` **{entry.get('type')}** · {entry.get('duration', data.get('default_duration'))}s · "
                f"`{entry.get('text')}` -> **{rendered}**{extra}"
            )
        await ctx.send("\n".join(lines)[:1900])

    @status.command(name="remove", aliases=["del", "delete"])
    async def status_remove(self, ctx: commands.Context, index: int):
        async with self.config.statuses() as statuses:
            if index < 1 or index > len(statuses):
                return await ctx.send("Indice non valido.")
            removed = statuses.pop(index - 1)
        await self.config.current_index.set(0)
        self._notify_loop()
        await ctx.send(f"Rimosso: **{removed.get('type')}** -> `{removed.get('text')}`")

    @status.command(name="clear")
    async def status_clear(self, ctx: commands.Context):
        await self.config.statuses.set([])
        await self.config.current_index.set(0)
        self._notify_loop()
        await ctx.send("Tutti gli status sono stati rimossi.")

    @status.command(name="mode")
    async def status_mode(self, ctx: commands.Context, mode: str):
        mode = mode.lower()
        if mode not in {"order", "random"}:
            return await ctx.send("Usa `order` oppure `random`.")
        await self.config.mode.set(mode)
        await self.config.current_index.set(0)
        self._last_random_index = None
        self._notify_loop()
        await ctx.send(f"Modalita impostata su **{mode}**.")

    @status.command(name="duration")
    async def status_duration(self, ctx: commands.Context, index: int, seconds: int):
        if seconds < 5 or seconds > 86400:
            return await ctx.send("La durata deve essere tra 5 e 86400 secondi.")
        async with self.config.statuses() as statuses:
            if index < 1 or index > len(statuses):
                return await ctx.send("Indice non valido.")
            statuses[index - 1]["duration"] = seconds
        self._notify_loop()
        await ctx.send(f"Durata dello status #{index} impostata a **{seconds}s**.")

    @status.command(name="edit")
    async def status_edit(self, ctx: commands.Context, index: int, *, text: str):
        async with self.config.statuses() as statuses:
            if index < 1 or index > len(statuses):
                return await ctx.send("Indice non valido.")
            statuses[index - 1]["text"] = text[:128]
        self._notify_loop()
        await ctx.send(f"Testo dello status #{index} aggiornato.")

    @status.command(name="type")
    async def status_type(self, ctx: commands.Context, index: int, kind: str):
        kind = kind.lower()
        if kind not in self.VALID_TYPES:
            return await ctx.send("Tipo non valido.")
        async with self.config.statuses() as statuses:
            if index < 1 or index > len(statuses):
                return await ctx.send("Indice non valido.")
            statuses[index - 1]["type"] = kind
        self._notify_loop()
        await ctx.send(f"Tipo dello status #{index} impostato su **{kind}**.")

    @status.command(name="streamurl")
    async def status_streamurl(self, ctx: commands.Context, url: str):
        if not url.lower().startswith(("https://twitch.tv/", "https://www.twitch.tv/")):
            return await ctx.send("Usa un URL Twitch valido.")
        await self.config.default_stream_url.set(url)
        self._notify_loop()
        await ctx.send(f"URL Streaming predefinito impostato su <{url}>.")

    @status.command(name="enable")
    async def status_enable(self, ctx: commands.Context):
        if not await self.config.statuses():
            return await ctx.send("Aggiungi almeno uno status prima di abilitare il ciclo.")
        await self._ensure_member_source_guild()
        await self.config.enabled.set(True)
        self._notify_loop()
        await ctx.send("Ciclo status **abilitato**.")

    @status.command(name="disable")
    async def status_disable(self, ctx: commands.Context):
        await self.config.enabled.set(False)
        self._active_entry = None
        self._notify_loop()
        await self.bot.change_presence(activity=None)
        await ctx.send("Ciclo status **disabilitato**.")

    @status.command(name="next")
    async def status_next(self, ctx: commands.Context):
        self._notify_loop()
        await ctx.send("Passaggio allo status successivo richiesto.")

    @status.command(name="recount", aliases=["refreshcount", "countrefresh"])
    async def status_recount(self, ctx: commands.Context):
        """Ricalcola il conteggio della guild sorgente e aggiorna subito la presenza."""
        guild = await self._ensure_member_source_guild()
        if self._active_entry:
            await self._refresh_current_presence()
        else:
            self._notify_loop()

        if guild is None:
            return await ctx.send("Nessuna guild disponibile per il conteggio membri.")

        count = self._guild_member_count(guild)
        await ctx.send(f"Conteggio membri aggiornato da **{guild.name}**: **{count}**.")

    @status.command(name="show")
    async def status_show(self, ctx: commands.Context):
        data = await self.config.all()
        values = self._placeholder_values()
        source = self._member_source_guild()
        source_text = f"{source.name} (`{source.id}`)" if source is not None else "nessuna"
        await ctx.send(
            f"Versione: **{self.__version__}**\n"
            f"Attivo: **{'si' if data.get('enabled') else 'no'}**\n"
            f"Modalita: **{data.get('mode')}**\n"
            f"Status configurati: **{len(data.get('statuses', []))}**\n"
            f"Guild membri: **{source_text}**\n"
            f"Membri correnti: **{values['member_count']}**\n"
            f"Twitch predefinito: <{data.get('default_stream_url')}>"
        )