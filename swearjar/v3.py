import re
import unicodedata
from functools import lru_cache
from typing import List, Optional, Sequence, Tuple

import discord
from redbot.core import app_commands

from .swearjar import SwearJar as LegacySwearJar


MAX_WORD_GAP = 4
CHAR_SUBSTITUTIONS = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
        # Confusabili cirillici comuni usati per aggirare i filtri.
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


class SwearJar(LegacySwearJar):
    """SwearJar con riconoscimento anti-elusione e leaderboard slash."""

    __version__ = "3.0.0"

    @staticmethod
    def _normalize(text: str) -> str:
        # Gli URL non devono creare falsi conteggi se contengono una parola vietata.
        text = re.sub(r"https?://\S+|www\.\S+", " ", text, flags=re.IGNORECASE)
        text = unicodedata.normalize("NFKD", text.casefold())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.translate(CHAR_SUBSTITUTIONS)
        text = re.sub(r"[^a-z]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _normalization_variants(cls, text: str) -> List[str]:
        # Simboli come @ e $ sono ambigui: possono essere lettere mascherate oppure
        # semplici separatori. Proviamo entrambe le letture per non perdere casi utili.
        variants = [cls._normalize(text), cls._normalize(text.translate(SYMBOL_LEET_SUBSTITUTIONS))]
        return list(dict.fromkeys(value for value in variants if value))

    @staticmethod
    def _collapse_runs(value: str) -> str:
        # Permette di riconoscere anche allungamenti tipo "diiiooo" / "pooorcooo".
        return re.sub(r"([a-z])\1+", r"\1", value)

    @classmethod
    @lru_cache(maxsize=4096)
    def _term_pattern(cls, term: str) -> Optional[re.Pattern]:
        normalized = cls._normalize(term)
        compact = cls._collapse_runs(normalized.replace(" ", ""))
        if not compact:
            return None

        # Dopo la normalizzazione i separatori diventano spazi: \s* permette
        # forme come d.i.o, d i o e p-o-r-c-o senza fare substring arbitrarie.
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
        """Tollera un singolo typo solo sulle parole lunghe, per limitare i falsi positivi."""
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
    def _word_gap(normalized_content: str, first: Tuple[str, int, int], second: Tuple[str, int, int]) -> int:
        _, start_a, end_a = first
        _, start_b, end_b = second
        if start_a <= start_b:
            between = normalized_content[end_a:start_b]
        else:
            between = normalized_content[end_b:start_a]
        return len(re.findall(r"[a-z]+", between))

    @classmethod
    def _find_violation(cls, content: str, deities: List[str], profanities: List[str]):
        best_pair = None
        best_gap = None

        for normalized in cls._normalization_variants(content):
            deity_matches = cls._collect_matches(normalized, deities)
            if not deity_matches:
                continue

            profanity_matches = cls._collect_matches(normalized, profanities, allow_fuzzy=True)
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

    @app_commands.command(name="top", description="Mostra la top 10 delle bestemmie rilevate nel server.")
    @app_commands.guild_only()
    async def top(self, interaction: discord.Interaction):
        """Leaderboard slash-only: top 10 e totale complessivo del server."""
        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message(
                "Questo comando puo essere usato solo in un server.", ephemeral=True
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
            member = guild.get_member(uid)
            if member is not None:
                name = discord.utils.escape_markdown(member.display_name)
            else:
                user = self.bot.get_user(uid)
                name = discord.utils.escape_markdown(str(user)) if user else f"Utente {uid}"
            prefix = medals[index - 1] if index <= 3 else f"**{index}.**"
            lines.append(f"{prefix} **{name}** — **{count}**")

        embed = discord.Embed(
            title="🏆 Classifica Swear Jar",
            description="\n".join(lines) if lines else "La leaderboard e' ancora vuota.",
            colour=discord.Colour.gold(),
        )
        embed.add_field(
            name="Totale server",
            value=f"**{total}** bestemmie rilevate",
            inline=False,
        )
        embed.set_footer(text="Top 10 del server")
        await interaction.followup.send(embed=embed)
