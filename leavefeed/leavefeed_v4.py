import uuid
from typing import Dict, List, Optional

import discord
from redbot.core import commands

from .leavefeed_v3 import LeaveFeed as BaseLeaveFeed, ITALY_TZ


class ContinueView(discord.ui.View):
    def __init__(self, cog: "LeaveFeed", session_id: str):
        super().__init__(timeout=600)
        self.cog = cog
        self.session_id = session_id

    @discord.ui.button(label="Continua", style=discord.ButtonStyle.primary)
    async def continue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.show_current_step(interaction, self.session_id)


class PreviewStartView(discord.ui.View):
    def __init__(self, cog: "LeaveFeed", guild_id: int, modal_key: str, owner_id: int):
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.modal_key = modal_key
        self.owner_id = owner_id

    @discord.ui.button(label="Apri anteprima", style=discord.ButtonStyle.success)
    async def preview_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            return await interaction.response.send_message("Questa anteprima non è per te.", ephemeral=True)
        await self.cog.start_questionnaire(interaction, self.guild_id, self.modal_key, preview=True)


class ChoiceSelect(discord.ui.Select):
    def __init__(self, cog: "LeaveFeed", session_id: str, element: Dict):
        self.cog = cog
        self.session_id = session_id
        options = []
        for option in list(element.get("options", []))[:25]:
            label = str(option.get("label") or option.get("value") or "Opzione")[:100]
            value = str(option.get("value") or label)[:100]
            description = str(option.get("description") or "")[:100] or None
            options.append(discord.SelectOption(label=label, value=value, description=description))
        multiple = element.get("kind") == "multi"
        min_values = 1 if element.get("required", True) else 0
        max_values = min(len(options), int(element.get("max_values", len(options) if multiple else 1) or 1))
        super().__init__(
            placeholder=str(element.get("placeholder") or "Seleziona...")[:150],
            min_values=min_values,
            max_values=max(1, max_values) if options else 1,
            options=options or [discord.SelectOption(label="Nessuna opzione configurata", value="none")],
            disabled=not bool(options),
        )

    async def callback(self, interaction: discord.Interaction):
        session = self.cog._sessions.get(self.session_id)
        if not session or interaction.user.id != session["user_id"]:
            return await interaction.response.send_message("Sessione non valida o scaduta.", ephemeral=True)
        session["answers"][session["index"]] = list(self.values)
        session["index"] += 1
        await interaction.response.send_message(
            "Risposta salvata.", view=ContinueView(self.cog, self.session_id), ephemeral=True
        )


class ChoiceView(discord.ui.View):
    def __init__(self, cog: "LeaveFeed", session_id: str, element: Dict):
        super().__init__(timeout=600)
        self.add_item(ChoiceSelect(cog, session_id, element))


class TextPageModal(discord.ui.Modal):
    def __init__(self, cog: "LeaveFeed", session_id: str, title: str, entries: List[tuple]):
        super().__init__(title=title[:45] or "Feedback", timeout=600)
        self.cog = cog
        self.session_id = session_id
        self.entries = entries
        self.inputs = []
        for index, element in entries:
            style = discord.TextStyle.short if element.get("kind") == "short" else discord.TextStyle.paragraph
            min_length = int(element.get("min_length", 0) or 0)
            max_length = max(1, min(int(element.get("max_length", 1000) or 1000), 4000))
            item = discord.ui.TextInput(
                label=str(element.get("label") or f"Domanda {index + 1}")[:45],
                placeholder=(str(element.get("placeholder"))[:100] if element.get("placeholder") else None),
                style=style,
                required=bool(element.get("required", True)),
                min_length=(min_length if min_length > 0 else None),
                max_length=max_length,
            )
            self.inputs.append(item)
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        session = self.cog._sessions.get(self.session_id)
        if not session or interaction.user.id != session["user_id"]:
            return await interaction.response.send_message("Sessione non valida o scaduta.", ephemeral=True)
        for (index, _), item in zip(self.entries, self.inputs):
            session["answers"][index] = str(item.value or "").strip()
        session["index"] = self.entries[-1][0] + 1
        await interaction.response.send_message(
            "Pagina completata.", view=ContinueView(self.cog, self.session_id), ephemeral=True
        )


