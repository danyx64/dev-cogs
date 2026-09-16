from __future__ import annotations

import asyncio
import base64
import codecs
import logging
import random
import re
from typing import Any, Dict, List, Optional, Tuple

import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


log = logging.getLogger("red.danyx64.alter")

CONFIG_ID = 0xA17E2026
WEBHOOK_NAME = "Alter"
DEFAULT_MESSAGE = "BT Failed: Too many attempts"
PLACEHOLDER_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")
ALLOWED_PLACEHOLDERS = {
    "content",
    "name",
    "username",
    "mention",
    "id",
    "channel",
    "server",
    "attachments",
}

MODE_DESCRIPTIONS = {
    "random": "sceglie a caso una trasformazione o una frase custom abilitata",
    "fixed": "usa il messaggio configurato con .alter messaggio",
    "spoiler": "modifica il testo e poi nasconde tutto dietro uno spoiler",
    "spoilerwords": "modifica il testo e mette ogni parola in uno spoiler separato",
    "spoilerrandom": "modifica il testo e nasconde casualmente molte parole",
    "mock": "alterna maiuscole e minuscole",
    "randomcase": "maiuscole/minuscole casuali",
    "reverse": "inverte l'intero testo",
    "wordreverse": "inverte ogni parola singolarmente",
    "shuffle": "mischia l'ordine delle parole",
    "scramble": "mischia le lettere interne delle parole",
    "redact": "oscura casualmente alcune parole",
    "censor": "censura casualmente lettere dentro le parole",
    "typo": "inserisce errori casuali nelle parole",
    "stutter": "aggiunge balbettii casuali",
    "duplicate": "duplica casualmente alcune parole",
    "leet": "converte il testo in leetspeak",
    "rot13": "applica ROT13",
    "caesar": "sposta le lettere di 3 posizioni",
    "base64": "codifica il testo in Base64",
    "binary": "converte il testo in binario UTF-8",
    "morse": "converte lettere e numeri in codice Morse",
    "novowels": "rimuove molte vocali",
    "vowelswap": "ruota le vocali",
    "spaceout": "separa i caratteri con spazi",
    "dots": "separa le parole con puntini sospensivi",
    "clap": "separa le parole con 👏",
    "snake": "trasforma gli spazi in underscore e altera il case",
    "fullwidth": "converte i caratteri ASCII in full-width",
    "tiny": "usa lettere Unicode piccole dove possibile",
    "brackets": "trasforma e racchiude ogni parola tra parentesi",
    "strike": "modifica il testo e applica barrato Discord",
    "underline": "modifica il testo e applica sottolineato Discord",
    "keyboard": "sposta alcune lettere verso tasti QWERTY vicini",
    "piglatin": "applica una semplice trasformazione Pig Latin",
    "punctuation": "aggiunge punteggiatura caotica",
    "echo": "ripete frammenti/parole con effetto eco",
}

RANDOM_MODES = tuple(mode for mode in MODE_DESCRIPTIONS if mode not in {"random", "fixed"})

HIDDEN_BASE_MODES = (
    "mock",
    "randomcase",
    "typo",
    "leet",
    "wordreverse",
    "scramble",
    "vowelswap",
    "keyboard",
)

MORSE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..", "0": "-----", "1": ".----", "2": "..---", "3": "...--",
    "4": "....-", "5": ".....", "6": "-....", "7": "--...", "8": "---..",
    "9": "----.", ".": ".-.-.-", ",": "--..--", "?": "..--..", "!": "-.-.--",
}

TINY_MAP = str.maketrans({
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ", "g": "ᵍ",
    "h": "ʰ", "i": "ⁱ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ", "n": "ⁿ",
    "o": "ᵒ", "p": "ᵖ", "q": "ᑫ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
})

QWERTY_NEIGHBORS = {
    "q": "w", "w": "qe", "e": "wr", "r": "et", "t": "ry", "y": "tu", "u": "yi",
    "i": "uo", "o": "ip", "p": "o", "a": "s", "s": "ad", "d": "sf", "f": "dg",
    "g": "fh", "h": "gj", "j": "hk", "k": "jl", "l": "k", "z": "x", "x": "zc",
    "c": "xv", "v": "cb", "b": "vn", "n": "bm", "m": "n",
}


