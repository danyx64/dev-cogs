import asyncio
import re
import string
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import discord
from discord import app_commands
from redbot.core import Config, commands
from redbot.core.bot import Red


CONFIG_IDENTIFIER = 927315640118477221
MAX_WORD_GAP = 4
DEFAULT_REPLY = "{mention} ha bestemmiato per la {count}ª volta."

DEFAULT_DEITIES = [
    "dio",
    "gesu",
    "gesu cristo",
    "cristo",
    "madonna",
    "allah",
    "signore",
    "padre eterno",
    "spirito santo",
    "vergine maria",
    "maria",
]

DEFAULT_PROFANITIES = [
    "porco",
    "porca",
    "cane",
    "cagna",
    "maiale",
    "maiala",
    "bestia",
    "boia",
    "maledetto",
    "maledetta",
    "impestato",
    "impestata",
    "schifoso",
    "schifosa",
    "cazzo",
    "merda",
    "stronzo",
    "stronza",
    "puttana",
    "troia",
    "bastardo",
    "bastarda",
]

CHAR_SUBSTITUTIONS = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        "а": "a",
        "е": "e",
        "і": "i",
        "о": "o",
        "р": "p",
        "с": "c",
        "у": "y",
        "х": "x",
        "ѕ": "s",
    }
)
SYMBOL_LEET_SUBSTITUTIONS = str.maketrans({"@": "a", "$": "s"})

PLACEHOLDER_DESCRIPTIONS = {
    "mention": "menzione dell'utente",
    "user": "menzione dell'utente",
    "username": "username Discord",
    "displayname": "nome visualizzato nel server",
    "user_id": "ID Discord dell'utente",
    "count": "bestemmie dell'utente",
    "user_count": "bestemmie dell'utente",
    "server_count": "bestemmie totali del server",
    "server_total": "bestemmie totali del server",
    "total": "bestemmie totali del server",
    "word": "bestemmia o combinazione rilevata",
    "deity": "riferimento religioso rilevato",
    "profanity": "termine offensivo rilevato",
    "channel": "canale del messaggio",
    "guild": "nome del server",
    "server": "nome del server",
}


