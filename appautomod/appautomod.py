import io
import json
import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import discord
from redbot.core import Config, commands
from redbot.core.bot import Red


SERVICE_NAME = "appautomod"


class AppAutoMod(commands.Cog):
    """Gestisce il Worker Cloudflare usato dal filtro AutoMod app-driven di Discord."""

    __author__ = "danyx64"
    __version__ = "0.1.0"

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(
            self,
            identifier=781560942317640119,
            force_registration=True,
        )
        self.config.register_guild(
            enabled=False,
            block_words=[],
            blocked_domains=[],
            regex_patterns=[],
            max_mentions=0,
            exempt_role_ids=[],
            exempt_channel_ids=[],
            exempt_user_ids=[],
        )
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8),
            headers={"User-Agent": "Red-AppAutoMod/0.1"},
        )

    def cog_unload(self):
        if not self.session.closed:
            self.bot.loop.create_task(self.session.close())

    async def red_delete_data_for_user(self, **kwargs):
        requester = kwargs.get("requester")
        user_id = kwargs.get("user_id")
        if user_id is None:
            return

        # Gli ID possono essere presenti solo nelle whitelist di eccezione.
        for guild in self.bot.guilds:
            group = self.config.guild(guild)
            ids = await group.exempt_user_ids()
            value = str(user_id)
            if value in ids:
                await group.exempt_user_ids.set([item for item in ids if item != value])
                try:
                    await self._sync_guild(guild)
                except Exception:
                    pass

    async def _credentials(self) -> Tuple[Optional[str], Optional[str]]:
        tokens = await self.bot.get_shared_api_tokens(SERVICE_NAME)
        worker_url = (tokens.get("worker_url") or "").strip().rstrip("/")
        control_token = (tokens.get("control_token") or "").strip()
        return worker_url or None, control_token or None

    @staticmethod
    def _valid_worker_url(value: str) -> bool:
        try:
            parsed = urlparse(value)
        except ValueError:
            return False
        return parsed.scheme == "https" and bool(parsed.netloc)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Tuple[int, Any]:
        worker_url, control_token = await self._credentials()
        if not worker_url or not self._valid_worker_url(worker_url):
            raise RuntimeError(
                "Worker URL non configurato. Usa in DM: "
                "[p]set api appautomod worker_url,<URL> control_token,<TOKEN>"
            )
        if auth and not control_token:
            raise RuntimeError(
                "CONTROL_TOKEN non configurato. Usa in DM: "
                "[p]set api appautomod worker_url,<URL> control_token,<TOKEN>"
            )

        headers = {}
        if auth:
            headers["Authorization"] = f"Bearer {control_token}"

        async with self.session.request(
            method,
            worker_url + path,
            json=json_body,
            headers=headers,
        ) as response:
            text = await response.text()
            if not text:
                data: Any = None
            else:
                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    data = text
            return response.status, data

    async def _guild_payload(self, guild: discord.Guild) -> Dict[str, Any]:
        data = await self.config.guild(guild).all()
        return {
            "enabled": bool(data["enabled"]),
            "block_words": list(data["block_words"]),
            "blocked_domains": list(data["blocked_domains"]),
            "regex_patterns": list(data["regex_patterns"]),
            "max_mentions": int(data["max_mentions"]),
            "exempt_role_ids": [str(item) for item in data["exempt_role_ids"]],
            "exempt_channel_ids": [str(item) for item in data["exempt_channel_ids"]],
            "exempt_user_ids": [str(item) for item in data["exempt_user_ids"]],
        }

    async def _sync_guild(self, guild: discord.Guild) -> Tuple[bool, str]:
        payload = await self._guild_payload(guild)
        status, data = await self._request(
            "PUT",
            f"/admin/guilds/{guild.id}/config",
            json_body=payload,
        )
        if 200 <= status < 300:
            return True, f"Worker sincronizzato (HTTP {status})."
        return False, f"Worker ha risposto HTTP {status}: {self._compact(data)}"

    async def _sync_after_change(self, ctx: commands.Context) -> bool:
        try:
            ok, message = await self._sync_guild(ctx.guild)
        except (aiohttp.ClientError, RuntimeError, TimeoutError) as exc:
            await ctx.send(
                "Configurazione salvata localmente, ma non sono riuscito a sincronizzare "
                f"il Worker: \`{exc}\`"
            )
            return False
        if not ok:
            await ctx.send(f"Configurazione salvata localmente, ma {message}")
            return False
        return True

    @staticmethod
    def _compact(value: Any, limit: int = 800) -> str:
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                text = repr(value)
        return text if len(text) <= limit else text[:limit] + "..."

    @staticmethod
    def _normalize_domain(value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"^https?://", "", value)
        value = re.sub(r"^www\.", "", value)
        return value.split("/", 1)[0].rstrip(".")

    @commands.group(name="appautomod", aliases=["aam"], invoke_without_command=True)
    @commands.guild_only()
    async def appautomod(self, ctx: commands.Context):
        """Configura il filtro AutoMod app-driven collegato al Worker."""
        await ctx.send_help(ctx.command)

    @appautomod.command(name="status")
    @commands.admin_or_permissions(manage_guild=True)
    async def status(self, ctx: commands.Context):
        """Mostra stato locale e raggiungibilita' del Worker."""
        data = await self.config.guild(ctx.guild).all()
        worker_url, control_token = await self._credentials()

        health = "non configurato"
        if worker_url and self._valid_worker_url(worker_url):
            try:
                code, body = await self._request("GET", "/health", auth=False)
                health = f"HTTP {code}"
                if isinstance(body, dict) and body.get("mode"):
                    health += f" / mode={body['mode']}"
            except (aiohttp.ClientError, RuntimeError, TimeoutError) as exc:
                health = f"errore: {exc}"

        embed = discord.Embed(
            title="AppAutoMod",
            colour=discord.Colour.green() if data["enabled"] else discord.Colour.orange(),
        )
        embed.add_field(name="Policy locale", value="Attiva" if data["enabled"] else "Disattiva")
        embed.add_field(name="Worker", value=health)
        embed.add_field(
            name="Credenziali Red",
            value=(
                f"URL: {'si' if worker_url else 'no'}\n"
                f"CONTROL_TOKEN: {'si' if control_token else 'no'}"
            ),
        )
        embed.add_field(name="Parole bloccate", value=str(len(data["block_words"])))
        embed.add_field(name="Domini bloccati", value=str(len(data["blocked_domains"])))
        embed.add_field(name="Regex", value=str(len(data["regex_patterns"])))
        embed.add_field(name="Limite mention", value=str(data["max_mentions"]))
        embed.set_footer(
            text="Il Worker e' in probe mode finche' il contratto sperimentale Discord non viene catturato."
        )
        await ctx.send(embed=embed)

    @appautomod.command(name="sync")
    @commands.admin_or_permissions(manage_guild=True)
    async def sync(self, ctx: commands.Context):
        """Invia la configurazione corrente del server al Worker."""
        async with ctx.typing():
            try:
                ok, message = await self._sync_guild(ctx.guild)
            except (aiohttp.ClientError, RuntimeError, TimeoutError) as exc:
                return await ctx.send(f"Sincronizzazione fallita: \`{exc}\`")
        await ctx.send(("✅ " if ok else "❌ ") + message)

    @appautomod.command(name="enable")
    @commands.admin_or_permissions(manage_guild=True)
    async def enable(self, ctx: commands.Context):
        """Abilita la policy nel Worker per questo server."""
        await self.config.guild(ctx.guild).enabled.set(True)
        if await self._sync_after_change(ctx):
            await ctx.send("✅ AppAutoMod abilitato per questo server.")

    @appautomod.command(name="disable")
    @commands.admin_or_permissions(manage_guild=True)
    async def disable(self, ctx: commands.Context):
        """Disabilita la policy nel Worker per questo server."""
        await self.config.guild(ctx.guild).enabled.set(False)
        if await self._sync_after_change(ctx):
            await ctx.send("✅ AppAutoMod disabilitato per questo server.")

    @appautomod.group(name="word", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def word(self, ctx: commands.Context):
        """Gestisce parole/frasi da bloccare."""
        await ctx.send_help(ctx.command)

    @word.command(name="add")
    async def word_add(self, ctx: commands.Context, *, value: str):
        value = value.strip()
        if not value or len(value) > 200:
            return await ctx.send("La parola/frase deve essere lunga da 1 a 200 caratteri.")
        group = self.config.guild(ctx.guild)
        values = await group.block_words()
        if value in values:
            return await ctx.send("E' gia' presente.")
        values.append(value)
        await group.block_words.set(values[:200])
        if await self._sync_after_change(ctx):
            await ctx.send(f"✅ Aggiunto: \`{value}\`")

    @word.command(name="remove", aliases=["del", "delete"])
    async def word_remove(self, ctx: commands.Context, *, value: str):
        group = self.config.guild(ctx.guild)
        values = await group.block_words()
        try:
            values.remove(value.strip())
        except ValueError:
            return await ctx.send("Valore non trovato.")
        await group.block_words.set(values)
        if await self._sync_after_change(ctx):
            await ctx.send("✅ Rimosso.")

    @word.command(name="list")
    async def word_list(self, ctx: commands.Context):
        values = await self.config.guild(ctx.guild).block_words()
        text = "\n".join(f"• {discord.utils.escape_markdown(v)}" for v in values) or "Nessuna."
        await ctx.send(text[:1900])

    @appautomod.group(name="domain", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def domain(self, ctx: commands.Context):
        """Gestisce domini da bloccare."""
        await ctx.send_help(ctx.command)

    @domain.command(name="add")
    async def domain_add(self, ctx: commands.Context, value: str):
        value = self._normalize_domain(value)
        if not value or "." not in value or len(value) > 253:
            return await ctx.send("Dominio non valido.")
        group = self.config.guild(ctx.guild)
        values = await group.blocked_domains()
        if value in values:
            return await ctx.send("E' gia' presente.")
        values.append(value)
        await group.blocked_domains.set(values[:200])
        if await self._sync_after_change(ctx):
            await ctx.send(f"✅ Dominio aggiunto: \`{value}\`")

    @domain.command(name="remove", aliases=["del", "delete"])
    async def domain_remove(self, ctx: commands.Context, value: str):
        value = self._normalize_domain(value)
        group = self.config.guild(ctx.guild)
        values = await group.blocked_domains()
        try:
            values.remove(value)
        except ValueError:
            return await ctx.send("Dominio non trovato.")
        await group.blocked_domains.set(values)
        if await self._sync_after_change(ctx):
            await ctx.send("✅ Dominio rimosso.")

    @domain.command(name="list")
    async def domain_list(self, ctx: commands.Context):
        values = await self.config.guild(ctx.guild).blocked_domains()
        text = "\n".join(f"• {v}" for v in values) or "Nessuno."
        await ctx.send(text[:1900])

    @appautomod.group(name="regex", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def regex(self, ctx: commands.Context):
        """Gestisce regex JavaScript usate dal Worker."""
        await ctx.send_help(ctx.command)

    @regex.command(name="add")
    async def regex_add(self, ctx: commands.Context, *, pattern: str):
        pattern = pattern.strip()
        if not pattern or len(pattern) > 500:
            return await ctx.send("Regex non valida o troppo lunga (max 500 caratteri).")
        group = self.config.guild(ctx.guild)
        values = await group.regex_patterns()
        if pattern in values:
            return await ctx.send("E' gia' presente.")
        values.append(pattern)
        await group.regex_patterns.set(values[:50])
        if await self._sync_after_change(ctx):
            await ctx.send("✅ Regex aggiunta.")

    @regex.command(name="remove", aliases=["del", "delete"])
    async def regex_remove(self, ctx: commands.Context, *, pattern: str):
        group = self.config.guild(ctx.guild)
        values = await group.regex_patterns()
        try:
            values.remove(pattern.strip())
        except ValueError:
            return await ctx.send("Regex non trovata.")
        await group.regex_patterns.set(values)
        if await self._sync_after_change(ctx):
            await ctx.send("✅ Regex rimossa.")

    @regex.command(name="list")
    async def regex_list(self, ctx: commands.Context):
        values = await self.config.guild(ctx.guild).regex_patterns()
        text = "\n".join(f"{index + 1}. \`{v}\`" for index, v in enumerate(values)) or "Nessuna."
        await ctx.send(text[:1900])

    @appautomod.command(name="mentions")
    @commands.admin_or_permissions(manage_guild=True)
    async def mentions(self, ctx: commands.Context, maximum: int):
        """Imposta il numero massimo di mention utente. 0 = disattivato."""
        if maximum < 0 or maximum > 100:
            return await ctx.send("Valore consentito: 0-100.")
        await self.config.guild(ctx.guild).max_mentions.set(maximum)
        if await self._sync_after_change(ctx):
            await ctx.send(f"✅ Limite mention impostato a {maximum}.")

    @appautomod.group(name="exempt", invoke_without_command=True)
    @commands.admin_or_permissions(manage_guild=True)
    async def exempt(self, ctx: commands.Context):
        """Gestisce eccezioni per canali, ruoli e utenti."""
        await ctx.send_help(ctx.command)

    @exempt.command(name="channel")
    async def exempt_channel(
        self,
        ctx: commands.Context,
        action: str,
        channel: discord.TextChannel,
    ):
        await self._change_exemption(
            ctx, "exempt_channel_ids", action, channel.id, f"#{channel.name}"
        )

    @exempt.command(name="role")
    async def exempt_role(
        self,
        ctx: commands.Context,
        action: str,
        role: discord.Role,
    ):
        await self._change_exemption(
            ctx, "exempt_role_ids", action, role.id, f"@{role.name}"
        )

    @exempt.command(name="user")
    async def exempt_user(
        self,
        ctx: commands.Context,
        action: str,
        member: discord.Member,
    ):
        await self._change_exemption(
            ctx, "exempt_user_ids", action, member.id, str(member)
        )

    async def _change_exemption(
        self,
        ctx: commands.Context,
        key: str,
        action: str,
        object_id: int,
        label: str,
    ):
        action = action.lower().strip()
        if action not in {"add", "remove", "del", "delete"}:
            return await ctx.send("Usa \`add\` oppure \`remove\`.")

        group = self.config.guild(ctx.guild)
        entry = getattr(group, key)
        values = [str(item) for item in await entry()]
        value = str(object_id)

        if action == "add":
            if value in values:
                return await ctx.send("E' gia' tra le eccezioni.")
            values.append(value)
            values = values[:100]
        else:
            if value not in values:
                return await ctx.send("Non e' tra le eccezioni.")
            values.remove(value)

        await entry.set(values)
        if await self._sync_after_change(ctx):
            await ctx.send(f"✅ Eccezione aggiornata: {label}")

    @appautomod.command(name="config")
    @commands.admin_or_permissions(manage_guild=True)
    async def show_config(self, ctx: commands.Context):
        """Mostra la configurazione locale senza segreti."""
        payload = await self._guild_payload(ctx.guild)
        pretty = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(pretty) <= 1900:
            return await ctx.send(f"\`\`\`json\n{pretty}\n\`\`\`")
        fp = io.BytesIO(pretty.encode("utf-8"))
        await ctx.send(file=discord.File(fp, filename=f"appautomod-{ctx.guild.id}.json"))

    @appautomod.command(name="capture")
    @commands.admin_or_permissions(manage_guild=True)
    async def capture(self, ctx: commands.Context):
        """Mostra un riepilogo dell'ultimo payload catturato dal Worker."""
        try:
            code, body = await self._request(
                "GET",
                f"/admin/captures/latest?guild_id={ctx.guild.id}",
            )
        except (aiohttp.ClientError, RuntimeError, TimeoutError) as exc:
            return await ctx.send(f"Richiesta fallita: \`{exc}\`")

        if code == 404:
            return await ctx.send("Nessun payload catturato per questo server.")
        if code < 200 or code >= 300 or not isinstance(body, dict):
            return await ctx.send(f"Worker HTTP {code}: {self._compact(body)}")

        capture = body.get("capture") or {}
        payload = capture.get("payload") or {}
        embed = discord.Embed(
            title="Ultima cattura AppAutoMod",
            colour=discord.Colour.blurple(),
        )
        embed.add_field(name="Ricevuta", value=str(capture.get("received_at") or "?"), inline=False)
        embed.add_field(name="Guild", value=str(capture.get("guild_id") or "?"))
        embed.add_field(name="Canale", value=str(capture.get("channel_id") or "?"))
        embed.add_field(name="Utente", value=str(capture.get("user_id") or "?"))
        embed.add_field(
            name="Chiavi top-level",
            value=", ".join(capture.get("top_level_keys") or [])[:1000] or "?",
            inline=False,
        )
        embed.add_field(
            name="Valutazione Worker",
            value=self._compact(capture.get("evaluation"), 1000),
            inline=False,
        )
        embed.add_field(
            name="Tipo / evento rilevato",
            value=self._compact(
                {
                    "type": payload.get("type") if isinstance(payload, dict) else None,
                    "event": payload.get("event") if isinstance(payload, dict) else None,
                    "t": payload.get("t") if isinstance(payload, dict) else None,
                },
                1000,
            ),
            inline=False,
        )
        await ctx.send(embed=embed)

    @appautomod.command(name="captureraw")
    @commands.is_owner()
    async def capture_raw(self, ctx: commands.Context):
        """Invia al proprietario del bot il JSON completo dell'ultima cattura."""
        try:
            code, body = await self._request(
                "GET",
                f"/admin/captures/latest?guild_id={ctx.guild.id}",
            )
        except (aiohttp.ClientError, RuntimeError, TimeoutError) as exc:
            return await ctx.send(f"Richiesta fallita: \`{exc}\`")

        if code < 200 or code >= 300:
            return await ctx.send(f"Worker HTTP {code}: {self._compact(body)}")

        pretty = json.dumps(body, ensure_ascii=False, indent=2)
        fp = io.BytesIO(pretty.encode("utf-8"))
        try:
            await ctx.author.send(
                "Cattura AppAutoMod. Trattala come dato diagnostico sensibile.",
                file=discord.File(fp, filename=f"appautomod-capture-{ctx.guild.id}.json"),
            )
        except discord.Forbidden:
            return await ctx.send("Non riesco a inviarti DM. Abilita temporaneamente i DM e riprova.")
        await ctx.send("✅ Cattura inviata in DM.")

    @commands.Cog.listener()
    async def on_red_api_tokens_update(self, service_name: str, api_tokens):
        if service_name != SERVICE_NAME:
            return
        # Nessuna cache delle credenziali: verranno lette al prossimo utilizzo.
        return
