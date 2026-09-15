import re
from typing import Optional, Sequence, Tuple

from .v41 import SwearJar as BaseSwearJar


EXTRA_PROFANITIES = (
    "porcaccio",
    "porcaccia",
    "porcacci",
    "porcacce",
    "porcone",
    "porcona",
    "porconi",
    "porcazzo",
    "porcazza",
    "porcazzi",
    "porcazze",
    "cagnaccio",
    "cagnaccia",
    "cagnacci",
    "cagnacce",
    "cagnone",
    "cagnona",
    "mannaggia",
    "maledizione",
    "merdoso",
    "merdosa",
    "zozzo",
    "zozza",
)


class SwearJar(BaseSwearJar):
    """SwearJar 4.2: piu varianti italiane e bestemmie scritte tutte attaccate."""

    __version__ = "4.2.0"

    @classmethod
    def _expanded_profanities(cls, values: Sequence[str]):
        result = []
        seen = set()
        for value in (*values, *EXTRA_PROFANITIES):
            key = cls._normalize(value)
            if not key or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @classmethod
    def _find_compact_violation(
        cls,
        content: str,
        deities: Sequence[str],
        profanities: Sequence[str],
    ) -> Optional[Tuple[str, str]]:
        """Rileva forme compatte come porcodio, porcoddio, diocane, porcamadonna.

        Il token deve essere composto interamente da una divinita e da una
        parolaccia (in uno dei due ordini), quindi non cerchiamo semplici
        sottostringhe dentro parole normali.
        """
        safe_deities = cls._safe_deities(deities)
        expanded_profanities = cls._expanded_profanities(profanities)

        deity_keys = []
        for deity in safe_deities:
            key = cls._collapse_runs(cls._normalize(deity).replace(" ", ""))
            if key:
                deity_keys.append((deity, key))

        profanity_keys = []
        for profanity in expanded_profanities:
            key = cls._collapse_runs(cls._normalize(profanity).replace(" ", ""))
            if key:
                profanity_keys.append((profanity, key))

        for normalized in cls._normalization_variants(content):
            for match in re.finditer(r"[a-z]+", normalized):
                token = cls._collapse_runs(match.group(0))
                if len(token) < 5:
                    continue
                for deity, deity_key in deity_keys:
                    for profanity, profanity_key in profanity_keys:
                        if token == deity_key + profanity_key or token == profanity_key + deity_key:
                            return deity, profanity
        return None

    @classmethod
    def _find_composed_violation(
        cls,
        content: str,
        deities: Sequence[str],
        profanities: Sequence[str],
    ):
        expanded = cls._expanded_profanities(profanities)

        # Prima usa il rilevatore normale: spazi, punteggiatura, lettere
        # ripetute, leet e un massimo di una parola in mezzo.
        pair = super()._find_composed_violation(content, deities, expanded)
        if pair is not None:
            return pair

        # Poi prova le forme totalmente attaccate.
        return cls._find_compact_violation(content, deities, expanded)
