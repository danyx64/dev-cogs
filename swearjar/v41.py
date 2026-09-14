import asyncio
import re
from typing import List, Optional, Sequence, Tuple

import discord
from redbot.core import commands

from .v4 import DEFAULT_DEITIES, DEFAULT_PROFANITIES, SwearJar as BaseSwearJar

MAX_WORD_GAP = 1
AMBIGUOUS_DEITIES = {"maria", "signore"}


class SwearJar(BaseSwearJar):
    __version__ = "4.1.0"

    @classmethod
    def _safe_deities(cls, values: Sequence[str]) -> List[str]:
        result = []
        seen = set()
        for value in values:
            key = cls._normalize(value)
            if not key or key in AMBIGUOUS_DEITIES or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    async def _sync_dictionary_files(self):
        deities = self._safe_deities(self._read_dictionary_file(self.deities_file, DEFAULT_DEITIES))
        profanities = self._read_dictionary_file(self.profanities_file, DEFAULT_PROFANITIES)
        for guild in self.bot.guilds:
            await self.config.guild(guild).deities.set(deities)
            await self.config.guild(guild).profanities.set(profanities)

    @classmethod
    def _term_spans(cls, content: str, term: str) -> List[Tuple[int, int]]:
        pattern = cls._term_pattern(term)
        return [] if pattern is None else [m.span() for m in pattern.finditer(content)]

    @classmethod
    def _fuzzy_spans(cls, content: str, term: str) -> List[Tuple[int, int]]:
        target = cls._collapse_runs(cls._normalize(term))
        if " " in target or len(target) < 7:
            return []
        result = []
        for match in re.finditer(r"[a-z]+", content):
            word = cls._collapse_runs(match.group(0))
            if len(word) < 7 or word[0] != target[0] or word[-1] != target[-1]:
                continue
            if cls._edit_distance_at_most_one(word, target):
                result.append(match.span())
        return result

    @classmethod
    def _collect_matches(cls, content: str, terms: Sequence[str], *, allow_fuzzy: bool = False):
        result = []
        seen = set()
        for term in terms:
            key = cls._normalize(term)
            if not key or key in seen:
                continue
            seen.add(key)
            spans = cls._term_spans(content, term)
            if not spans and allow_fuzzy:
                spans = cls._fuzzy_spans(content, term)
            result.extend((term, start, end) for start, end in spans)
        return result

    @classmethod
    def _valid_custom(cls, phrase: str, deities: Sequence[str]) -> bool:
        normalized = cls._normalize(phrase)
        if len(re.findall(r"[a-z]+", normalized)) < 2:
            return False
        if len(normalized.replace(" ", "")) < 5:
            return False
        for variant in cls._normalization_variants(phrase):
            for deity in cls._safe_deities(deities):
                if cls._term_spans(variant, deity):
                    return True
        return False

    async def _migrate_legacy_words(self):
        for guild in self.bot.guilds:
            conf = self.config.guild(guild)
            deities = self._safe_deities(await conf.deities())
            legacy = await conf.words()
            current = await conf.custom_swears()
            cleaned = [value for value in current if self._valid_custom(value, deities)]
            known = {self._normalize(value) for value in cleaned}
            for value in legacy:
                key = self._normalize(value)
                if key and key not in known and self._valid_custom(value, deities):
                    cleaned.append(value)
                    known.add(key)
            if cleaned != current:
                await conf.custom_swears.set(cleaned)
            if legacy:
                await conf.words.set([])

    @classmethod
    def _find_custom_violation(cls, content: str, custom_swears: Sequence[str], deities=None) -> Optional[str]:
        deities = cls._safe_deities(deities or DEFAULT_DEITIES)
        for phrase in custom_swears:
            if not cls._valid_custom(phrase, deities):
                continue
            for variant in cls._normalization_variants(content):
                if cls._term_spans(variant, phrase):
                    return phrase
        return None

    @classmethod
    def _find_composed_violation(cls, content: str, deities: Sequence[str], profanities: Sequence[str]):
        best = None
        best_score = None
        for normalized in cls._normalization_variants(content):
            deity_matches = cls._collect_matches(normalized, cls._safe_deities(deities))
            profanity_matches = cls._collect_matches(normalized, profanities, allow_fuzzy=True)
            for deity in deity_matches:
                for profanity in profanity_matches:
                    gap = cls._word_gap(normalized, deity, profanity)
                    if gap > MAX_WORD_GAP:
                        continue
                    score = (gap, abs(deity[1] - profanity[1]))
                    if best_score is None or score < best_score:
                        best_score = score
                        best = (deity[0], profanity[0])
        return best

    async def _detect(self, guild_config, content: str):
        deities = self._safe_deities(await guild_config.deities())
        custom = self._find_custom_violation(content, await guild_config.custom_swears(), deities)
        if custom is not None:
            return custom, "", ""
        pair = self._find_composed_violation(content, deities, await guild_config.profanities())
        if pair is None:
            return None
        return f"{pair[0]} + {pair[1]}", pair[0], pair[1]

    async def diagnose_text(self, guild: discord.Guild, content: str) -> str:
        conf = self.config.guild(guild)
        result = await self._detect(conf, content)
        normalized = self._normalize(content) or "-"
        if result is None:
            return f"**Risultato:** NON rilevata\n**Normalizzato:** `{normalized}`"
        word, deity, profanity = result
        source = "regola manuale" if not deity else "divinita + parolaccia"
        details = f"\n**Match:** `{word}`"
        if deity:
            details += f"\n**Divinita:** `{deity}`\n**Parolaccia:** `{profanity}`"
        return f"**Risultato:** RILEVATA\n**Origine:** {source}{details}\n**Normalizzato:** `{normalized}`"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot or not message.content:
            return
        if not await self.config.guild(message.guild).enabled():
            return
        if not await self._channel_allowed(message.guild, message.channel.id):
            return

        guild_config = self.config.guild(message.guild)
        detected = await self._detect(guild_config, message.content)
        if detected is None:
            return
        detected_word, deity, profanity = detected

        key = (message.guild.id, message.author.id)
        lock = self._count_locks.setdefault(key, asyncio.Lock())
        async with lock:
            member_group = self.config.member(message.author)
            new_count = (await member_group.count()) + 1
            await member_group.count.set(new_count)

        total = await self._server_total(message.guild)
        values = {
            "mention": message.author.mention,
            "user": message.author.mention,
            "username": message.author.name,
            "displayname": message.author.display_name,
            "user_id": message.author.id,
            "count": new_count,
            "user_count": new_count,
            "server_count": total,
            "server_total": total,
            "total": total,
            "word": detected_word,
            "deity": deity,
            "profanity": profanity,
            "channel": getattr(message.channel, "mention", f"#{message.channel}"),
            "guild": message.guild.name,
            "server": message.guild.name,
        }
        reply = self._format_reply(await guild_config.reply_message(), values)
        if not reply.strip():
            return

        reply_lock = self._reply_locks.setdefault(message.channel.id, asyncio.Lock())
        try:
            async with reply_lock:
                await message.reply(
                    reply[:2000],
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )
        except (discord.Forbidden, discord.HTTPException):
            pass
