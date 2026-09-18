const JSON_HEADERS = {
  "content-type": "application/json; charset=utf-8",
  "cache-control": "no-store",
};

const DEFAULT_CONFIG = {
  enabled: false,
  block_words: [],
  blocked_domains: [],
  regex_patterns: [],
  max_mentions: 0,
  exempt_role_ids: [],
  exempt_channel_ids: [],
  exempt_user_ids: [],
};

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return json({
        ok: true,
        service: "discord-automod-filter",
        mode: "probe",
        kv: Boolean(env.AUTOMOD_KV),
      });
    }

    if (url.pathname === "/interactions" && request.method === "POST") {
      return handleDiscordInteraction(request, env, ctx);
    }

    if (url.pathname.startsWith("/admin/")) {
      if (!isAdminRequest(request, env)) {
        return json({ ok: false, error: "unauthorized" }, 401);
      }

      const configMatch = url.pathname.match(/^\/admin\/guilds\/(\d+)\/config$/);
      if (configMatch) {
        const guildId = configMatch[1];

        if (request.method === "GET") {
          const config = await getGuildConfig(env, guildId);
          return json({ ok: true, guild_id: guildId, config });
        }

        if (request.method === "PUT") {
          let body;
          try {
            body = await request.json();
          } catch {
            return json({ ok: false, error: "invalid_json" }, 400);
          }

          const config = normalizeConfig(body);
          await env.AUTOMOD_KV.put("config:guild:" + guildId, JSON.stringify(config));
          return json({ ok: true, guild_id: guildId, config });
        }

        return json({ ok: false, error: "method_not_allowed" }, 405);
      }

      if (url.pathname === "/admin/captures/latest" && request.method === "GET") {
        const guildId = url.searchParams.get("guild_id");
        const key = guildId ? "capture:guild:" + guildId : "capture:latest";
        const capture = await env.AUTOMOD_KV.get(key, { type: "json" });
        if (!capture) {
          return json({ ok: false, error: "no_capture" }, 404);
        }
        return json({ ok: true, capture });
      }

      return json({ ok: false, error: "not_found" }, 404);
    }

    return json({ ok: false, error: "not_found" }, 404);
  },
};

async function handleDiscordInteraction(request, env, ctx) {
  if (!env.DISCORD_PUBLIC_KEY) {
    return json({ ok: false, error: "missing_discord_public_key" }, 500);
  }

  const rawBody = await request.text();
  const valid = await verifyDiscordRequest(request, rawBody, env.DISCORD_PUBLIC_KEY);
  if (!valid) {
    return json({ ok: false, error: "invalid_signature" }, 401);
  }

  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return json({ ok: false, error: "invalid_json" }, 400);
  }

  // Discord endpoint validation: PING -> PONG.
  if (payload && payload.type === 1) {
    return json({ type: 1 });
  }

  const extracted = extractContext(payload);
  const config = extracted.guildId
    ? await getGuildConfig(env, extracted.guildId)
    : { ...DEFAULT_CONFIG };
  const evaluation = evaluatePolicy(extracted, config);

  const capture = {
    received_at: new Date().toISOString(),
    guild_id: extracted.guildId,
    channel_id: extracted.channelId,
    user_id: extracted.userId,
    top_level_keys: payload && typeof payload === "object" ? Object.keys(payload) : [],
    evaluation,
    payload: sanitizePayload(
      payload,
      String(env.CAPTURE_MESSAGE_CONTENT || "").toLowerCase() === "true"
    ),
  };

  const ttl = clampInt(env.CAPTURE_TTL_SECONDS, 300, 604800, 86400);
  const writes = [
    env.AUTOMOD_KV.put("capture:latest", JSON.stringify(capture), { expirationTtl: ttl }),
  ];
  if (extracted.guildId) {
    writes.push(
      env.AUTOMOD_KV.put(
        "capture:guild:" + extracted.guildId,
        JSON.stringify(capture),
        { expirationTtl: ttl }
      )
    );
  }
  ctx.waitUntil(Promise.all(writes));

  // IMPORTANT:
  // The app-driven Guild Policy / AutoMod experiment is not publicly documented
  // enough to safely invent its decision response envelope. In probe mode we
  // capture the signed request and fail open with an empty 204 response.
  //
  // Once the real payload/response contract is known, replace this adapter only;
  // policy evaluation and Red <-> Worker configuration can stay unchanged.
  return new Response(null, {
    status: 204,
    headers: { "cache-control": "no-store" },
  });
}

