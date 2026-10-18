// What deploy.yml asks Telegram for after each deploy (brief §7.1): the
// webhook, with its secret and only the update types the bot handles, and the
// command menu from the locale. Pure, so tests can check it; set_webhook.ts
// sends it. Re-running it is harmless: every call sets the same state.

/** The update types src/index.ts accepts; Telegram drops the rest. */
export const ALLOWED_UPDATES = ["message", "callback_query"] as const;

export interface TelegramCall {
  method: string;
  body: Record<string, unknown>;
}

export interface SetupInput {
  workerUrl: string;
  webhookPath: string;
  secret: string;
  commands: Record<string, string>;
}

export function webhookUrl(workerUrl: string, webhookPath: string): string {
  const url = new URL(webhookPath, workerUrl);
  if (url.protocol !== "https:") {
    throw new Error("the Worker URL must use https");
  }
  return url.toString();
}

export function setupCalls(input: SetupInput): TelegramCall[] {
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(input.secret)) {
    // Telegram's rule for secret_token.
    throw new Error("TELEGRAM_WEBHOOK_SECRET must be 1-256 characters of A-Z, a-z, 0-9, _ and -");
  }
  return [
    {
      method: "setWebhook",
      body: {
        url: webhookUrl(input.workerUrl, input.webhookPath),
        secret_token: input.secret,
        allowed_updates: ALLOWED_UPDATES,
        drop_pending_updates: false,
      },
    },
    {
      method: "setMyCommands",
      body: {
        commands: Object.entries(input.commands).map(([command, description]) => ({
          command,
          description,
        })),
      },
    },
  ];
}
