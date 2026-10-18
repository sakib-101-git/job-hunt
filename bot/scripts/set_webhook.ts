// Register the webhook and the command menu with Telegram (run by deploy.yml).
//   TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... WORKER_URL=https://... \
//     node scripts/set_webhook.ts
// Node runs this TypeScript directly (type stripping, Node 23.6+).
import { readFileSync } from "node:fs";

import { setupCalls, webhookUrl } from "./telegram-setup.ts";

interface Answer {
  ok: boolean;
  description?: string;
  result?: unknown;
}

function need(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`${name} is not set`);
  }
  return value;
}

function readJson(relative: string): unknown {
  return JSON.parse(readFileSync(new URL(relative, import.meta.url), "utf-8"));
}

async function call(api: string, method: string, body: Record<string, unknown>): Promise<Answer> {
  const response = await fetch(`${api}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const answer = (await response.json()) as Answer;
  if (!answer.ok) {
    throw new Error(`${method} failed: ${answer.description ?? String(response.status)}`);
  }
  return answer;
}

async function main(): Promise<void> {
  const config = readJson("../src/generated/config.json") as {
    bot: { webhook_path: string };
    notifications: { telegram_api_base_url: string };
  };
  const locale = readJson("../../locales/en.json") as { bot: { commands: Record<string, string> } };
  const workerUrl = need("WORKER_URL");
  const api = `${config.notifications.telegram_api_base_url}/bot${need("TELEGRAM_BOT_TOKEN")}`;
  const calls = setupCalls({
    workerUrl,
    webhookPath: config.bot.webhook_path,
    secret: need("TELEGRAM_WEBHOOK_SECRET"),
    commands: locale.bot.commands,
  });
  for (const { method, body } of calls) {
    await call(api, method, body);
    console.log(`${method}: ok`);
  }
  const info = (await call(api, "getWebhookInfo", {})).result as {
    url: string;
    pending_update_count: number;
    last_error_message?: string;
  };
  if (info.url !== webhookUrl(workerUrl, config.bot.webhook_path)) {
    throw new Error("getWebhookInfo shows a different URL than the one just set");
  }
  console.log(`webhook set; ${String(info.pending_update_count)} updates pending`);
  if (info.last_error_message) {
    console.log(`Telegram's last delivery error: ${info.last_error_message}`);
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