class _SafeFormatDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class SwearJar(commands.Cog):
    """Contatore bestemmie con filtro anti-elusione, gestione semplice e /top."""

    __author__ = "danyx64"
    __version__ = "4.0.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.base_path = Path(__file__).resolve().parent
        self.deities_file = self.base_path / "divinita.txt"
        self.profanities_file = self.base_path / "parolacce.txt"

        self.config = Config.get_conf(
            self,
            identifier=CONFIG_IDENTIFIER,
            force_registration=True,
        )
        self.config.register_guild(
            enabled=True,
            words=[],
            deities=DEFAULT_DEITIES,
            profanities=DEFAULT_PROFANITIES,
            custom_swears=[],
            reply_message=DEFAULT_REPLY,
            channel_mode="all",
            channels=[],
            whitelist_channels=[],
            blacklist_channels=[],
        )
        self.config.register_member(count=0)

        self._reply_locks: Dict[int, asyncio.Lock] = {}
        self._count_locks: Dict[Tuple[int, int], asyncio.Lock] = {}

    async def cog_load(self):
        await self._sync_dictionary_files()
        await self._migrate_legacy_words()
        await self._migrate_legacy_channels()

    @staticmethod
    def _clean_file_values(lines: Sequence[str]) -> List[str]:
        values = []
        seen = set()
        for line in lines:
            value = line.strip()
            if not value or value.startswith("#"):
                continue
            key = value.casefold()
            if key in seen:
                continue
            seen.add(key)
            values.append(value)
        return values

    def _read_dictionary_file(self, path: Path, fallback: Sequence[str]) -> List[str]:
        try:
            values = self._clean_file_values(path.read_text(encoding="utf-8").splitlines())
            return values or list(fallback)
        except OSError:
            return list(fallback)

    async def _sync_dictionary_files(self):
        deities = self._read_dictionary_file(self.deities_file, DEFAULT_DEITIES)
        profanities = self._read_dictionary_file(self.profanities_file, DEFAULT_PROFANITIES)
        for guild in self.bot.guilds:
            await self.config.guild(guild).deities.set(deities)
            await self.config.guild(guild).profanities.set(profanities)

    async def _migrate_legacy_words(self):
        """Riusa eventuali bestemmie salvate nel vecchio campo `words`."""
        for guild in self.bot.guilds:
            legacy_words = await self.config.guild(guild).words()
            if not legacy_words:
                continue
            current = await self.config.guild(guild).custom_swears()
            merged = list(current)
            known = {self._normalize(value) for value in merged}
            for value in legacy_words:
                key = self._normalize(value)
                if key and key not in known:
                    merged.append(value)
                    known.add(key)
            await self.config.guild(guild).custom_swears.set(merged)

    async def _migrate_legacy_channels(self):
        """Converte la vecchia lista unica nelle nuove whitelist/blacklist separate."""
        for guild in self.bot.guilds:
            conf = self.config.guild(guild)
            mode = await conf.channel_mode()
            legacy = await conf.channels()
            if mode in {"include", "whitelist"}:
                if legacy and not await conf.whitelist_channels():
                    await conf.whitelist_channels.set(list(dict.fromkeys(legacy)))
                await conf.channel_mode.set("whitelist")
            elif mode in {"exclude", "blacklist"}:
                if legacy and not await conf.blacklist_channels():
                    await conf.blacklist_channels.set(list(dict.fromkeys(legacy)))
                await conf.channel_mode.set("blacklist")
            elif mode != "all":
                await conf.channel_mode.set("all")

    @staticmethod
    def _normalize(text: str) -> str:
        text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
        text = unicodedata.normalize("NFKD", text.casefold())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.translate(CHAR_SUBSTITUTIONS)
        text = re.sub(r"[^a-z]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _normalization_variants(cls, text: str) -> List[str]:
        variants = [
            cls._normalize(text),
            cls._normalize(text.translate(SYMBOL_LEET_SUBSTITUTIONS)),
        ]
        return list(dict.fromkeys(value for value in variants if value))

    @staticmethod
    def _collapse_runs(value: str) -> str:
        return re.sub(r"([a-z])\1+", r"\1", value)

    @classmethod
    @lru_cache(maxsize=8192)
    def _term_pattern(cls, term: str) -> Optional[re.Pattern]:
        normalized = cls._normalize(term)
        compact = cls._collapse_runs(normalized.replace(" ", ""))
        if not compact:
            return None
        body = r"\s*".join(f"{re.escape(ch)}+" for ch in compact)
        return re.compile(rf"(?<![a-z]){body}(?![a-z])")

    @classmethod
    def _find_term_span(cls, normalized_content: str, term: str) -> Optional[Tuple[int, int]]:
        pattern = cls._term_pattern(term)
        if pattern is None:
            return None
        match = pattern.search(normalized_content)
        return match.span() if match else None

    @staticmethod
    def _edit_distance_at_most_one(left: str, right: str) -> bool:
        if left == right:
            return True
        if abs(len(left) - len(right)) > 1:
            return False
        if len(left) > len(right):
            left, right = right, left
        if len(left) == len(right):
            return sum(a != b for a, b in zip(left, right)) <= 1

        i = j = differences = 0
        while i < len(left) and j < len(right):
            if left[i] == right[j]:
                i += 1
                j += 1
                continue
            differences += 1
            if differences > 1:
                return False
            j += 1
        return True

    @classmethod
    def _find_fuzzy_span(cls, normalized_content: str, term: str) -> Optional[Tuple[int, int]]:
        term_n = cls._collapse_runs(cls._normalize(term))
        if " " in term_n or len(term_n) < 7:
            return None

        for match in re.finditer(r"[a-z]+", normalized_content):
            word = cls._collapse_runs(match.group(0))
            if len(word) < 7:
                continue
            if word[0] != term_n[0] or word[-1] != term_n[-1]:
                continue
            if cls._edit_distance_at_most_one(word, term_n):
                return match.span()
        return None

    @classmethod
    def _collect_matches(
        cls,
        normalized_content: str,
        terms: Sequence[str],
        *,
        allow_fuzzy: bool = False,
    ) -> List[Tuple[str, int, int]]:
        matches = []
        seen = set()
        for term in terms:
            key = cls._normalize(term)
            if not key or key in seen:
                continue
            seen.add(key)
            span = cls._find_term_span(normalized_content, term)
            if span is None and allow_fuzzy:
                span = cls._find_fuzzy_span(normalized_content, term)
            if span is not None:
                matches.append((term, span[0], span[1]))
        return matches

    @staticmethod
    def _word_gap(
        normalized_content: str,
        first: Tuple[str, int, int],
        second: Tuple[str, int, int],
    ) -> int:
        _, start_a, end_a = first
        _, start_b, end_b = second
        if start_a <= start_b:
            between = normalized_content[end_a:start_b]
        else:
            between = normalized_content[end_b:start_a]
        return len(re.findall(r"[a-z]+", between))

    @classmethod
    def _find_custom_violation(cls, content: str, custom_swears: Sequence[str]) -> Optional[str]:
        for normalized in cls._normalization_variants(content):
            for term in custom_swears:
                if cls._find_term_span(normalized, term) is not None:
                    return term
        return None

    @classmethod
    def _find_composed_violation(
        cls,
        content: str,
        deities: Sequence[str],
        profanities: Sequence[str],
    ) -> Optional[Tuple[str, str]]:
        best_pair = None
        best_gap = None
        for normalized in cls._normalization_variants(content):
            deity_matches = cls._collect_matches(normalized, deities)
            if not deity_matches:
                continue
            profanity_matches = cls._collect_matches(
                normalized,
                profanities,
                allow_fuzzy=True,
            )
            if not profanity_matches:
                continue
            for deity in deity_matches:
                for profanity in profanity_matches:
                    gap = cls._word_gap(normalized, deity, profanity)
                    if gap > MAX_WORD_GAP:
                        continue
                    if best_gap is None or gap < best_gap:
                        best_gap = gap
                        best_pair = (deity[0], profanity[0])
        return best_pair

    async def _channel_allowed(self, guild: discord.Guild, channel_id: int) -> bool:
        conf = self.config.guild(guild)
        mode = await conf.channel_mode()
        if mode == "all":
            return True
        if mode == "whitelist":
            return channel_id in set(await conf.whitelist_channels())
        if mode == "blacklist":
            return channel_id not in set(await conf.blacklist_channels())
        return True

    async def _server_total(self, guild: discord.Guild) -> int:
        all_members = await self.config.all_members(guild)
        return sum(int(data.get("count", 0) or 0) for data in all_members.values())

    @staticmethod
    def _format_reply(template: str, values: dict) -> str:
        try:
            return template.format_map(_SafeFormatDict(values))
        except (ValueError, KeyError, IndexError):
            return DEFAULT_REPLY.format_map(_SafeFormatDict(values))

    @staticmethod
    def _validate_template(template: str) -> Optional[str]:
        try:
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template)
                if field_name
            }
        except ValueError:
            return "Le parentesi graffe del messaggio non sono valide."
        unknown = sorted(fields - set(PLACEHOLDER_DESCRIPTIONS))
        if unknown:
            return "Placeholder non validi: " + ", ".join(f"`{{{name}}}`" for name in unknown)
        return None

    @staticmethod
    def _extract_id(value: str) -> Optional[int]:
        value = value.strip()
        match = re.fullmatch(r"<@!?(\d{15,25})>", value)
        if match:
            return int(match.group(1))
        if value.isdigit() and 15 <= len(value) <= 25:
            return int(value)
        return None

    @staticmethod
    def _channel_id_from_argument(value: str) -> Optional[int]:
        value = value.strip()
        match = re.fullmatch(r"<#(\d{15,25})>", value)
        if match:
            return int(match.group(1))
        if value.isdigit() and 15 <= len(value) <= 25:
            return int(value)
        return None

    async def _set_member_count(self, guild: discord.Guild, uid: int, count: int) -> Tuple[int, int]:
        member_group = self.config.member_from_ids(guild.id, uid)
        previous = await member_group.count()
        await member_group.count.set(count)
        return previous, await self._server_total(guild)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not message.content:
            return
        if not await self.config.guild(message.guild).enabled():
            return
        if not await self._channel_allowed(message.guild, message.channel.id):
            return

        guild_config = self.config.guild(message.guild)
        custom_swears = await guild_config.custom_swears()
        custom_match = self._find_custom_violation(message.content, custom_swears)

        deity = profanity = ""
        detected_word = custom_match
        if detected_word is None:
            deities = await guild_config.deities()
            profanities = await guild_config.profanities()
            composed = self._find_composed_violation(message.content, deities, profanities)
            if composed is None:
                return
            deity, profanity = composed
            detected_word = f"{deity} + {profanity}"

        count_key = (message.guild.id, message.author.id)
        count_lock = self._count_locks.setdefault(count_key, asyncio.Lock())
        async with count_lock:
            member_group = self.config.member(message.author)
            new_count = (await member_group.count()) + 1
            await member_group.count.set(new_count)

        server_total = await self._server_total(message.guild)
        template = await guild_config.reply_message()
        values = {
            "mention": message.author.mention,
            "user": message.author.mention,
            "username": message.author.name,
            "displayname": message.author.display_name,
            "user_id": message.author.id,
            "count": new_count,
            "user_count": new_count,
            "server_count": server_total,
            "server_total": server_total,
            "total": server_total,
            "word": detected_word,
            "deity": deity,
            "profanity": profanity,
            "channel": getattr(message.channel, "mention", f"#{message.channel}"),
            "guild": message.guild.name,
            "server": message.guild.name,
        }
        reply = self._format_reply(template, values)
        if not reply.strip():
            return

        reply_lock = self._reply_locks.setdefault(message.channel.id, asyncio.Lock())
        try:
            async with reply_lock:
                await message.reply(
                    reply[:2000],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(
                        users=True,
                        roles=False,
                        everyone=False,
                    ),
                )
        except (discord.Forbidden, discord.HTTPException):
            pass

    @commands.command(name="swearjar")
    @commands.guild_only()
    async def swearjar_help(self, ctx: commands.Context):
        """Mostra la guida compatta di tutti i comandi SwearJar."""
        p = ctx.clean_prefix
        embed = discord.Embed(
            title="SwearJar - Comandi",
            description="Gestione semplice del contatore bestemmie.",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(
            name="Controllo",
            value=(
                f"`{p}enable` - attiva il rilevamento\n"
                f"`{p}disable` - disattiva il rilevamento\n"
                f"`{p}reset <user_id>` - azzera un utente\n"
                f"`{p}setcount <user_id> <numero>` - imposta manualmente il conteggio\n"
                f"`{p}swear set <user_id> <numero>` - stessa correzione dal gruppo swear"
            ),
            inline=False,
        )
        embed.add_field(
            name="Canali",
            value=(
                f"`{p}channel all` - controlla tutti i canali\n"
                f"`{p}channel whitelist add <#canale>` - controlla solo i canali aggiunti\n"
                f"`{p}channel whitelist remove <#canale>` - rimuove dalla whitelist\n"
                f"`{p}channel blacklist add <#canale>` - ignora i canali aggiunti\n"
                f"`{p}channel blacklist remove <#canale>` - rimuove dalla blacklist"
            ),
            inline=False,
        )
        embed.add_field(
            name="Bestemmie manuali",
            value=(
                f"`{p}swear add <bestemmia>` - aggiunge una frase completa\n"
                f"`{p}swear remove <bestemmia>` - rimuove una frase\n"
                f"`{p}swear list` - mostra le frasi aggiunte manualmente"
            ),
            inline=False,
        )
        embed.add_field(
            name="Messaggio",
            value=(
                f"`{p}message set <testo>` - imposta la risposta\n"
                f"`{p}message reset` - ripristina la risposta standard\n"
                f"`{p}message placeholders` - mostra tutti i placeholder"
            ),
            inline=False,
        )
        embed.add_field(
            name="Classifica",
            value="`/top` - top 10 con menzioni e totale server",
            inline=False,
        )
        await ctx.send(embed=embed)

    @commands.command(name="enable")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def enable(self, ctx: commands.Context):
        """Attiva il rilevamento delle bestemmie nel server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        await ctx.send("SwearJar attivato.")

    @commands.command(name="disable")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def disable(self, ctx: commands.Context):
        """Disattiva il rilevamento delle bestemmie nel server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("SwearJar disattivato.")

    @commands.command(name="reset")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def reset_count(self, ctx: commands.Context, user_id: str):
        """Azzera il conteggio di un utente tramite ID Discord o menzione."""
        uid = self._extract_id(user_id)
        if uid is None:
            return await ctx.send("Usa un ID Discord valido o una menzione. Esempio: `.reset 123456789012345678`.")
        member_group = self.config.member_from_ids(ctx.guild.id, uid)
        previous = await member_group.count()
        await member_group.count.set(0)
        await ctx.send(f"Conteggio di <@{uid}>: **{previous} -> 0**.", allowed_mentions=discord.AllowedMentions(users=True))

    @commands.command(name="setcount", aliases=["swearset"])
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def set_count(self, ctx: commands.Context, user_id: str, count: int):
        """Imposta manualmente il conteggio di un utente per correggere miscount."""
        uid = self._extract_id(user_id)
        if uid is None:
            return await ctx.send("Usa un ID Discord valido o una menzione.")
        if count < 0:
            return await ctx.send("Il conteggio non puo essere negativo.")
        previous, total = await self._set_member_count(ctx.guild, uid, count)
        await ctx.send(
            f"Conteggio di <@{uid}> corretto: **{previous} -> {count}**. Totale server: **{total}**.",
            allowed_mentions=discord.AllowedMentions(users=True),
        )

    @commands.group(name="channel", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def channel(self, ctx: commands.Context):
        """Configura dove SwearJar deve leggere i messaggi."""
        conf = self.config.guild(ctx.guild)
        mode = await conf.channel_mode()
        whitelist = await conf.whitelist_channels()
        blacklist = await conf.blacklist_channels()
        await ctx.send(
            f"Modalita attuale: **{mode}**. "
            f"Whitelist: **{len(whitelist)}** canali. Blacklist: **{len(blacklist)}** canali.\n"
            f"Usa `{ctx.clean_prefix}help channel` per i sotto-comandi."
        )

    @channel.command(name="all")
    async def channel_all(self, ctx: commands.Context):
        """Controlla tutti i canali del server."""
        await self.config.guild(ctx.guild).channel_mode.set("all")
        await ctx.send("Modalita canali: **all**. SwearJar controllera tutti i canali.")

    @channel.group(name="whitelist", invoke_without_command=True)
    async def channel_whitelist(self, ctx: commands.Context):
        """Usa solo i canali presenti nella whitelist."""
        await self.config.guild(ctx.guild).channel_mode.set("whitelist")
        channels = await self.config.guild(ctx.guild).whitelist_channels()
        text = "\n".join(f"- <#{cid}>" for cid in channels) if channels else "Whitelist vuota."
        await ctx.send("Modalita **whitelist** attiva.\n" + text, allowed_mentions=discord.AllowedMentions.none())

    @channel_whitelist.command(name="add")
    async def channel_whitelist_add(self, ctx: commands.Context, channel: str):
        """Aggiunge un canale alla whitelist e attiva la modalita whitelist."""
        cid = self._channel_id_from_argument(channel)
        if cid is None or ctx.guild.get_channel_or_thread(cid) is None:
            return await ctx.send("Canale non valido. Usa una menzione `#canale` oppure il suo ID.")
        await self.config.guild(ctx.guild).channel_mode.set("whitelist")
        async with self.config.guild(ctx.guild).whitelist_channels() as channels:
            if cid not in channels:
                channels.append(cid)
        await ctx.send(f"<#{cid}> aggiunto alla whitelist.", allowed_mentions=discord.AllowedMentions.none())

    @channel_whitelist.command(name="remove")
    async def channel_whitelist_remove(self, ctx: commands.Context, channel: str):
        """Rimuove un canale dalla whitelist."""
        cid = self._channel_id_from_argument(channel)
        if cid is None:
            return await ctx.send("Canale non valido.")
        async with self.config.guild(ctx.guild).whitelist_channels() as channels:
            if cid not in channels:
                return await ctx.send("Quel canale non e presente nella whitelist.")
            channels.remove(cid)
        await ctx.send("Canale rimosso dalla whitelist.")

    @channel_whitelist.command(name="clear")
    async def channel_whitelist_clear(self, ctx: commands.Context):
        """Svuota la whitelist."""
        await self.config.guild(ctx.guild).whitelist_channels.set([])
        await self.config.guild(ctx.guild).channel_mode.set("whitelist")
        await ctx.send("Whitelist svuotata.")

    @channel.group(name="blacklist", invoke_without_command=True)
    async def channel_blacklist(self, ctx: commands.Context):
        """Ignora i canali presenti nella blacklist."""
        await self.config.guild(ctx.guild).channel_mode.set("blacklist")
        channels = await self.config.guild(ctx.guild).blacklist_channels()
        text = "\n".join(f"- <#{cid}>" for cid in channels) if channels else "Blacklist vuota."
        await ctx.send("Modalita **blacklist** attiva.\n" + text, allowed_mentions=discord.AllowedMentions.none())

    @channel_blacklist.command(name="add")
    async def channel_blacklist_add(self, ctx: commands.Context, channel: str):
        """Aggiunge un canale alla blacklist e attiva la modalita blacklist."""
        cid = self._channel_id_from_argument(channel)
        if cid is None or ctx.guild.get_channel_or_thread(cid) is None:
            return await ctx.send("Canale non valido. Usa una menzione `#canale` oppure il suo ID.")
        await self.config.guild(ctx.guild).channel_mode.set("blacklist")
        async with self.config.guild(ctx.guild).blacklist_channels() as channels:
            if cid not in channels:
                channels.append(cid)
        await ctx.send(f"<#{cid}> aggiunto alla blacklist.", allowed_mentions=discord.AllowedMentions.none())

    @channel_blacklist.command(name="remove")
    async def channel_blacklist_remove(self, ctx: commands.Context, channel: str):
        """Rimuove un canale dalla blacklist."""
        cid = self._channel_id_from_argument(channel)
        if cid is None:
            return await ctx.send("Canale non valido.")
        async with self.config.guild(ctx.guild).blacklist_channels() as channels:
            if cid not in channels:
                return await ctx.send("Quel canale non e presente nella blacklist.")
            channels.remove(cid)
        await ctx.send("Canale rimosso dalla blacklist.")

    @channel_blacklist.command(name="clear")
    async def channel_blacklist_clear(self, ctx: commands.Context):
        """Svuota la blacklist."""
        await self.config.guild(ctx.guild).blacklist_channels.set([])
        await self.config.guild(ctx.guild).channel_mode.set("blacklist")
        await ctx.send("Blacklist svuotata.")

    @commands.group(name="swear", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def swear(self, ctx: commands.Context):
        """Gestisce le bestemmie complete aggiunte manualmente."""
        await ctx.send(
            f"Usa `{ctx.clean_prefix}swear add <bestemmia>`, "
            f"`{ctx.clean_prefix}swear remove <bestemmia>`, `{ctx.clean_prefix}swear list` "
            f"o `{ctx.clean_prefix}swear set <user_id> <numero>`."
        )

    @swear.command(name="set", aliases=["fix"])
    async def swear_set_count(self, ctx: commands.Context, user_id: str, count: int):
        """Imposta manualmente il conteggio di un utente per correggere miscount."""
        uid = self._extract_id(user_id)
        if uid is None:
            return await ctx.send("Usa un ID Discord valido o una menzione.")
        if count < 0:
            return await ctx.send("Il conteggio non puo essere negativo.")
        previous, total = await self._set_member_count(ctx.guild, uid, count)
        await ctx.send(
            f"Conteggio di <@{uid}> corretto: **{previous} -> {count}**. Totale server: **{total}**.",
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )

    @swear.command(name="add")
    async def swear_add(self, ctx: commands.Context, *, phrase: str):
        """Aggiunge una bestemmia completa al riconoscimento diretto."""
        phrase = phrase.strip()
        normalized = self._normalize(phrase)
        if len(normalized.replace(" ", "")) < 4:
            return await ctx.send("La frase e troppo corta.")
        if len(phrase) > 200:
            return await ctx.send("La frase e troppo lunga (massimo 200 caratteri).")
        async with self.config.guild(ctx.guild).custom_swears() as values:
            known = {self._normalize(value) for value in values}
            if normalized in known:
                return await ctx.send("Questa bestemmia e gia presente.")
            values.append(phrase)
        self._term_pattern.cache_clear()
        await ctx.send(f"Aggiunta: `{phrase}`")

    @swear.command(name="remove", aliases=["del", "delete"])
    async def swear_remove(self, ctx: commands.Context, *, phrase: str):
        """Rimuove una bestemmia completa dalla lista manuale."""
        target = self._normalize(phrase)
        async with self.config.guild(ctx.guild).custom_swears() as values:
            for existing in list(values):
                if self._normalize(existing) == target:
                    values.remove(existing)
                    self._term_pattern.cache_clear()
                    return await ctx.send(f"Rimossa: `{existing}`")
        await ctx.send("Bestemmia non trovata nella lista manuale.")

    @swear.command(name="list")
    async def swear_list(self, ctx: commands.Context):
        """Mostra le bestemmie complete aggiunte manualmente."""
        values = await self.config.guild(ctx.guild).custom_swears()
        if not values:
            return await ctx.send("Nessuna bestemmia manuale configurata.")
        lines = [f"`{index}.` {value}" for index, value in enumerate(values, 1)]
        chunks = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1800:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)
        for chunk in chunks:
            await ctx.send(chunk)

    @commands.group(name="message", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def message(self, ctx: commands.Context):
        """Configura il messaggio inviato quando viene rilevata una bestemmia."""
        current = await self.config.guild(ctx.guild).reply_message()
        await ctx.send(
            "Messaggio attuale:\n"
            f"```\n{current}\n```\n"
            f"Usa `{ctx.clean_prefix}message set <testo>` oppure `{ctx.clean_prefix}message placeholders`."
        )

    @message.command(name="set")
    async def message_set(self, ctx: commands.Context, *, text: str):
        """Imposta il messaggio di risposta; supporta conteggio utente e totale server."""
        text = text.strip()
        if not text:
            return await ctx.send("Il messaggio non puo essere vuoto.")
        if len(text) > 2000:
            return await ctx.send("Il messaggio non puo superare 2000 caratteri.")
        error = self._validate_template(text)
        if error:
            return await ctx.send(error)
        await self.config.guild(ctx.guild).reply_message.set(text)
        await ctx.send("Messaggio aggiornato.")

    @message.command(name="reset")
    async def message_reset(self, ctx: commands.Context):
        """Ripristina il messaggio standard."""
        await self.config.guild(ctx.guild).reply_message.set(DEFAULT_REPLY)
        await ctx.send("Messaggio ripristinato.")

    @message.command(name="placeholders", aliases=["vars", "variabili"])
    async def message_placeholders(self, ctx: commands.Context):
        """Mostra tutti i placeholder utilizzabili nel messaggio."""
        lines = [f"`{{{name}}}` - {description}" for name, description in PLACEHOLDER_DESCRIPTIONS.items()]
        await ctx.send("**Placeholder disponibili**\n" + "\n".join(lines))

    @app_commands.command(name="top", description="Mostra la top 10 delle bestemmie rilevate nel server.")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction):
        """Leaderboard slash-only con menzioni e totale complessivo del server."""
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Questo comando puo essere usato solo in un server.",
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)
        all_members = await self.config.all_members(guild)
        entries = [
            (int(uid), int(data.get("count", 0) or 0))
            for uid, data in all_members.items()
            if int(data.get("count", 0) or 0) > 0
        ]
        total = sum(count for _, count in entries)
        ranking = sorted(entries, key=lambda item: item[1], reverse=True)[:10]

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for index, (uid, count) in enumerate(ranking, 1):
            prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
            lines.append(f"{prefix} <@{uid}> — **{count}**")

        embed = discord.Embed(
            title="🏆 Classifica Swear Jar",
            description="\n".join(lines) if lines else "La leaderboard e ancora vuota.",
            colour=discord.Colour.gold(),
        )
        embed.add_field(
            name="Totale server",
            value=f"**{total}** bestemmie rilevate",
            inline=False,
        )
        embed.set_footer(text="Top 10 del server")
        await interaction.followup.send(
            embed=embed,
            allowed_mentions=discord.AllowedMentions(
                users=True,
                roles=False,
                everyone=False,
            ),
        )
