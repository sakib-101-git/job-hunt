import type { Bot } from "grammy";

import type { BotContext, BotDeps } from "../context";
import {
  addFeedback,
  type FeedbackKind,
  getOwnedMatch,
  hasFeedback,
  listByFeedback,
  type OwnedMatch,
  removeFeedback,
  waitingCount,
} from "../db/matches";
import { safe } from "../locale";

export interface AlertPress {
  code: string;
  matchId: number;
  option: number | undefined;
}

/** "wy:123" or "rs:123:2"; anything else is null. */
export function parseAlertData(data: string): AlertPress | null {
  const match = /^([a-z]{1,4}):(\d{1,15})(?::(\d{1,2}))?$/.exec(data);
  if (!match) return null;
  const matchId = Number(match[2]);
  if (!Number.isSafeInteger(matchId)) return null;
  return { code: match[1] ?? "", matchId, option: match[3] === undefined ? undefined : Number(match[3]) };
}

export function registerAlerts(bot: Bot<BotContext>, deps: BotDeps): void {
  const { db, config, locale: t } = deps;
  const codes = config.callbacks;
  const alertCodes = new Set(Object.values(codes));

  function listText(title: string, matches: OwnedMatch[]): string {
    const lines = [t.html(title), ""];
    for (const m of matches) {
      lines.push(
        t.html("bot.lists.item", {
          id: m.id,
          url: m.url,
          title: m.title,
          company: m.company ? safe(t.html("bot.lists.company", { company: m.company })) : "",
          score: m.score,
        }),
      );
    }
    lines.push("", t.html("bot.lists.footer"));
    return lines.join("\n");
  }

  async function toggle(
    ctx: BotContext,
    userId: number,
    match: OwnedMatch,
    kind: FeedbackKind,
    keys: { on: string; off: string },
  ): Promise<void> {
    if (await hasFeedback(db, match.id, kind)) {
      await removeFeedback(db, match.id, kind);
      await ctx.answerCallbackQuery(t.plain(keys.off));
    } else {
      await addFeedback(db, userId, match.id, kind, ctx.now);
      await ctx.answerCallbackQuery(t.plain(keys.on));
    }
  }

  bot.on("callback_query:data", async (ctx, next) => {
    const press = parseAlertData(ctx.callbackQuery.data);
    if (!press || !alertCodes.has(press.code)) {
      await next();
      return;
    }
    const user = ctx.user;
    const match = user ? await getOwnedMatch(db, user.id, press.matchId) : null;
    if (!user || !match) {
      await ctx.answerCallbackQuery(t.plain("bot.match_gone"));
      return;
    }
    switch (press.code) {
      case codes.why:
        await ctx.answerCallbackQuery();
        await ctx.reply(match.whyHtml ?? t.html("bot.why_missing"), {
          parse_mode: "HTML",
          link_preview_options: { is_disabled: true },
        });
        return;
      case codes.applied:
        await toggle(ctx, user.id, match, "applied", {
          on: "bot.feedback.applied_on",
          off: "bot.feedback.applied_off",
        });
        return;
      case codes.save:
        await toggle(ctx, user.id, match, "saved", {
          on: "bot.feedback.saved_on",
          off: "bot.feedback.saved_off",
        });
        return;
      case codes.hide:
        await addFeedback(db, user.id, match.id, "hidden", ctx.now);
        await ctx.answerCallbackQuery(t.plain("bot.feedback.hidden"));
        // Telegram lets a bot delete its messages for 48 hours; older ones stay.
        await ctx.deleteMessage().catch(() => undefined);
        return;
      case codes.thumbs_up:
        await addFeedback(db, user.id, match.id, "thumbs_up", ctx.now, { removes: ["thumbs_down"] });
        await ctx.answerCallbackQuery(t.plain("bot.feedback.thanks"));
        return;
      case codes.thumbs_down: {
        await addFeedback(db, user.id, match.id, "thumbs_down", ctx.now, { removes: ["thumbs_up"] });
        await ctx.answerCallbackQuery();
        const reasons = config.bot.feedback_reasons.map((reason, i) => ({
          text: t.plain(`bot.reasons.${reason}`),
          callback_data: `${codes.reason}:${String(match.id)}:${String(i)}`,
        }));
        await ctx.reply(t.html("bot.feedback.reason_question", { title: match.title }), {
          parse_mode: "HTML",
          reply_markup: { inline_keyboard: [reasons.slice(0, 3), reasons.slice(3)] },
        });
        return;
      }
      case codes.reason: {
        const reason = press.option === undefined ? undefined : config.bot.feedback_reasons[press.option];
        if (reason === undefined) {
          await ctx.answerCallbackQuery(t.plain("bot.stale"));
          return;
        }
        await addFeedback(db, user.id, match.id, "thumbs_down", ctx.now, {
          reason,
          removes: ["thumbs_up"],
        });
        await ctx.answerCallbackQuery();
        await ctx.editMessageText(
          t.plain("bot.feedback.reason_thanks", { reason: t.plain(`bot.reasons.${reason}`) }),
        );
        return;
      }
    }
  });

  for (const [command, kind, title, empty] of [
    ["saved", "saved", "bot.lists.saved", "bot.lists.saved_empty"],
    ["applied", "applied", "bot.lists.applied", "bot.lists.applied_empty"],
  ] as const) {
    bot.command(command, async (ctx) => {
      if (!ctx.user) {
        await ctx.reply(t.plain("bot.not_set_up"));
        return;
      }
      const matches = await listByFeedback(db, ctx.user.id, kind, config.bot.list_limit);
      if (!matches.length) {
        await ctx.reply(t.plain(empty));
        return;
      }
      await ctx.reply(listText(title, matches), {
        parse_mode: "HTML",
        link_preview_options: { is_disabled: true },
      });
    });
  }

  bot.command("why", async (ctx) => {
    if (!ctx.user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const arg = ctx.match.trim();
    if (!/^\d{1,15}$/.test(arg)) {
      await ctx.reply(t.plain("bot.why_usage"));
      return;
    }
    const match = await getOwnedMatch(db, ctx.user.id, Number(arg));
    if (!match) {
      await ctx.reply(t.plain("bot.match_gone"));
      return;
    }
    await ctx.reply(match.whyHtml ?? t.html("bot.why_missing"), {
      parse_mode: "HTML",
      link_preview_options: { is_disabled: true },
    });
  });

  bot.command("digest", async (ctx) => {
    if (!ctx.user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    if ((await waitingCount(db, ctx.user.id, config.notifications.max_attempts)) === 0) {
      await ctx.reply(t.plain("bot.digest_empty"));
      return;
    }
    const queued = await deps.dispatch("send-digest", { user_id: ctx.user.id });
    await ctx.reply(t.plain(queued ? "bot.digest_requested" : "bot.dispatch_failed"));
  });
}
