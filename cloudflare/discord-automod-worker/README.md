# Discord AutoMod application filter Worker

Cloudflare Worker used as the Discord **Interactions Endpoint URL** for the experimental
app-driven AutoMod / Guild Policy flow.

## Current mode: probe-first

The public Discord documentation exposes the `GUILD_POLICY` AutoMod trigger, but the
request/decision envelope for the app-driven filter shown in the Discord client is not
documented sufficiently to safely hard-code a BLOCK/ALLOW response.

This Worker therefore does three things safely:

1. validates Discord Ed25519 signatures;
2. answers the standard Discord `PING` with `PONG`;
3. captures the signed non-PING payload in KV (redacted by default) so the exact
   experimental contract can be observed before implementing the final decision adapter.

For non-PING experimental requests it currently returns HTTP 204 (fail-open probe mode).

## Endpoints

- `POST /interactions` - Discord interactions endpoint
- `GET /health` - public health check
- `GET /admin/captures/latest?guild_id=...` - latest captured payload
- `GET /admin/guilds/:guild_id/config` - guild policy config
- `PUT /admin/guilds/:guild_id/config` - update guild policy config

Admin endpoints require:

`Authorization: Bearer <CONTROL_TOKEN>`

## Required secrets

- `DISCORD_PUBLIC_KEY` - Developer Portal -> General Information -> Public Key
- `CONTROL_TOKEN` - a long random secret shared only with the Red cog

Never commit either secret to Git.

## Deploy

From this directory:

```bash
npm install
npx wrangler login
npx wrangler kv namespace create AUTOMOD_KV
```

Copy the returned namespace id into `wrangler.toml`:

```toml
[[kv_namespaces]]
binding = "AUTOMOD_KV"
id = "YOUR_NAMESPACE_ID"
```

Then set secrets:

```bash
npx wrangler secret put DISCORD_PUBLIC_KEY
npx wrangler secret put CONTROL_TOKEN
```

Deploy:

```bash
npm run deploy
```

Wrangler will print a URL similar to:

`https://discord-automod-filter.<account>.workers.dev`

Use this in Discord:

`https://discord-automod-filter.<account>.workers.dev/interactions`

## Payload capture privacy

By default message content is redacted in captures. To temporarily capture message
content while reverse-engineering the experimental payload, set:

```toml
[vars]
CAPTURE_MESSAGE_CONTENT = "true"
```

Deploy, perform one controlled test message, inspect the capture through the Red cog,
then set it back to `false`.

Captured payloads expire automatically according to `CAPTURE_TTL_SECONDS` (default 24h).

## Important if this is the same application used by Red

Configuring an Interactions Endpoint URL changes delivery of normal application
interactions for that application. If the Red bot currently uses slash commands,
buttons or modals via Gateway, do not switch the production application blindly.

The safest test path is a second Discord application dedicated to this AutoMod filter.