class LeaveFeed(BaseLeaveFeed):
    """LeaveFeed v2.2: questionari multi-step, paragrafi, opzioni e preview reale."""

    __version__ = "2.2.0"
    MAX_ELEMENTS = 50

    def __init__(self, bot):
        super().__init__(bot)
        self._sessions: Dict[str, Dict] = {}

    async def _ensure_schema(self, guild: discord.Guild) -> Dict:
        data = await super()._ensure_schema(guild)
        changed = False
        modals = data.get("modals") or {}
        for modal in modals.values():
            if "description" not in modal:
                modal["description"] = ""
                changed = True
            if "elements" not in modal:
                elements = []
                for question in modal.get("questions", []):
                    elements.append({
                        "kind": "short" if question.get("style") == "short" else "long",
                        "label": question.get("label") or "Domanda",
                        "placeholder": question.get("placeholder") or "",
                        "required": bool(question.get("required", True)),
                        "min_length": int(question.get("min_length", 0) or 0),
                        "max_length": int(question.get("max_length", 1000) or 1000),
                    })
                modal["elements"] = elements
                changed = True
        if changed:
            await self.config.guild(guild).modals.set(modals)
            data["modals"] = modals
        return data

    async def open_modal(self, interaction: discord.Interaction, guild_id: int, button_key: str):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await interaction.response.send_message("Questo server non è più disponibile.", ephemeral=True)
        data = await self._ensure_schema(guild)
        button = data["buttons"].get(button_key)
        if not button:
            return await interaction.response.send_message("Questo pulsante non è più configurato.", ephemeral=True)
        modal_key = button.get("modal")
        await self.start_questionnaire(interaction, guild_id, modal_key, preview=False)

    async def start_questionnaire(self, interaction: discord.Interaction, guild_id: int, modal_key: str, preview: bool = False):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return await interaction.response.send_message("Server non disponibile.", ephemeral=True)
        data = await self._ensure_schema(guild)
        modal = data["modals"].get(modal_key)
        if not modal:
            return await interaction.response.send_message("Modal non trovato.", ephemeral=True)
        elements = modal.get("elements") or []
        if not elements:
            return await interaction.response.send_message("Questo modal non contiene ancora elementi.", ephemeral=True)
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = {
            "guild_id": guild_id,
            "modal_key": modal_key,
            "user_id": interaction.user.id,
            "index": 0,
            "answers": {},
            "preview": preview,
        }
        description = str(modal.get("description") or "").strip()
        if description:
            return await interaction.response.send_message(
                description[:1900], view=ContinueView(self, session_id), ephemeral=True
            )
        await self.show_current_step(interaction, session_id)

    async def show_current_step(self, interaction: discord.Interaction, session_id: str):
        session = self._sessions.get(session_id)
        if not session or interaction.user.id != session["user_id"]:
            return await interaction.response.send_message("Sessione non valida o scaduta.", ephemeral=True)
        guild = self.bot.get_guild(session["guild_id"])
        if guild is None:
            return await interaction.response.send_message("Server non disponibile.", ephemeral=True)
        data = await self._ensure_schema(guild)
        modal = data["modals"].get(session["modal_key"])
        if not modal:
            return await interaction.response.send_message("Modal non trovato.", ephemeral=True)
        elements = modal.get("elements") or []
        index = session["index"]
        if index >= len(elements):
            return await self._finish_session(interaction, session_id, modal)

        element = elements[index]
        kind = element.get("kind", "long")
        if kind == "paragraph":
            session["index"] += 1
            return await interaction.response.send_message(
                str(element.get("text") or "")[:1900],
                view=ContinueView(self, session_id),
                ephemeral=True,
            )
        if kind in {"single", "multi"}:
            return await interaction.response.send_message(
                f"**{str(element.get('label') or 'Scegli un’opzione')}**",
                view=ChoiceView(self, session_id, element),
                ephemeral=True,
            )

        entries = []
        cursor = index
        while cursor < len(elements) and len(entries) < 5:
            current = elements[cursor]
            if current.get("kind", "long") not in {"short", "long"}:
                break
            entries.append((cursor, current))
            cursor += 1
        title = str(modal.get("title") or "Feedback")
        await interaction.response.send_modal(TextPageModal(self, session_id, title, entries))

    async def _finish_session(self, interaction: discord.Interaction, session_id: str, modal: Dict):
        session = self._sessions.pop(session_id, None)
        if not session:
            return await interaction.response.send_message("Sessione scaduta.", ephemeral=True)
        if session.get("preview"):
            return await interaction.response.send_message("Anteprima completata. Nessun feedback è stato inviato allo staff.", ephemeral=True)

        guild = self.bot.get_guild(session["guild_id"])
        if guild is None:
            return await interaction.response.send_message("Server non disponibile.", ephemeral=True)
        data = await self._ensure_schema(guild)
        channel_id = data.get("feedback_channel_id")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message("Canale feedback non configurato.", ephemeral=True)

        now = discord.utils.utcnow().astimezone(ITALY_TZ)
        lines = [
            f"**Utente:** <@{interaction.user.id}>",
            f"**Data:** {now.strftime('%d/%m/%Y')}",
            f"**Ora:** {now.strftime('%H:%M:%S')}",
            f"**ID:** `{interaction.user.id}`",
            "**Motivo:**",
        ]
        for idx, element in enumerate(modal.get("elements") or []):
            if element.get("kind") == "paragraph":
                continue
            answer = session["answers"].get(idx, "—")
            if isinstance(answer, list):
                answer = ", ".join(answer) if answer else "—"
            label = str(element.get("label") or f"Domanda {idx + 1}")
            lines.append(f"**{self._clean(label, 80)}:** {self._clean(answer)}")
        try:
            await channel.send(
                embed=discord.Embed(description="\n".join(lines)[:4096], colour=discord.Colour.blurple()),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.response.send_message(
                str(modal.get("submit_message") or "Grazie per il feedback.")[:1900], ephemeral=True
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message("Non sono riuscito a consegnare il feedback allo staff.", ephemeral=True)

    @BaseLeaveFeed.modal_group.command(name="description")
    @commands.admin_or_permissions(manage_guild=True)
    async def modal_description(self, ctx: commands.Context, key: str, *, text: str):
        """Imposta il paragrafo introduttivo mostrato prima del questionario."""
        if len(text) > 1900:
            return await ctx.send("La descrizione può avere massimo 1900 caratteri.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            modal["description"] = "" if text == "-" else text
        await ctx.send("Descrizione del modal aggiornata.")

    @BaseLeaveFeed.modal_group.command(name="preview")
    @commands.admin_or_permissions(manage_guild=True)
    async def modal_preview(self, ctx: commands.Context, key: str):
        """Mostra la configurazione del modal e un pulsante per provarne il flusso reale."""
        data = await self._ensure_schema(ctx.guild)
        modal = data["modals"].get(key.lower())
        if not modal:
            return await ctx.send("Modal non trovato.")
        elements = modal.get("elements") or []
        lines = [
            f"**Modal:** `{key.lower()}`",
            f"**Titolo:** {self._clean(modal.get('title') or 'Feedback', 100)}",
            f"**Descrizione:** {self._clean(modal.get('description') or '—', 500)}",
            f"**Messaggio finale:** {self._clean(modal.get('submit_message') or 'Grazie per il feedback.', 300)}",
            f"**Elementi:** {len(elements)}",
            "",
        ]
        for i, element in enumerate(elements, 1):
            kind = element.get("kind", "long")
            if kind == "paragraph":
                lines.append(f"**{i}. Paragrafo:** {self._clean(element.get('text') or '—', 180)}")
            elif kind in {"single", "multi"}:
                opts = ", ".join(str(o.get("label") or o.get("value")) for o in element.get("options", [])) or "—"
                lines.append(f"**{i}. {kind}:** {self._clean(element.get('label') or 'Domanda', 80)} | Opzioni: {self._clean(opts, 220)}")
            else:
                lines.append(f"**{i}. {kind}:** {self._clean(element.get('label') or 'Domanda', 100)}")
        embed = discord.Embed(description="\n".join(lines)[:4096], colour=discord.Colour.blurple())
        await ctx.send(embed=embed, view=PreviewStartView(self, ctx.guild.id, key.lower(), ctx.author.id))

    @BaseLeaveFeed.modal_group.command(name="usage")
    @commands.admin_or_permissions(manage_guild=True)
    async def modal_usage(self, ctx: commands.Context):
        """Spiega i comandi disponibili per costruire e provare un questionario."""
        await ctx.send(
            "**Comandi modal/questionario**\n"
            "`.leavefeed modal preview CHIAVE` — anteprima reale\n"
            "`.leavefeed modal description CHIAVE TESTO` — paragrafo iniziale\n"
            "`.leavefeed question addmore CHIAVE short|long true|false MIN MAX DOMANDA` — aggiunge testo senza limite di 5 totale\n"
            "`.leavefeed question addchoice CHIAVE single|multi true|false DOMANDA` — aggiunge scelta\n"
            "`.leavefeed question optionadd CHIAVE INDICE valore | etichetta | descrizione` — aggiunge opzione\n"
            "`.leavefeed question optionremove CHIAVE INDICE NUM_OPZIONE` — rimuove opzione\n"
            "`.leavefeed paragraph add CHIAVE TESTO` — aggiunge un paragrafo nel flusso\n"
            "`.leavefeed paragraph edit CHIAVE INDICE TESTO` — modifica il paragrafo\n"
            "`.leavefeed element remove CHIAVE INDICE` — elimina qualsiasi elemento\n"
            "`.leavefeed element move CHIAVE INDICE NUOVO_INDICE` — riordina gli elementi"
        )

    @BaseLeaveFeed.question_group.command(name="addmore")
    @commands.admin_or_permissions(manage_guild=True)
    async def question_addmore(self, ctx: commands.Context, modal_key: str, style: str, required: bool, min_length: int, max_length: int, *, label: str):
        """Aggiunge una domanda testuale extra; il questionario verrà diviso automaticamente in più pagine."""
        style = style.lower()
        if style not in {"short", "long"}:
            return await ctx.send("Lo stile deve essere `short` oppure `long`.")
        if not 1 <= len(label) <= 45:
            return await ctx.send("La domanda deve essere lunga da 1 a 45 caratteri.")
        if min_length < 0 or max_length < 1 or max_length > 4000 or min_length > max_length:
            return await ctx.send("Lunghezze non valide.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if len(elements) >= self.MAX_ELEMENTS:
                return await ctx.send(f"Massimo {self.MAX_ELEMENTS} elementi per modal.")
            elements.append({"kind": style, "label": label, "placeholder": "", "required": required, "min_length": min_length, "max_length": max_length})
        await ctx.send("Domanda aggiunta al questionario.")

    @BaseLeaveFeed.question_group.command(name="addchoice")
    @commands.admin_or_permissions(manage_guild=True)
    async def question_addchoice(self, ctx: commands.Context, modal_key: str, kind: str, required: bool, *, label: str):
        """Aggiunge una domanda a scelta singola o multipla."""
        kind = kind.lower()
        if kind not in {"single", "multi"}:
            return await ctx.send("Usa `single` oppure `multi`.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if len(elements) >= self.MAX_ELEMENTS:
                return await ctx.send(f"Massimo {self.MAX_ELEMENTS} elementi per modal.")
            elements.append({"kind": kind, "label": label[:100], "placeholder": "Seleziona...", "required": required, "options": [], "max_values": 25 if kind == "multi" else 1})
        await ctx.send("Domanda a scelta aggiunta. Ora aggiungi le opzioni con `question optionadd`.")

    @BaseLeaveFeed.question_group.command(name="optionadd")
    @commands.admin_or_permissions(manage_guild=True)
    async def question_optionadd(self, ctx: commands.Context, modal_key: str, index: int, *, option: str):
        """Aggiunge un'opzione a una domanda choice. Formato: valore | etichetta | descrizione."""
        parts = [p.strip() for p in option.split("|", 2)]
        value = parts[0]
        label = parts[1] if len(parts) > 1 and parts[1] else value
        description = parts[2] if len(parts) > 2 else ""
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if index < 1 or index > len(elements):
                return await ctx.send("Indice non valido.")
            element = elements[index - 1]
            if element.get("kind") not in {"single", "multi"}:
                return await ctx.send("Quell'elemento non è una domanda a scelta.")
            options = element.setdefault("options", [])
            if len(options) >= 25:
                return await ctx.send("Discord permette massimo 25 opzioni per selezione.")
            options.append({"value": value[:100], "label": label[:100], "description": description[:100]})
        await ctx.send("Opzione aggiunta.")

    @BaseLeaveFeed.question_group.command(name="optionremove")
    @commands.admin_or_permissions(manage_guild=True)
    async def question_optionremove(self, ctx: commands.Context, modal_key: str, index: int, option_index: int):
        """Rimuove un'opzione da una domanda a scelta."""
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if index < 1 or index > len(elements):
                return await ctx.send("Indice elemento non valido.")
            options = elements[index - 1].setdefault("options", [])
            if option_index < 1 or option_index > len(options):
                return await ctx.send("Indice opzione non valido.")
            options.pop(option_index - 1)
        await ctx.send("Opzione rimossa.")

    @BaseLeaveFeed.leavefeed.group(name="paragraph", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def paragraph_group(self, ctx: commands.Context):
        """Gestisce i paragrafi informativi inseriti tra le domande."""
        await ctx.send_help(ctx.command)

    @paragraph_group.command(name="add")
    async def paragraph_add(self, ctx: commands.Context, modal_key: str, *, text: str):
        """Aggiunge un paragrafo informativo nel punto finale del questionario."""
        if len(text) > 1900:
            return await ctx.send("Massimo 1900 caratteri.")
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if len(elements) >= self.MAX_ELEMENTS:
                return await ctx.send(f"Massimo {self.MAX_ELEMENTS} elementi per modal.")
            elements.append({"kind": "paragraph", "text": text})
        await ctx.send("Paragrafo aggiunto.")

    @paragraph_group.command(name="edit")
    async def paragraph_edit(self, ctx: commands.Context, modal_key: str, index: int, *, text: str):
        """Modifica il testo di un paragrafo già presente."""
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if index < 1 or index > len(elements) or elements[index - 1].get("kind") != "paragraph":
                return await ctx.send("A quell'indice non c'è un paragrafo.")
            elements[index - 1]["text"] = text[:1900]
        await ctx.send("Paragrafo aggiornato.")

    @BaseLeaveFeed.leavefeed.group(name="element", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def element_group(self, ctx: commands.Context):
        """Gestisce ordine e rimozione di tutti gli elementi del questionario."""
        await ctx.send_help(ctx.command)

    @element_group.command(name="list")
    async def element_list(self, ctx: commands.Context, modal_key: str):
        """Elenca in ordine domande, scelte e paragrafi del modal."""
        data = await self._ensure_schema(ctx.guild)
        modal = data["modals"].get(modal_key.lower())
        if not modal:
            return await ctx.send("Modal non trovato.")
        elements = modal.get("elements") or []
        if not elements:
            return await ctx.send("Nessun elemento configurato.")
        lines = []
        for i, element in enumerate(elements, 1):
            kind = element.get("kind", "long")
            text = element.get("text") if kind == "paragraph" else element.get("label")
            lines.append(f"`{i}` **{kind}** — {self._clean(text or '—', 160)}")
        await ctx.send("\n".join(lines)[:1900])

    @element_group.command(name="remove")
    async def element_remove(self, ctx: commands.Context, modal_key: str, index: int):
        """Rimuove qualsiasi elemento del questionario tramite indice."""
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if index < 1 or index > len(elements):
                return await ctx.send("Indice non valido.")
            elements.pop(index - 1)
        await ctx.send("Elemento rimosso.")

    @element_group.command(name="move")
    async def element_move(self, ctx: commands.Context, modal_key: str, index: int, new_index: int):
        """Sposta un elemento in una nuova posizione nel questionario."""
        async with self.config.guild(ctx.guild).modals() as modals:
            modal = modals.get(modal_key.lower())
            if not modal:
                return await ctx.send("Modal non trovato.")
            elements = modal.setdefault("elements", [])
            if index < 1 or index > len(elements) or new_index < 1 or new_index > len(elements):
                return await ctx.send("Indice non valido.")
            item = elements.pop(index - 1)
            elements.insert(new_index - 1, item)
        await ctx.send("Elemento spostato.")
