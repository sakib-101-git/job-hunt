import { isValidWebhookSecret, SECRET_HEADER } from "./webhook";

export const ROUTES = {
  health: "/health",
  telegram: "/telegram",
} as const;

export default {
  fetch(request, env): Response {
    const { pathname } = new URL(request.url);

    if (request.method === "GET" && pathname === ROUTES.health) {
      return new Response("ok");
    }

    if (request.method === "POST" && pathname === ROUTES.telegram) {
      if (!isValidWebhookSecret(request.headers.get(SECRET_HEADER), env.TELEGRAM_WEBHOOK_SECRET)) {
        return new Response("unauthorized", { status: 401 });
      }
      // Update handling arrives with the bot implementation; acknowledge so
      // Telegram does not retry.
      return new Response("ok");
    }

    return new Response("not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