class Alter(commands.Cog):
    """Ripubblica i messaggi con un webhook usando nome/avatar dell'autore e un effetto configurabile."""

    __author__ = "danyx64"
    __version__ = "1.2.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=CONFIG_ID, force_registration=True)
        self.config.register_guild(
            enabled=False,
            channel_id=None,
            webhook_id=None,
            message_template=DEFAULT_MESSAGE,
            mode="random",
            custom_presets={},
        )
        self._webhook_cache: Dict[int, discord.Webhook] = {}
        self._webhook_locks: Dict[int, asyncio.Lock] = {}
        self._rng = random.SystemRandom()

    async def red_delete_data_for_user(self, *, requester: str, user_id: int) -> None:
        return

    def _lock_for(self, guild_id: int) -> asyncio.Lock:
        lock = self._webhook_locks.get(guild_id)
        if lock is None:
            lock = asyncio.Lock()
            self._webhook_locks[guild_id] = lock
        return lock

    @staticmethod
    def _missing_permissions(channel: discord.TextChannel) -> list[str]:
        me = channel.guild.me
        if me is None:
            return ["Manage Webhooks", "Manage Messages", "View Channel"]
        perms = channel.permissions_for(me)
        missing = []
        if not perms.manage_webhooks:
            missing.append("Manage Webhooks")
        if not perms.manage_messages:
            missing.append("Manage Messages")
        if not perms.view_channel:
            missing.append("View Channel")
        return missing

    async def _find_webhook(
        self,
        channel: discord.TextChannel,
        webhook_id: Optional[int],
    ) -> Optional[discord.Webhook]:
        if not webhook_id:
            return None

        cached = self._webhook_cache.get(channel.guild.id)
        if cached is not None and cached.id == webhook_id and cached.channel_id == channel.id:
            return cached

        try:
            webhooks = await channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return None

        for webhook in webhooks:
            if webhook.id == webhook_id and webhook.type is discord.WebhookType.incoming:
                self._webhook_cache[channel.guild.id] = webhook
                return webhook
        return None

    async def _create_webhook(self, channel: discord.TextChannel) -> discord.Webhook:
        webhook = await channel.create_webhook(name=WEBHOOK_NAME, reason="Alter cog webhook proxy")
        self._webhook_cache[channel.guild.id] = webhook
        await self.config.guild(channel.guild).webhook_id.set(webhook.id)
        return webhook

    async def _ensure_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        async with self._lock_for(channel.guild.id):
            conf = self.config.guild(channel.guild)
            webhook = await self._find_webhook(channel, await conf.webhook_id())
            if webhook is not None:
                return webhook

            missing = self._missing_permissions(channel)
            if missing:
                log.warning(
                    "Alter cannot create webhook in guild %s channel %s; missing: %s",
                    channel.guild.id,
                    channel.id,
                    ", ".join(missing),
                )
                return None

            try:
                return await self._create_webhook(channel)
            except (discord.Forbidden, discord.HTTPException) as exc:
                log.warning(
                    "Alter failed to create webhook in guild %s channel %s: %r",
                    channel.guild.id,
                    channel.id,
                    exc,
                )
                return None

    async def _delete_configured_webhook(self, guild: discord.Guild) -> None:
        conf = self.config.guild(guild)
        channel = guild.get_channel((await conf.channel_id()) or 0)
        webhook_id = await conf.webhook_id()
        self._webhook_cache.pop(guild.id, None)
        if not isinstance(channel, discord.TextChannel) or not webhook_id:
            return

        webhook = await self._find_webhook(channel, webhook_id)
        if webhook is None:
            return
        try:
            await webhook.delete(reason="Alter configuration removed")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            pass

    @staticmethod
    def _unknown_placeholders(template: str) -> list[str]:
        return sorted(
            {
                match.group(1)
                for match in PLACEHOLDER_RE.finditer(template)
                if match.group(1) not in ALLOWED_PLACEHOLDERS
            }
        )

    @staticmethod
    def _render_template(template: str, message: discord.Message) -> str:
        values = {
            "content": message.content or "",
            "name": message.author.display_name,
            "username": message.author.name,
            "mention": message.author.mention,
            "id": str(message.author.id),
            "channel": message.channel.mention,
            "server": message.guild.name if message.guild else "",
            "attachments": " ".join(a.url for a in message.attachments),
        }
        rendered = template
        for key, value in values.items():
            rendered = rendered.replace("{" + key + "}", str(value))
        return rendered.replace("\\n", "\n").strip()

    @staticmethod
    def _preset_key(name: str) -> str:
        value = re.sub(r"[^\w-]+", "-", name.strip().casefold(), flags=re.UNICODE)
        value = re.sub(r"-+", "-", value).strip("-")
        return value[:40]

    @staticmethod
    def _clean_mode(mode: str) -> str:
        selected = mode.strip().lower()
        aliases = {
            "spoilerall": "spoiler",
            "spoiler-all": "spoiler",
            "spoiler-words": "spoilerwords",
            "spoiler-random": "spoilerrandom",
            "word-reverse": "wordreverse",
            "random-case": "randomcase",
            "b64": "base64",
            "no-vowels": "novowels",
            "full-width": "fullwidth",
            "pig-latin": "piglatin",
        }
        return aliases.get(selected, selected)

    @staticmethod
    def _mock(text: str) -> str:
        upper = True
        output = []
        for char in text:
            if char.isalpha():
                output.append(char.upper() if upper else char.lower())
                upper = not upper
            else:
                output.append(char)
        return "".join(output)

    def _random_case(self, text: str) -> str:
        return "".join(
            self._rng.choice((char.lower(), char.upper())) if char.isalpha() else char
            for char in text
        )

    def _spoiler_random(self, text: str) -> str:
        changed = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            word = match.group(0)
            if self._rng.random() < 0.7:
                changed += 1
                return f"||{word}||"
            return word

        result = re.sub(r"\S+", repl, text)
        if text and changed == 0:
            result = re.sub(r"\S+", lambda m: f"||{m.group(0)}||", result, count=1)
        return result

    def _redact(self, text: str) -> str:
        changed = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            word = match.group(0)
            if self._rng.random() < 0.55:
                changed += 1
                return "█" * min(max(len(word), 3), 12)
            return word

        result = re.sub(r"\S+", repl, text)
        if text and changed == 0:
            result = re.sub(r"\S+", lambda m: "█" * min(max(len(m.group(0)), 3), 12), result, count=1)
        return result

    def _censor(self, text: str) -> str:
        chars = list(text)
        candidates = [i for i, ch in enumerate(chars) if ch.isalpha()]
        if not candidates:
            return text
        self._rng.shuffle(candidates)
        for index in candidates[: max(1, len(candidates) // 4)]:
            chars[index] = "*"
        return "".join(chars)

    def _typo(self, text: str) -> str:
        mutations = 0

        def mutate(word: str) -> str:
            nonlocal mutations
            if len(word) < 3 or self._rng.random() > 0.5:
                return word
            chars = list(word)
            action = self._rng.choice(("drop", "swap", "double"))
            index = self._rng.randrange(1, max(2, len(chars) - 1))
            index = min(index, len(chars) - 1)
            if action == "drop" and len(chars) > 2:
                del chars[index]
            elif action == "swap" and index + 1 < len(chars):
                chars[index], chars[index + 1] = chars[index + 1], chars[index]
            else:
                chars.insert(index, chars[index])
            mutations += 1
            return "".join(chars)

        result = re.sub(r"\b[^\s]+\b", lambda m: mutate(m.group(0)), text)
        if text and mutations == 0:
            return self._force_visible_change(result)
        return result

    def _stutter(self, text: str) -> str:
        changed = 0

        def repl(match: re.Match[str]) -> str:
            nonlocal changed
            word = match.group(0)
            if len(word) < 2 or self._rng.random() > 0.45:
                return word
            changed += 1
            repeats = self._rng.randint(1, 2)
            return (word[0] + "-") * repeats + word

        result = re.sub(r"\b\w+\b", repl, text)
        if text and changed == 0:
            return self._force_visible_change(result)
        return result

    def _duplicate(self, text: str) -> str:
        words = text.split()
        if not words:
            return text
        index = self._rng.randrange(len(words))
        words.insert(index, words[index])
        if len(words) > 2 and self._rng.random() < 0.5:
            index = self._rng.randrange(len(words))
            words.insert(index, words[index])
        return " ".join(words)

    @staticmethod
    def _leet(text: str) -> str:
        table = str.maketrans({
            "a": "4", "A": "4", "e": "3", "E": "3", "i": "1", "I": "1",
            "o": "0", "O": "0", "s": "5", "S": "5", "t": "7", "T": "7",
            "b": "8", "B": "8", "g": "9", "G": "9",
        })
        return text.translate(table)

    @staticmethod
    def _caesar(text: str) -> str:
        output = []
        for char in text:
            if "a" <= char <= "z":
                output.append(chr((ord(char) - 97 + 3) % 26 + 97))
            elif "A" <= char <= "Z":
                output.append(chr((ord(char) - 65 + 3) % 26 + 65))
            else:
                output.append(char)
        return "".join(output)

    @staticmethod
    def _morse(text: str) -> str:
        words = []
        for word in text.upper().split():
            encoded = [MORSE.get(char, char) for char in word]
            words.append(" ".join(encoded))
        return " / ".join(words)

    def _remove_vowels(self, text: str) -> str:
        result = re.sub(r"[aeiouAEIOUàèéìòùÀÈÉÌÒÙ]", "", text)
        return result or text

    @staticmethod
    def _vowel_swap(text: str) -> str:
        table = str.maketrans({
            "a": "e", "e": "i", "i": "o", "o": "u", "u": "a",
            "A": "E", "E": "I", "I": "O", "O": "U", "U": "A",
        })
        return text.translate(table)

    @staticmethod
    def _fullwidth(text: str) -> str:
        out = []
        for char in text:
            code = ord(char)
            if char == " ":
                out.append("　")
            elif 33 <= code <= 126:
                out.append(chr(code + 0xFEE0))
            else:
                out.append(char)
        return "".join(out)

    def _keyboard(self, text: str) -> str:
        chars = list(text)
        candidates = [i for i, ch in enumerate(chars) if ch.casefold() in QWERTY_NEIGHBORS]
        if not candidates:
            return text
        self._rng.shuffle(candidates)
        for index in candidates[: max(1, len(candidates) // 5)]:
            original = chars[index]
            neighbors = QWERTY_NEIGHBORS[original.casefold()]
            replacement = self._rng.choice(neighbors)
            chars[index] = replacement.upper() if original.isupper() else replacement
        return "".join(chars)

    def _scramble(self, text: str) -> str:
        changed = 0

        def mutate(match: re.Match[str]) -> str:
            nonlocal changed
            word = match.group(0)
            if len(word) < 4:
                return word
            middle = list(word[1:-1])
            original = middle[:]
            for _ in range(5):
                self._rng.shuffle(middle)
                if middle != original:
                    break
            if middle != original:
                changed += 1
            return word[0] + "".join(middle) + word[-1]

        result = re.sub(r"\b\w{4,}\b", mutate, text)
        if text and changed == 0:
            return self._force_visible_change(result)
        return result

    @staticmethod
    def _piglatin(text: str) -> str:
        def convert(match: re.Match[str]) -> str:
            word = match.group(0)
            if len(word) < 2:
                return word + "ay"
            lower = word.casefold()
            if lower[0] in "aeiou":
                return word + "way"
            result = word[1:] + word[0] + "ay"
            return result

        return re.sub(r"\b[A-Za-zÀ-ÿ]+\b", convert, text)

    def _punctuation(self, text: str) -> str:
        words = text.split()
        if not words:
            return text + "?!"
        marks = ("!", "?", "...", "?!", "!!", "??")
        return " ".join(word + self._rng.choice(marks) for word in words)

    def _echo(self, text: str) -> str:
        words = text.split()
        if not words:
            return self._force_visible_change(text)
        out = []
        for word in words:
            out.append(word)
            if self._rng.random() < 0.35:
                out.append(word.lower())
        if len(out) == len(words):
            out.append(words[-1].lower())
        return " ".join(out)

    @staticmethod
    def _force_visible_change(text: str) -> str:
        if not text:
            return "[alterato]"
        for index, char in enumerate(text):
            if char.isalpha():
                replacement = char.swapcase()
                if replacement != char:
                    return text[:index] + replacement + text[index + 1 :]
            if char.isdigit():
                replacement = str((int(char) + 1) % 10)
                return text[:index] + replacement + text[index + 1 :]
        return text + " ~"

    def _ensure_changed(self, original: str, transformed: str) -> str:
        transformed = transformed.strip()
        if not original:
            return transformed or "[alterato]"
        if transformed == original:
            return self._force_visible_change(original)
        return transformed or self._force_visible_change(original)

    def _hidden_source(self, text: str) -> str:
        mode = self._rng.choice(HIDDEN_BASE_MODES)
        return self._transform_builtin(mode, text, allow_hidden=False)

    def _transform_builtin(self, mode: str, text: str, *, allow_hidden: bool = True) -> str:
        if mode == "spoiler" and allow_hidden:
            base = self._hidden_source(text)
            return f"||{base}||"
        if mode == "spoilerwords" and allow_hidden:
            base = self._hidden_source(text)
            return re.sub(r"\S+", lambda m: f"||{m.group(0)}||", base)
        if mode == "spoilerrandom" and allow_hidden:
            return self._spoiler_random(self._hidden_source(text))
        if mode == "mock":
            return self._mock(text)
        if mode == "randomcase":
            return self._random_case(text)
        if mode == "reverse":
            return text[::-1]
        if mode == "wordreverse":
            return re.sub(r"\S+", lambda m: m.group(0)[::-1], text)
        if mode == "shuffle":
            words = text.split()
            self._rng.shuffle(words)
            return " ".join(words)
        if mode == "scramble":
            return self._scramble(text)
        if mode == "redact":
            return self._redact(text)
        if mode == "censor":
            return self._censor(text)
        if mode == "typo":
            return self._typo(text)
        if mode == "stutter":
            return self._stutter(text)
        if mode == "duplicate":
            return self._duplicate(text)
        if mode == "leet":
            return self._leet(text)
        if mode == "rot13":
            return codecs.encode(text, "rot_13")
        if mode == "caesar":
            return self._caesar(text)
        if mode == "base64":
            return base64.b64encode(text.encode("utf-8")).decode("ascii") if text else ""
        if mode == "binary":
            return " ".join(f"{byte:08b}" for byte in text.encode("utf-8"))
        if mode == "morse":
            return self._morse(text)
        if mode == "novowels":
            return self._remove_vowels(text)
        if mode == "vowelswap":
            return self._vowel_swap(text)
        if mode == "spaceout":
            return " ".join(text)
        if mode == "dots":
            return "... ".join(text.split())
        if mode == "clap":
            return " 👏 ".join(text.split())
        if mode == "snake":
            return self._mock(text).replace(" ", "_")
        if mode == "fullwidth":
            return self._fullwidth(text)
        if mode == "tiny":
            return text.casefold().translate(TINY_MAP)
        if mode == "brackets":
            base = self._vowel_swap(text)
            return re.sub(r"\S+", lambda m: f"[{m.group(0)}]", base)
        if mode == "strike":
            return f"~~{self._hidden_source(text)}~~"
        if mode == "underline":
            return f"__{self._hidden_source(text)}__"
        if mode == "keyboard":
            return self._keyboard(text)
        if mode == "piglatin":
            return self._piglatin(text)
        if mode == "punctuation":
            return self._punctuation(text)
        if mode == "echo":
            return self._echo(text)
        return self._force_visible_change(text)

    def _transform_text(self, mode: str, message: discord.Message) -> str:
        text = message.content or ""
        attachments = " ".join(a.url for a in message.attachments)
        transformed = self._transform_builtin(mode, text)
        transformed = self._ensure_changed(text, transformed)
        if attachments:
            transformed = (transformed + "\n" + attachments).strip()
        return transformed[:2000] or "[alterato]"

    async def _random_choice(self, guild: discord.Guild) -> str:
        presets = await self.config.guild(guild).custom_presets()
        pool = list(RANDOM_MODES)
        for key, data in presets.items():
            if isinstance(data, dict) and data.get("in_random", True) and str(data.get("text") or "").strip():
                pool.append(f"preset:{key}")
        return self._rng.choice(pool) if pool else "mock"

    async def _resolve_mode(self, guild: discord.Guild, requested: str) -> Optional[str]:
        mode = self._clean_mode(requested)
        if mode == "original":
            return "random"
        if mode in MODE_DESCRIPTIONS:
            return mode
        if mode.startswith("preset:") or mode.startswith("frase:"):
            _, raw_name = mode.split(":", 1)
            key = self._preset_key(raw_name)
            presets = await self.config.guild(guild).custom_presets()
            return f"preset:{key}" if key in presets else None
        return None

    async def _render_choice(self, message: discord.Message, requested_mode: Optional[str] = None) -> Tuple[str, str]:
        conf = self.config.guild(message.guild)
        raw_mode = requested_mode if requested_mode is not None else str(await conf.mode() or "random")
        mode = await self._resolve_mode(message.guild, raw_mode)
        if mode is None:
            mode = "random"
        if mode == "random":
            mode = await self._random_choice(message.guild)

        if mode == "fixed":
            template = str(await conf.message_template() or DEFAULT_MESSAGE)
            rendered = self._render_template(template, message)
            rendered = self._ensure_changed(message.content or "", rendered)
            if message.attachments and "{attachments}" not in template:
                rendered = (rendered + "\n" + " ".join(a.url for a in message.attachments)).strip()
            return rendered[:2000] or "[alterato]", "fixed"

        if mode.startswith("preset:"):
            key = mode.split(":", 1)[1]
            presets = await conf.custom_presets()
            data = presets.get(key)
            if not isinstance(data, dict) or not str(data.get("text") or "").strip():
                fallback = self._transform_text("mock", message)
                return fallback, "mock"
            template = str(data.get("text") or "")
            rendered = self._render_template(template, message)
            rendered = self._ensure_changed(message.content or "", rendered)
            if message.attachments and "{attachments}" not in template:
                rendered = (rendered + "\n" + " ".join(a.url for a in message.attachments)).strip()
            display = str(data.get("name") or key)
            return rendered[:2000] or "[alterato]", f"frase:{display}"

        return self._transform_text(mode, message), mode

    async def _render_message(self, message: discord.Message) -> str:
        content, _ = await self._render_choice(message)
        return content

    async def _send_as_author(
        self,
        webhook: discord.Webhook,
        message: discord.Message,
        content: str,
    ) -> None:
        username = message.author.display_name.strip()[:80] or message.author.name[:80]
        await webhook.send(
            content=content,
            username=username,
            avatar_url=str(message.author.display_avatar.url),
            allowed_mentions=discord.AllowedMentions.none(),
            wait=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if message.webhook_id is not None or message.author.bot:
            return

        conf = self.config.guild(message.guild)
        if not await conf.enabled() or message.channel.id != await conf.channel_id():
            return

        try:
            ctx = await self.bot.get_context(message)
            if ctx.valid:
                return
        except Exception:
            pass

        webhook = await self._ensure_webhook(message.channel)
        if webhook is None:
            return

        content = await self._render_message(message)

        try:
            await self._send_as_author(webhook, message, content)
        except discord.NotFound:
            self._webhook_cache.pop(message.guild.id, None)
            await conf.webhook_id.set(None)
            webhook = await self._ensure_webhook(message.channel)
            if webhook is None:
                return
            try:
                await self._send_as_author(webhook, message, content)
            except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
                log.warning("Alter webhook retry failed in guild %s: %r", message.guild.id, exc)
                return
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning("Alter webhook send failed in guild %s: %r", message.guild.id, exc)
            return

        try:
            await message.delete()
        except discord.NotFound:
            pass
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Alter could not delete message %s in guild %s: %r",
                message.id,
                message.guild.id,
                exc,
            )

    @commands.group(name="alter", invoke_without_command=True)
    @commands.guild_only()
    async def alter(self, ctx: commands.Context) -> None:
        """Configura Alter."""
        await ctx.send_help(ctx.command)

    @alter.command(name="setup", aliases=["canale", "channel"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_setup(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Imposta il canale e crea/riusa automaticamente il webhook Alter."""
        missing = self._missing_permissions(channel)
        if missing:
            return await ctx.send(
                "❌ Nel canale mi mancano questi permessi: **" + ", ".join(missing) + "**."
            )

        conf = self.config.guild(ctx.guild)
        old_channel_id = await conf.channel_id()
        if old_channel_id == channel.id:
            webhook = await self._ensure_webhook(channel)
            if webhook is None:
                return await ctx.send("❌ Non sono riuscito a creare/trovare il webhook Alter.")
            await conf.enabled.set(True)
            return await ctx.send(
                f"✅ Alter attivato in {channel.mention}. Webhook riutilizzato: **{webhook.name}** (`{webhook.id}`)."
            )

        if old_channel_id:
            await self._delete_configured_webhook(ctx.guild)

        await conf.channel_id.set(channel.id)
        await conf.webhook_id.set(None)
        self._webhook_cache.pop(ctx.guild.id, None)
        webhook = await self._ensure_webhook(channel)
        if webhook is None:
            return await ctx.send("❌ Non sono riuscito a creare il webhook Alter.")

        await conf.enabled.set(True)
        await ctx.send(
            f"✅ Alter attivato in {channel.mention}. Webhook creato: **{webhook.name}** (`{webhook.id}`)."
        )

    @alter.command(name="mode", aliases=["modalita", "effetto"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_mode(self, ctx: commands.Context, mode: Optional[str] = None) -> None:
        """Mostra o cambia la modalita built-in di Alter."""
        conf = self.config.guild(ctx.guild)
        if mode is None:
            current = str(await conf.mode() or "random")
            if current == "original":
                current = "random"
            return await ctx.send(
                f"Modalita attuale: **{current}**. Usa `.alter modes` per vedere tutte le opzioni."
            )

        selected = await self._resolve_mode(ctx.guild, mode)
        if selected is None or selected.startswith("preset:"):
            return await ctx.send(
                "❌ Modalita built-in sconosciuta. Usa `.alter modes`; per una frase usa `.alter frase usa <nome>`."
            )
        await conf.mode.set(selected)
        await ctx.send(f"✅ Modalita Alter impostata su **{selected}** — {MODE_DESCRIPTIONS[selected]}.")

    @alter.command(name="modes", aliases=["modalita-lista", "effetti", "listamode"])
    async def alter_modes(self, ctx: commands.Context) -> None:
        """Elenca tutte le modalita built-in e le frasi custom."""
        current = str(await self.config.guild(ctx.guild).mode() or "random")
        if current == "original":
            current = "random"

        lines = []
        for mode, description in MODE_DESCRIPTIONS.items():
            marker = "👉" if mode == current else "•"
            lines.append(f"{marker} `{mode}` — {description}")

        chunks: List[List[str]] = []
        chunk: List[str] = []
        size = 0
        for line in lines:
            if size + len(line) + 1 > 3600 and chunk:
                chunks.append(chunk)
                chunk = []
                size = 0
            chunk.append(line)
            size += len(line) + 1
        if chunk:
            chunks.append(chunk)

        for index, part in enumerate(chunks, start=1):
            embed = discord.Embed(
                title="Alter - modalita disponibili" if index == 1 else "Alter - modalita (continua)",
                description="\n".join(part),
                colour=discord.Colour.blurple(),
            )
            embed.set_footer(text="Cambia con: .alter mode <modalita> | Frasi: .alter frase lista")
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

        presets = await self.config.guild(ctx.guild).custom_presets()
        if presets:
            preset_lines = []
            for key, data in presets.items():
                if not isinstance(data, dict):
                    continue
                display = str(data.get("name") or key)
                in_random = bool(data.get("in_random", True))
                marker = "👉" if current == f"preset:{key}" else "•"
                preset_lines.append(
                    f"{marker} `{display}` — random: {'✅' if in_random else '❌'}"
                )
            for start in range(0, len(preset_lines), 20):
                embed = discord.Embed(
                    title="Alter - frasi custom",
                    description="\n".join(preset_lines[start : start + 20]),
                    colour=discord.Colour.blurple(),
                )
                embed.set_footer(text="Usa solo una frase con: .alter frase usa <nome>")
                await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter.command(name="messaggio", aliases=["message", "testo"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_message(self, ctx: commands.Context, *, testo: Optional[str] = None) -> None:
        """Imposta il template usato dalla modalita fixed."""
        conf = self.config.guild(ctx.guild)
        if testo is None:
            current = str(await conf.message_template() or DEFAULT_MESSAGE)
            return await ctx.send(
                "**Messaggio della modalita `fixed`**\n"
                f"```\n{current}\n```\n"
                "Placeholder: `{content}` `{name}` `{username}` `{mention}` `{id}` "
                "`{channel}` `{server}` `{attachments}`.\n"
                "Reset: `.alter messaggio reset`."
            )

        template = str(testo).strip()
        if template.lower() in {"reset", "default", "predefinito"}:
            await conf.message_template.set(DEFAULT_MESSAGE)
            return await ctx.send(f"✅ Messaggio `fixed` ripristinato a: `{DEFAULT_MESSAGE}`")
        if not template:
            return await ctx.send("❌ Il messaggio non puo essere vuoto.")
        if len(template) > 1900:
            return await ctx.send("❌ Il template puo avere massimo 1900 caratteri.")

        unknown = self._unknown_placeholders(template)
        if unknown:
            return await ctx.send(
                "❌ Placeholder sconosciuti: " + ", ".join(f"`{{{name}}}`" for name in unknown)
            )

        await conf.message_template.set(template)
        await ctx.send("✅ Messaggio `fixed` aggiornato. Attivalo con `.alter mode fixed`.")

    @alter.group(name="frase", aliases=["frasi", "preset", "presets"], invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_phrase(self, ctx: commands.Context) -> None:
        """Gestisce frasi custom illimitate."""
        presets = await self.config.guild(ctx.guild).custom_presets()
        await ctx.send(
            f"Frasi custom salvate: **{len(presets)}**. "
            "Comandi: `.alter frase add`, `edit`, `remove`, `mostra`, `lista`, `usa`, `random`, `rinomina`."
        )

    @alter_phrase.command(name="add", aliases=["aggiungi", "crea"])
    async def alter_phrase_add(self, ctx: commands.Context, name: str, *, text: str) -> None:
        key = self._preset_key(name)
        text = text.strip()
        if not key:
            return await ctx.send("❌ Nome frase non valido.")
        if not text:
            return await ctx.send("❌ La frase non puo essere vuota.")
        if len(text) > 1900:
            return await ctx.send("❌ La frase puo avere massimo 1900 caratteri.")
        unknown = self._unknown_placeholders(text)
        if unknown:
            return await ctx.send(
                "❌ Placeholder sconosciuti: " + ", ".join(f"`{{{item}}}`" for item in unknown)
            )

        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        if key in presets:
            return await ctx.send("❌ Esiste gia una frase con quel nome. Usa `.alter frase edit`.")
        presets[key] = {"name": name.strip()[:80], "text": text, "in_random": True}
        await conf.custom_presets.set(presets)
        await ctx.send(
            f"✅ Frase **{name}** creata e **aggiunta al random**. "
            f"Solo questa: `.alter frase usa {key}`."
        )

    @alter_phrase.command(name="edit", aliases=["modifica"])
    async def alter_phrase_edit(self, ctx: commands.Context, name: str, *, text: str) -> None:
        key = self._preset_key(name)
        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        if key not in presets:
            return await ctx.send("❌ Frase non trovata. Usa `.alter frase lista`.")
        text = text.strip()
        if not text or len(text) > 1900:
            return await ctx.send("❌ La frase deve avere tra 1 e 1900 caratteri.")
        unknown = self._unknown_placeholders(text)
        if unknown:
            return await ctx.send(
                "❌ Placeholder sconosciuti: " + ", ".join(f"`{{{item}}}`" for item in unknown)
            )
        data = dict(presets[key]) if isinstance(presets[key], dict) else {}
        data["text"] = text
        data.setdefault("name", name.strip()[:80])
        data.setdefault("in_random", True)
        presets[key] = data
        await conf.custom_presets.set(presets)
        await ctx.send(f"✅ Frase **{data['name']}** aggiornata.")

    @alter_phrase.command(name="remove", aliases=["rimuovi", "delete", "elimina"])
    async def alter_phrase_remove(self, ctx: commands.Context, name: str) -> None:
        key = self._preset_key(name)
        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        if key not in presets:
            return await ctx.send("❌ Frase non trovata.")
        display = str(presets[key].get("name") or key) if isinstance(presets[key], dict) else key
        presets.pop(key, None)
        await conf.custom_presets.set(presets)
        if str(await conf.mode() or "") == f"preset:{key}":
            await conf.mode.set("random")
        await ctx.send(f"✅ Frase **{display}** eliminata.")

    @alter_phrase.command(name="mostra", aliases=["show", "info"])
    async def alter_phrase_show(self, ctx: commands.Context, name: str) -> None:
        key = self._preset_key(name)
        presets = await self.config.guild(ctx.guild).custom_presets()
        data = presets.get(key)
        if not isinstance(data, dict):
            return await ctx.send("❌ Frase non trovata.")
        display = str(data.get("name") or key)
        text = str(data.get("text") or "")
        in_random = bool(data.get("in_random", True))
        await ctx.send(
            f"**{display}** | random: {'✅' if in_random else '❌'}\n```\n{text[:1800]}\n```"
        )

    @alter_phrase.command(name="lista", aliases=["list"])
    async def alter_phrase_list(self, ctx: commands.Context) -> None:
        presets = await self.config.guild(ctx.guild).custom_presets()
        if not presets:
            return await ctx.send("Nessuna frase custom configurata.")
        current = str(await self.config.guild(ctx.guild).mode() or "random")
        lines = []
        for key, data in presets.items():
            if not isinstance(data, dict):
                continue
            display = str(data.get("name") or key)
            preview = str(data.get("text") or "").replace("\n", " ")[:70]
            marker = "👉" if current == f"preset:{key}" else "•"
            lines.append(
                f"{marker} **{display}** (`{key}`) · random {'✅' if data.get('in_random', True) else '❌'}\n↳ {preview}"
            )
        for start in range(0, len(lines), 12):
            embed = discord.Embed(
                title=f"Alter - frasi custom ({len(lines)})",
                description="\n".join(lines[start : start + 12]),
                colour=discord.Colour.blurple(),
            )
            await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter_phrase.command(name="usa", aliases=["use", "solo"])
    async def alter_phrase_use(self, ctx: commands.Context, name: str) -> None:
        key = self._preset_key(name)
        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        data = presets.get(key)
        if not isinstance(data, dict):
            return await ctx.send("❌ Frase non trovata.")
        await conf.mode.set(f"preset:{key}")
        await ctx.send(f"✅ Alter usera **solo la frase {data.get('name') or key}**.")

    @alter_phrase.command(name="random")
    async def alter_phrase_random(self, ctx: commands.Context, name: str, state: str) -> None:
        key = self._preset_key(name)
        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        data = presets.get(key)
        if not isinstance(data, dict):
            return await ctx.send("❌ Frase non trovata.")
        state_value = state.strip().lower()
        if state_value in {"on", "si", "yes", "true", "1", "attiva"}:
            enabled = True
        elif state_value in {"off", "no", "false", "0", "disattiva"}:
            enabled = False
        else:
            return await ctx.send("Usa `.alter frase random <nome> on` oppure `off`.")
        data = dict(data)
        data["in_random"] = enabled
        presets[key] = data
        await conf.custom_presets.set(presets)
        await ctx.send(
            f"✅ Frase **{data.get('name') or key}** {'aggiunta al' if enabled else 'rimossa dal'} random."
        )

    @alter_phrase.command(name="rinomina", aliases=["rename"])
    async def alter_phrase_rename(self, ctx: commands.Context, old_name: str, new_name: str) -> None:
        old_key = self._preset_key(old_name)
        new_key = self._preset_key(new_name)
        if not new_key:
            return await ctx.send("❌ Nuovo nome non valido.")
        conf = self.config.guild(ctx.guild)
        presets = await conf.custom_presets()
        data = presets.get(old_key)
        if not isinstance(data, dict):
            return await ctx.send("❌ Frase originale non trovata.")
        if new_key != old_key and new_key in presets:
            return await ctx.send("❌ Esiste gia una frase con il nuovo nome.")
        data = dict(data)
        data["name"] = new_name.strip()[:80]
        if new_key != old_key:
            presets.pop(old_key, None)
        presets[new_key] = data
        await conf.custom_presets.set(presets)
        if str(await conf.mode() or "") == f"preset:{old_key}":
            await conf.mode.set(f"preset:{new_key}")
        await ctx.send(f"✅ Frase rinominata in **{data['name']}** (`{new_key}`).")

    @alter.command(name="test", aliases=["prova", "preview"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_test(self, ctx: commands.Context, *, mode: Optional[str] = None) -> None:
        """Invia un test con la modalita attiva o una modalita/frase specifica."""
        conf = self.config.guild(ctx.guild)
        channel = ctx.guild.get_channel((await conf.channel_id()) or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.alter setup #canale`.")

        webhook = await self._ensure_webhook(channel)
        if webhook is None:
            return await ctx.send("❌ Webhook Alter non disponibile.")

        requested = None
        if mode:
            raw = mode.strip()
            resolved = await self._resolve_mode(ctx.guild, raw)
            if resolved is None:
                key = self._preset_key(raw)
                presets = await conf.custom_presets()
                if key in presets:
                    resolved = f"preset:{key}"
            if resolved is None:
                return await ctx.send("❌ Modalita/frase non valida. Usa `.alter modes` o `.alter frase lista`.")
            requested = resolved

        content, used_mode = await self._render_choice(ctx.message, requested)
        try:
            await self._send_as_author(webhook, ctx.message, content[:2000] or "[alterato]")
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            return await ctx.send(f"❌ Invio test fallito: `{type(exc).__name__}`")
        await ctx.send(f"✅ Test **{used_mode}** inviato in {channel.mention}.")

    @alter.command(name="on", aliases=["enable", "attiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_on(self, ctx: commands.Context) -> None:
        conf = self.config.guild(ctx.guild)
        channel = ctx.guild.get_channel((await conf.channel_id()) or 0)
        if not isinstance(channel, discord.TextChannel):
            return await ctx.send("❌ Prima usa `.alter setup #canale`.")
        if await self._ensure_webhook(channel) is None:
            return await ctx.send("❌ Non riesco a creare/trovare il webhook Alter.")
        await conf.enabled.set(True)
        await ctx.send("✅ Alter attivato.")

    @alter.command(name="off", aliases=["disable", "disattiva"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_off(self, ctx: commands.Context) -> None:
        await self.config.guild(ctx.guild).enabled.set(False)
        await ctx.send("⏸️ Alter disattivato. Il webhook resta salvato per la prossima attivazione.")

    @alter.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_status(self, ctx: commands.Context) -> None:
        settings = await self.config.guild(ctx.guild).all()
        channel = ctx.guild.get_channel(settings.get("channel_id") or 0)
        webhook = None
        if isinstance(channel, discord.TextChannel):
            webhook = await self._find_webhook(channel, settings.get("webhook_id"))

        mode = str(settings.get("mode") or "random")
        if mode == "original":
            mode = "random"
        template = str(settings.get("message_template") or DEFAULT_MESSAGE)
        presets = settings.get("custom_presets") if isinstance(settings.get("custom_presets"), dict) else {}
        random_presets = sum(
            1 for data in presets.values() if isinstance(data, dict) and data.get("in_random", True)
        )

        if mode.startswith("preset:"):
            key = mode.split(":", 1)[1]
            data = presets.get(key)
            mode_description = (
                f"frase custom: {data.get('name') or key}" if isinstance(data, dict) else "frase custom mancante"
            )
        else:
            mode_description = MODE_DESCRIPTIONS.get(mode, "-")

        embed = discord.Embed(title="Alter", colour=discord.Colour.blurple())
        embed.add_field(
            name="Stato",
            value="✅ Attivo" if settings.get("enabled") else "⏸️ Disattivato",
            inline=True,
        )
        embed.add_field(
            name="Canale",
            value=channel.mention if isinstance(channel, discord.TextChannel) else "Non configurato",
            inline=True,
        )
        embed.add_field(
            name="Webhook",
            value=f"✅ `{webhook.id}`" if webhook else "❌ assente / da ricreare",
            inline=True,
        )
        embed.add_field(name="Modalita", value=f"**{mode}**", inline=True)
        embed.add_field(name="Descrizione", value=mode_description, inline=False)
        embed.add_field(
            name="Frasi custom",
            value=f"**{len(presets)}** totali · **{random_presets}** incluse nel random",
            inline=False,
        )
        if mode == "fixed":
            embed.add_field(name="Messaggio fixed", value=f"```\n{template[:950]}\n```", inline=False)
        embed.set_footer(text=f"Alter v{self.__version__}")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @alter.command(name="remove", aliases=["rimuovi", "reset"])
    @commands.admin_or_permissions(manage_guild=True)
    async def alter_remove(self, ctx: commands.Context) -> None:
        """Disattiva Alter, elimina il webhook e resetta la configurazione."""
        conf = self.config.guild(ctx.guild)
        await conf.enabled.set(False)
        await self._delete_configured_webhook(ctx.guild)
        await conf.channel_id.set(None)
        await conf.webhook_id.set(None)
        await conf.message_template.set(DEFAULT_MESSAGE)
        await conf.mode.set("random")
        await conf.custom_presets.set({})
        self._webhook_cache.pop(ctx.guild.id, None)
        await ctx.send("✅ Alter rimosso: webhook eliminato e configurazione resettata.")

    @alter.command(name="version", aliases=["versione"])
    async def alter_version(self, ctx: commands.Context) -> None:
        await ctx.send(f"Alter **v{self.__version__}**.")
