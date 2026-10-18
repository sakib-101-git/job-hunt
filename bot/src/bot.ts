import { Bot, type Context, GrammyError, InputFile } from "grammy";
import type { UserFromGetMe } from "grammy/types";

import type { SharedConfig } from "./config";
import { exportUserData } from "./db/export";
import {
  draftFromPreferences,
  getOnboardingState,
  hasPreferences,
  savePreferences,
  setOnboardingState,
} from "./db/prefs";
import { countUpdate } from "./db/ratelimit";
import {
  deleteUserData,
  getUser,
  pauseUser,
  resumeUser,
  upsertTelegramUser,
  type User,
} from "./db/users";
import type { Dispatcher } from "./dispatch";
import type { Locale } from "./locale";
import {
  acceptsSkillText,
  apply,
  type Draft,
  newDraft,
  parseAction,
  withSkills,
} from "./onboarding/draft";
import { parseSkills, skillIndex } from "./onboarding/skills";
import { render, type View } from "./onboarding/view";

export interface BotDeps {
  db: D1Database;
  config: SharedConfig;
  locale: Locale;
  dispatch: Dispatcher;
  now: () => Date;
}

interface Flavor {
  user: User | null;
  now: Date;
}

export type BotContext = Context & Flavor;

// Callback data for /delete's confirmation; alert buttons use the shared codes.
const DELETE_YES = "x:y";
const DELETE_NO = "x:n";
const DAY_MS = 86_400_000;

