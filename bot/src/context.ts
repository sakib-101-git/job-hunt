import { type Context, GrammyError } from "grammy";
import type { InlineKeyboardButton } from "grammy/types";

import type { SharedConfig } from "./config";
import type { User } from "./db/users";
import type { Dispatcher } from "./dispatch";
import type { Locale } from "./locale";

export interface BotDeps {
  db: D1Database;
  config: SharedConfig;
  locale: Locale;
  dispatch: Dispatcher;
  now: () => Date;
  adminIds: ReadonlySet<number>;
}

interface Flavor {
  user: User | null;
  now: Date;
}

export type BotContext = Context & Flavor;

export interface View {
  text: string;
  keyboard: InlineKeyboardButton[][];
}

/** Send `view` as a new message, or edit the pressed message in place. */
export async function show(ctx: BotContext, view: View, edit: boolean): Promise<void> {
  const options = { parse_mode: "HTML" as const, reply_markup: { inline_keyboard: view.keyboard } };
  if (edit && ctx.callbackQuery?.message) {
    try {
      await ctx.editMessageText(view.text, options);
    } catch (error) {
      if (!(error instanceof GrammyError && error.description.includes("message is not modified"))) {
        throw error;
      }
    }
    return;
  }
  await ctx.reply(view.text, options);
}

/** "25 Oct" in the app's time zone, from the locale's month names. */
export function formatDate(date: Date, config: SharedConfig, t: Locale): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: config.timezone,
    day: "numeric",
    month: "numeric",
  }).formatToParts(date);
  const part = (type: string): number => Number(parts.find((p) => p.type === type)?.value);
  return t.plain("bot.date", { day: part("day"), month: t.list("months")[part("month") - 1] ?? "" });
}

/** "123,456" → {123, 456}; anything that isn't a positive integer is ignored. */
export function parseAdminIds(raw: string | undefined): Set<number> {
  return new Set(
    (raw ?? "")
      .split(",")
      .map((s) => s.trim())
      .filter((s) => /^\d+$/.test(s))
      .map(Number),
  );
}
