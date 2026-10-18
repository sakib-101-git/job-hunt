import { env } from "cloudflare:workers";
import type { Message, Update, User, UserFromGetMe } from "grammy/types";

import { createBot } from "../src/bot";
import { CONFIG } from "../src/config";
import type { DispatchEvent } from "../src/dispatch";
import { EN } from "../src/locale";

export const ALICE: User = { id: 1001, is_bot: false, first_name: "Alice", username: "alice" };
export const BOB: User = { id: 1002, is_bot: false, first_name: "Bob" };
export const T0 = new Date("2026-10-18T06:00:00Z");

const BOT_INFO = {
  id: 42,
  is_bot: true,
  first_name: "JobHunt",
  username: "jobhunt_test_bot",
  can_join_groups: false,
  can_read_all_group_messages: false,
  supports_inline_queries: false,
} satisfies Partial<UserFromGetMe> as UserFromGetMe;

export interface ApiCall {
  method: string;
  payload: Record<string, unknown>;
}

let nextId = 1;

/**
 * A bot wired to the test D1 with Telegram's API replaced by a recorder: each
 * call is kept in `calls` and answered with a plausible success.
 */
export function harness(options: { dispatchOk?: boolean } = {}) {
  const calls: ApiCall[] = [];
  const dispatched: { event: DispatchEvent; payload: Record<string, unknown> }[] = [];
  const clock = { now: T0 };
  const bot = createBot(
    "123456:test-bot-token",
    {
      db: env.DB,
      config: CONFIG,
      locale: EN,
      dispatch: (event, payload) => {
        dispatched.push({ event, payload });
        return Promise.resolve(options.dispatchOk ?? true);
      },
      now: () => clock.now,
    },
    BOT_INFO,
  );
  bot.api.config.use((_prev, method, payload) => {
    calls.push({ method, payload: payload });
    const message = { message_id: nextId++, date: 0, chat: { id: ALICE.id, type: "private" } };
    const result = method.startsWith("send") || method === "editMessageText" ? message : true;
    return Promise.resolve({ ok: true, result } as never);
  });

  function chat(from: User): Message["chat"] {
    return { id: from.id, type: "private", first_name: from.first_name };
  }

  async function send(text: string, from: User = ALICE): Promise<ApiCall[]> {
    const command = /^\/\w+/.exec(text);
    const update = {
      update_id: nextId++,
      message: {
        message_id: nextId++,
        date: 0,
        chat: chat(from),
        from,
        text,
        ...(command ? { entities: [{ type: "bot_command", offset: 0, length: command[0].length }] } : {}),
      },
    } as Update;
    return run(update);
  }

  async function tap(data: string, from: User = ALICE): Promise<ApiCall[]> {
    const update = {
      update_id: nextId++,
      callback_query: {
        id: String(nextId++),
        from,
        chat_instance: "test",
        data,
        message: { message_id: 7, date: 0, chat: chat(from), text: "menu" },
      },
    } as Update;
    return run(update);
  }

  async function run(update: Update): Promise<ApiCall[]> {
    const before = calls.length;
    await bot.handleUpdate(update);
    return calls.slice(before);
  }

  return { bot, calls, dispatched, clock, send, tap, run };
}

/** The text of every message sent or edited, in order. */
export function texts(calls: ApiCall[]): string[] {
  return calls
    .filter((c) => c.method === "sendMessage" || c.method === "editMessageText")
    .map((c) => String(c.payload.text));
}

export function answers(calls: ApiCall[]): (string | undefined)[] {
  return calls
    .filter((c) => c.method === "answerCallbackQuery")
    .map((c) => c.payload.text as string | undefined);
}

export function buttons(call: ApiCall | undefined): { text: string; callback_data?: string }[][] {
  const markup = call?.payload.reply_markup as
    | { inline_keyboard: { text: string; callback_data?: string }[][] }
    | undefined;
  return markup?.inline_keyboard ?? [];
}

export async function resetDatabase(): Promise<void> {
  await env.DB.batch([env.DB.prepare("DELETE FROM users"), env.DB.prepare("DELETE FROM jobs")]);
}