export function createBot(token: string, deps: BotDeps, botInfo?: UserFromGetMe): Bot<BotContext> {
  const { db, config, locale: t } = deps;
  const bot = new Bot<BotContext>(token, botInfo ? { botInfo } : {});
  const skills = skillIndex(config.skills);

  // Private chats only; load the user and apply the per-user update limit.
  bot.use(async (ctx, next) => {
    if (ctx.chat?.type !== "private" || !ctx.from) {
      return;
    }
    ctx.now = deps.now();
    ctx.user = await getUser(db, ctx.from.id);
    if (ctx.user) {
      const count = await countUpdate(db, ctx.user.id, ctx.now);
      const limit = config.bot.updates_per_minute;
      if (count > limit) {
        if (ctx.callbackQuery) {
          await ctx.answerCallbackQuery(t.plain("bot.slow_down"));
        } else if (count === limit + 1) {
          await ctx.reply(t.plain("bot.slow_down"));
        }
        return;
      }
    }
    await next();
  });

  async function show(ctx: BotContext, view: View, edit: boolean): Promise<void> {
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

  async function startDraft(ctx: BotContext, user: User, draft: Draft): Promise<void> {
    await setOnboardingState(db, user.id, draft, ctx.now);
    await show(ctx, render(draft, config, t), false);
  }

  /** The user's open draft, or a message telling them how to get one. */
  async function openSettings(ctx: BotContext, step: Draft["step"]): Promise<void> {
    const user = ctx.user;
    if (!user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const open = await getOnboardingState(db, user.id);
    if (open?.mode === "onboarding") {
      await ctx.reply(t.plain("bot.finish_setup_first"));
      await show(ctx, render(open, config, t), false);
      return;
    }
    const draft = await draftFromPreferences(db, user.id, config, step);
    if (!draft) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    await startDraft(ctx, user, draft);
  }

  function formatDate(date: Date): string {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: config.timezone,
      day: "numeric",
      month: "numeric",
    }).formatToParts(date);
    const part = (type: string): number => Number(parts.find((p) => p.type === type)?.value);
    return t.plain("bot.date", { day: part("day"), month: t.list("months")[part("month") - 1] ?? "" });
  }

  bot.command("start", async (ctx) => {
    const from = ctx.from;
    if (!from) return;
    const user = await upsertTelegramUser(
      db,
      { id: from.id, chatId: ctx.chat.id, username: from.username, firstName: from.first_name },
      "en",
      ctx.now,
    );
    const name = from.first_name;
    const open = await getOnboardingState(db, user.id);
    if (open) {
      await ctx.reply(t.plain("bot.resume_setup"));
      await show(ctx, render(open, config, t), false);
    } else if (await hasPreferences(db, user.id)) {
      await ctx.reply(t.html("bot.welcome_back", { name }), { parse_mode: "HTML" });
    } else {
      await ctx.reply(t.html("bot.welcome", { name }), { parse_mode: "HTML" });
      await startDraft(ctx, user, newDraft(config));
    }
  });

  bot.command("settings", (ctx) => openSettings(ctx, "summary"));
  bot.command("tracks", (ctx) => openSettings(ctx, "tracks"));
  bot.command("skills", (ctx) => openSettings(ctx, "skills"));
  bot.command("help", (ctx) => ctx.reply(t.html("bot.help"), { parse_mode: "HTML" }));
  bot.command("privacy", (ctx) => ctx.reply(t.html("bot.privacy"), { parse_mode: "HTML" }));

  bot.command("pause", async (ctx) => {
    if (!ctx.user || !(await hasPreferences(db, ctx.user.id))) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const arg = ctx.match.trim();
    const max = config.bot.max_pause_days;
    const days = arg === "" ? null : /^\d+$/.test(arg) ? Number(arg) : NaN;
    if (days !== null && !(days >= 1 && days <= max)) {
      await ctx.reply(t.plain("bot.pause_usage", { max }));
      return;
    }
    const until = days === null ? null : new Date(ctx.now.getTime() + days * DAY_MS);
    await pauseUser(db, ctx.user.id, until, ctx.now);
    await ctx.reply(
      until ? t.plain("bot.paused_until", { date: formatDate(until) }) : t.plain("bot.paused"),
    );
  });

  bot.command("resume", async (ctx) => {
    if (!ctx.user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const resumed = await resumeUser(db, ctx.user.id, ctx.now);
    await ctx.reply(t.plain(resumed ? "bot.resumed" : "bot.not_paused"));
  });

  bot.command("export", async (ctx) => {
    const data = ctx.user ? await exportUserData(db, ctx.user.id) : null;
    if (!data) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const file = new TextEncoder().encode(JSON.stringify(data, null, 2));
    await ctx.replyWithDocument(new InputFile(file, "jobhunt-data.json"), {
      caption: t.plain("bot.export_caption"),
    });
  });

  bot.command("delete", async (ctx) => {
    if (!ctx.user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    await ctx.reply(t.html("bot.delete_confirm"), {
      parse_mode: "HTML",
      reply_markup: {
        inline_keyboard: [
          [
            { text: t.plain("bot.buttons.delete_yes"), callback_data: DELETE_YES },
            { text: t.plain("bot.buttons.cancel"), callback_data: DELETE_NO },
          ],
        ],
      },
    });
  });

  bot.callbackQuery([DELETE_YES, DELETE_NO], async (ctx) => {
    await ctx.answerCallbackQuery();
    if (ctx.callbackQuery.data === DELETE_NO) {
      await ctx.editMessageText(t.plain("bot.delete_cancelled"));
      return;
    }
    if (ctx.user) {
      await deleteUserData(db, ctx.user.id);
    }
    await ctx.editMessageText(t.plain("bot.deleted"));
  });

  bot.callbackQuery(/^o:/, async (ctx) => {
    const user = ctx.user;
    const action = parseAction(ctx.callbackQuery.data);
    const draft = user && action ? await getOnboardingState(db, user.id) : null;
    if (!user || !action || !draft) {
      await ctx.answerCallbackQuery(t.plain("bot.stale"));
      return;
    }
    const outcome = apply(draft, action, config);
    switch (outcome.kind) {
      case "stale":
        await ctx.answerCallbackQuery(t.plain("bot.stale"));
        return;
      case "pick_one":
        await ctx.answerCallbackQuery(t.plain("bot.pick_one"));
        return;
      case "ok":
        await setOnboardingState(db, user.id, outcome.draft, ctx.now);
        await ctx.answerCallbackQuery();
        await show(ctx, render(outcome.draft, config, t), true);
        return;
      case "finish": {
        await savePreferences(db, user.id, outcome.draft, config, ctx.now);
        await ctx.answerCallbackQuery();
        await show(ctx, { ...render(outcome.draft, config, t), keyboard: [] }, true);
        if (outcome.draft.mode === "settings") {
          await ctx.reply(t.plain("bot.saved"));
          return;
        }
        const queued = await deps.dispatch("backfill-user", { user_id: user.id });
        await ctx.reply(
          queued
            ? t.plain("bot.started", { days: config.max_age_days })
            : t.plain("bot.started_later"),
        );
        return;
      }
    }
  });

  bot.on("message:text", async (ctx) => {
    const user = ctx.user;
    if (!user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const draft = ctx.msg.text.startsWith("/") ? null : await getOnboardingState(db, user.id);
    if (!draft || !acceptsSkillText(draft)) {
      await ctx.reply(t.plain("bot.unknown_text"));
      return;
    }
    const parsed = parseSkills(ctx.msg.text, skills, config.bot);
    const updated = withSkills(draft, parsed.ids, parsed.unknown);
    await setOnboardingState(db, user.id, updated, ctx.now);
    await show(ctx, render(updated, config, t), false);
  });

  // Any other button (an old menu, or a feature not handled here) still gets
  // an answer, so the user's client stops its loading spinner.
  bot.on("callback_query", (ctx) => ctx.answerCallbackQuery(t.plain("bot.stale")));

  bot.catch((err) => {
    const update = err.ctx.update;
    console.error(
      JSON.stringify({
        msg: "update failed",
        update_id: update.update_id,
        error: err.error instanceof Error ? err.error.message : String(err.error),
      }),
    );
  });

  return bot;
}
