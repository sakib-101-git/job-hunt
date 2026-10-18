import type { Update } from "grammy/types";
import { z } from "zod";

import { type BotContext, createBot } from "./bot";
import { CONFIG } from "./config";
import { githubDispatcher } from "./dispatch";
import { EN } from "./locale";
import { isValidWebhookSecret, SECRET_HEADER } from "./webhook";
import type { Bot } from "grammy";

export const ROUTES = {
  health: "/health",
  telegram: "/telegram",
} as const;

// Only the updates the bot asks for in setWebhook's allowed_updates.
const UpdateShape = z.looseObject({
  update_id: z.number().int(),
  message: z.looseObject({}).optional(),
  callback_query: z.looseObject({}).optional(),
});

// One bot per isolate: getMe runs once, on the first update that needs it.
let cached: { token: string; bot: Bot<BotContext> } | undefined;

function botFor(env: Env): Bot<BotContext> {
  if (cached?.token !== env.TELEGRAM_BOT_TOKEN) {
    const bot = createBot(env.TELEGRAM_BOT_TOKEN, {
      db: env.DB,
      config: CONFIG,
      locale: EN,
      dispatch: githubDispatcher(CONFIG.github, env.GITHUB_DISPATCH_TOKEN),
      now: () => new Date(),
    });
    cached = { token: env.TELEGRAM_BOT_TOKEN, bot };
  }
  return cached.bot;
}

async function handleTelegram(request: Request, env: Env): Promise<Response> {
  if (!isValidWebhookSecret(request.headers.get(SECRET_HEADER), env.TELEGRAM_WEBHOOK_SECRET)) {
    return new Response("unauthorized", { status: 401 });
  }
  const parsed = UpdateShape.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return new Response("bad request", { status: 400 });
  }
  if (parsed.data.message || parsed.data.callback_query) {
    const bot = botFor(env);
    await bot.init();
    // Errors are logged by bot.catch; Telegram gets a 200 either way, since a
    // retried update would repeat whatever part of it already ran.
    await bot.handleUpdate(parsed.data as unknown as Update);
  }
  return new Response("ok");
}

export default {
  async fetch(request, env): Promise<Response> {
    const { pathname } = new URL(request.url);

    if (request.method === "GET" && pathname === ROUTES.health) {
      return new Response("ok");
    }
    if (request.method === "POST" && pathname === ROUTES.telegram) {
      return handleTelegram(request, env);
    }
    return new Response("not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