async function verifyDiscordRequest(request, rawBody, publicKeyHex) {
  const signatureHex = request.headers.get("X-Signature-Ed25519");
  const timestamp = request.headers.get("X-Signature-Timestamp");

  if (!signatureHex || !timestamp) {
    return false;
  }
  if (!/^[0-9a-f]{128}$/i.test(signatureHex) || !/^[0-9a-f]{64}$/i.test(publicKeyHex)) {
    return false;
  }

  try {
    const key = await crypto.subtle.importKey(
      "raw",
      hexToBytes(publicKeyHex),
      { name: "Ed25519" },
      false,
      ["verify"]
    );
    const data = new TextEncoder().encode(timestamp + rawBody);
    return await crypto.subtle.verify(
      { name: "Ed25519" },
      key,
      hexToBytes(signatureHex),
      data
    );
  } catch (error) {
    console.error("Signature verification error", error);
    return false;
  }
}

function hexToBytes(hex) {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i += 1) {
    bytes[i] = Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function isAdminRequest(request, env) {
  if (!env.CONTROL_TOKEN) {
    return false;
  }
  const header = request.headers.get("Authorization") || "";
  return header === "Bearer " + env.CONTROL_TOKEN;
}

async function getGuildConfig(env, guildId) {
  const stored = await env.AUTOMOD_KV.get("config:guild:" + guildId, { type: "json" });
  return normalizeConfig(stored || DEFAULT_CONFIG);
}

function normalizeConfig(value) {
  const input = value && typeof value === "object" ? value : {};
  return {
    enabled: Boolean(input.enabled),
    block_words: cleanStringArray(input.block_words, 200, 200),
    blocked_domains: cleanStringArray(input.blocked_domains, 200, 253)
      .map((v) => v.toLowerCase().replace(/^https?:\/\//, "").replace(/^www\./, "").replace(/\/$/, "")),
    regex_patterns: cleanStringArray(input.regex_patterns, 50, 500),
    max_mentions: clampInt(input.max_mentions, 0, 100, 0),
    exempt_role_ids: cleanSnowflakeArray(input.exempt_role_ids, 100),
    exempt_channel_ids: cleanSnowflakeArray(input.exempt_channel_ids, 100),
    exempt_user_ids: cleanSnowflakeArray(input.exempt_user_ids, 100),
  };
}

function cleanStringArray(value, maxItems, maxLength) {
  if (!Array.isArray(value)) {
    return [];
  }
  const out = [];
  const seen = new Set();
  for (const item of value) {
    const text = String(item || "").trim().slice(0, maxLength);
    if (!text || seen.has(text)) {
      continue;
    }
    seen.add(text);
    out.push(text);
    if (out.length >= maxItems) {
      break;
    }
  }
  return out;
}

function cleanSnowflakeArray(value, maxItems) {
  return cleanStringArray(value, maxItems, 32).filter((v) => /^\d{5,32}$/.test(v));
}

function clampInt(value, min, max, fallback) {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, parsed));
}

function extractContext(payload) {
  const candidates = [
    payload,
    payload && payload.data,
    payload && payload.d,
    payload && payload.event,
    payload && payload.message,
    payload && payload.data && payload.data.message,
    payload && payload.d && payload.d.message,
  ].filter(Boolean);

  return {
    guildId: firstScalar(candidates, ["guild_id"], ["guildId"], ["guild", "id"]),
    channelId: firstScalar(candidates, ["channel_id"], ["channelId"], ["channel", "id"]),
    userId: firstScalar(
      candidates,
      ["user_id"],
      ["userId"],
      ["author_id"],
      ["authorId"],
      ["user", "id"],
      ["author", "id"],
      ["member", "user", "id"]
    ),
    content:
      firstScalar(candidates, ["content"], ["message_content"], ["text"]) || "",
    roleIds: firstArray(
      candidates,
      ["role_ids"],
      ["roles"],
      ["member", "roles"]
    ),
  };
}

function firstScalar(objects, ...paths) {
  for (const object of objects) {
    for (const path of paths) {
      const value = readPath(object, path);
      if (typeof value === "string" || typeof value === "number") {
        return String(value);
      }
    }
  }
  return null;
}

function firstArray(objects, ...paths) {
  for (const object of objects) {
    for (const path of paths) {
      const value = readPath(object, path);
      if (Array.isArray(value)) {
        return value.map(String);
      }
    }
  }
  return [];
}

function readPath(object, path) {
  const parts = Array.isArray(path) ? path : [path];
  let current = object;
  for (const part of parts) {
    if (!current || typeof current !== "object") {
      return undefined;
    }
    current = current[part];
  }
  return current;
}

function evaluatePolicy(context, config) {
  if (!config.enabled) {
    return { action: "allow", reasons: ["disabled"] };
  }

  if (context.userId && config.exempt_user_ids.includes(context.userId)) {
    return { action: "allow", reasons: ["exempt_user"] };
  }
  if (context.channelId && config.exempt_channel_ids.includes(context.channelId)) {
    return { action: "allow", reasons: ["exempt_channel"] };
  }
  if (context.roleIds.some((id) => config.exempt_role_ids.includes(id))) {
    return { action: "allow", reasons: ["exempt_role"] };
  }

  const reasons = [];
  const content = String(context.content || "");
  const folded = content.toLocaleLowerCase();

  for (const word of config.block_words) {
    if (folded.includes(word.toLocaleLowerCase())) {
      reasons.push("blocked_word:" + word);
      break;
    }
  }

  const domains = extractDomains(content);
  for (const blocked of config.blocked_domains) {
    if (domains.some((domain) => domain === blocked || domain.endsWith("." + blocked))) {
      reasons.push("blocked_domain:" + blocked);
      break;
    }
  }

  for (const pattern of config.regex_patterns) {
    try {
      if (new RegExp(pattern, "iu").test(content)) {
        reasons.push("regex:" + pattern);
        break;
      }
    } catch {
      // Invalid expressions are ignored here; the Cog keeps configuration editable.
    }
  }

  if (config.max_mentions > 0) {
    const mentions = content.match(/<@!?\d+>/g) || [];
    if (mentions.length > config.max_mentions) {
      reasons.push("mention_limit:" + mentions.length);
    }
  }

  return reasons.length
    ? { action: "block", reasons }
    : { action: "allow", reasons: [] };
}

function extractDomains(content) {
  const matches = String(content || "").matchAll(
    /(?:https?:\/\/)?(?:www\.)?([a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?\.[a-z]{2,63})(?=[:\/?#\s]|$)/giu
  );
  return Array.from(matches, (match) => String(match[1]).toLowerCase());
}

function sanitizePayload(payload, allowContent) {
  const sensitiveKeys = new Set([
    "token",
    "authorization",
    "password",
    "secret",
    "email",
    "phone",
  ]);
  const contentKeys = new Set([
    "content",
    "message_content",
    "clean_content",
  ]);

  function walk(value, key = "", depth = 0) {
    if (depth > 12) {
      return "[max-depth]";
    }

    const lower = String(key).toLowerCase();
    if (sensitiveKeys.has(lower)) {
      return "[redacted]";
    }
    if (!allowContent && contentKeys.has(lower) && typeof value === "string") {
      return "[redacted:" + value.length + " chars]";
    }

    if (Array.isArray(value)) {
      return value.slice(0, 50).map((item) => walk(item, key, depth + 1));
    }
    if (value && typeof value === "object") {
      const out = {};
      for (const [childKey, childValue] of Object.entries(value).slice(0, 100)) {
        out[childKey] = walk(childValue, childKey, depth + 1);
      }
      return out;
    }
    if (typeof value === "string" && value.length > 4000) {
      return value.slice(0, 4000) + "[truncated]";
    }
    return value;
  }

  return walk(payload);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: JSON_HEADERS,
  });
}
