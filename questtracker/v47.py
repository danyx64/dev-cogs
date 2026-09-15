from typing import Dict, List

import discord
from redbot.core import commands

from .v46 import QuestTracker as QuestTrackerV46


# Sostituiamo `.quest controlla` per mostrare anche gli errori di invio.
for _command_name in ("controlla", "check"):
    QuestTrackerV46.quest.remove_command(_command_name)


class QuestTracker(QuestTrackerV46):
    """QuestTracker 4.7.0: non marca come viste le Quest che non riesce a inviare."""

    __version__ = "4.7.0"

    def __init__(self, bot):
        super().__init__(bot)
        self._last_scan_errors: Dict[int, List[str]] = {}

    async def _scan_guild(self, guild: discord.Guild, *, force: bool = False) -> int:
        """Controlla le Quest e marca come viste solo quelle inviate con successo.

        Nelle versioni precedenti, alla fine della scansione venivano unite alle
        `seen_keys` tutte le Quest attive, comprese quelle per cui Discord aveva
        rifiutato l'invio. Una singola Forbidden/HTTPException poteva quindi far
        sparire definitivamente una Quest senza che fosse mai stata notificata.
        """
        self._last_scan_errors[guild.id] = []
        settings = await self.config.guild(guild).all()

        if not settings.get("enabled") and not force:
            return 0

        channel = guild.get_channel(settings.get("channel_id") or 0)
        if not isinstance(channel, discord.TextChannel):
            self._last_scan_errors[guild.id].append(
                "Canale Quest non configurato o non piu disponibile."
            )
            return 0

        active = self._active_quests(await self._fetch_quests())

        # Manteniamo il comportamento storico al primissimo avvio: il setup
        # registra lo stato corrente senza inondare il canale di Quest vecchie.
        if not settings.get("initialized"):
            await self.config.guild(guild).seen_keys.set(
                [canonical for canonical, _, _ in active]
            )
            await self.config.guild(guild).initialized.set(True)
            return 0

        seen_order = [str(value) for value in (settings.get("seen_keys") or [])]
        seen = set(seen_order)
        sent = 0

        for canonical, entry, config in reversed(active):
            if canonical in seen:
                continue

            quest_id = str(
                entry.get("id")
                or config.get("id")
                or config.get("quest_id")
                or "?"
            ).strip()

            # Se non possiamo costruire il link Discord, non consideriamo la
            # Quest gestita: resta nuova e verra ritentata alla scansione seguente.
            if self._quest_share_url(entry, config) is None:
                self._last_scan_errors[guild.id].append(
                    f"Quest `{quest_id}`: ID/link non valido."
                )
                continue

            try:
                await self._send_quest(channel, guild, entry, config, test=False)
            except discord.Forbidden:
                self._last_scan_errors[guild.id].append(
                    f"Quest `{quest_id}`: permessi insufficienti nel canale {channel.mention}."
                )
                continue
            except discord.HTTPException as exc:
                status = getattr(exc, "status", "?")
                code = getattr(exc, "code", "?")
                self._last_scan_errors[guild.id].append(
                    f"Quest `{quest_id}`: errore Discord HTTP {status} / code {code}."
                )
                continue

            # Solo dopo un invio completato la Quest diventa vista.
            seen.add(canonical)
            seen_order.append(canonical)
            sent += 1

        # Non aggiungiamo piu automaticamente tutte le Quest attive: quelle che
        # falliscono devono rimanere nuove, cosi il loop le ritenta ogni 5 minuti.
        await self.config.guild(guild).seen_keys.set(seen_order[-500:])
        return sent

    @QuestTrackerV46.quest.command(name="controlla", aliases=["check"])
    @commands.admin_or_permissions(manage_guild=True)
    async def quest_check(self, ctx: commands.Context):
        """Forza una scansione e mostra eventuali errori di invio."""
        async with ctx.typing():
            sent = await self._scan_guild(ctx.guild, force=True)

        errors = self._last_scan_errors.get(ctx.guild.id, [])
        message = f"✅ Controllo completato. Nuove Quest pubblicate: **{sent}**."

        if errors:
            preview = "\n".join(f"• {error}" for error in errors[:8])
            if len(errors) > 8:
                preview += f"\n• …e altri {len(errors) - 8} errori."
            message += (
                "\n⚠️ Alcune Quest non sono state marcate come viste e verranno "
                "ritentate automaticamente:\n" + preview
            )

        await ctx.send(message[:2000], allowed_mentions=discord.AllowedMentions.none())
