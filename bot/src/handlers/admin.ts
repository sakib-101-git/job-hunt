import type { Bot } from "grammy";

import type { BotContext, BotDeps } from "../context";
import { adminStatus } from "../db/admin";
import { deleteKv, getKv, setKv } from "../db/kv";
import { isoSeconds } from "../db/time";
import { escapeHtml } from "../locale";

// Admin commands, for the Telegram ids in ADMIN_TELEGRAM_IDS only. Anyone else
// gets the same answer as for an unknown command.

const BROADCAST_YES = "bc:y";
const BROADCAST_NO = "bc:n";

export function registerAdmin(bot: Bot<BotContext>, deps: BotDeps): void {
  const { db, locale: t } = deps;
  const admins = bot.filter((ctx): ctx is BotContext => deps.adminIds.has(ctx.from?.id ?? -1));
  const draftKey = (id: number): string => `broadcast:${String(id)}`;

  admins.command("admin", async (ctx) => {
    const now = isoSeconds(ctx.now);
    const status = await adminStatus(db, now.slice(0, 10));
    const none = t.plain("bot.admin.none");
    const counts = (rows: { name: string; count: number }[]): string =>
      rows.length ? rows.map((r) => t.plain("bot.admin.count", r)).join(", ") : none;
    const queue = Object.fromEntries(status.queue.map((r) => [r.status, r.n]));
    const lines = [
      t.html("bot.admin.title", { time: now }),
      t.html("bot.admin.users", {
        counts: counts(status.users.map((r) => ({ name: r.status, count: r.n }))),
      }),
      t.html("bot.admin.queue", { pending: queue.pending ?? 0, failed: queue.failed ?? 0 }),
      t.html("bot.admin.llm", {
        usage: counts(status.llm.map((r) => ({ name: r.provider, count: r.requests }))),
      }),
      status.lastRun
        ? t.html("bot.admin.run", {
            trigger: status.lastRun.trigger,
            status: status.lastRun.status,
            at: status.lastRun.started_at,
          })
        : t.html("bot.admin.no_run"),
      "",
      t.html("bot.admin.sources"),
      ...status.sources.map((s) =>
        t.html("bot.admin.source", {
          mark: t.plain(s.consecutive_failures ? "bot.admin.bad_mark" : "bot.admin.ok_mark"),
          name: s.source,
          count: s.last_count ?? 0,
          at: s.last_success_at ?? t.plain("bot.admin.never"),
          failures: s.consecutive_failures
            ? t.plain("bot.admin.failures", { count: s.consecutive_failures })
            : "",
        }),
      ),
    ];
    await ctx.reply(lines.join("\n"), { parse_mode: "HTML" });
  });

  admins.command("broadcast", async (ctx) => {
    const text = ctx.match.trim();
    if (!text) {
      await ctx.reply(t.plain("bot.admin.broadcast_usage"));
      return;
    }
    await setKv(db, draftKey(ctx.from?.id ?? 0), escapeHtml(text), ctx.now);
    await ctx.reply(t.html("bot.admin.broadcast_preview", { text }), {
      parse_mode: "HTML",
      reply_markup: {
        inline_keyboard: [
          [
            { text: t.plain("bot.buttons.send"), callback_data: BROADCAST_YES },
            { text: t.plain("bot.buttons.cancel"), callback_data: BROADCAST_NO },
          ],
        ],
      },
    });
  });

  admins.callbackQuery([BROADCAST_YES, BROADCAST_NO], async (ctx) => {
    const key = draftKey(ctx.from.id);
    const html = await getKv(db, key);
    await ctx.answerCallbackQuery();
    if (html === null) {
      await ctx.editMessageText(t.plain("bot.stale"));
      return;
    }
    await deleteKv(db, key);
    if (ctx.callbackQuery.data === BROADCAST_NO) {
      await ctx.editMessageText(t.plain("bot.admin.broadcast_cancelled"));
      return;
    }
    // Sending to every user runs in the pipeline, within Telegram's limits.
    const queued = await deps.dispatch("broadcast", { html, admin_id: ctx.from.id });
    await ctx.editMessageText(
      queued ? t.plain("bot.admin.broadcast_queued") : t.plain("bot.dispatch_failed"),
    );
  });
}
