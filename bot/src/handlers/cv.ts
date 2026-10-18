import type { Bot } from "grammy";

import { type BotContext, type BotDeps, show } from "../context";
import { getOnboardingState, setOnboardingState } from "../db/prefs";
import { getCvFile, setCvFile } from "../db/profiles";
import { withCv } from "../onboarding/draft";
import { render } from "../onboarding/view";

// CV intake (brief §6.10): the Worker keeps only Telegram's file id and, after
// the user agrees, asks the pipeline to parse it. The PDF never passes through
// the Worker.

const CV_YES = "cv:y";
const CV_NO = "cv:n";
const PDF = "application/pdf";
const MB = 1_000_000;

export function registerCv(bot: Bot<BotContext>, deps: BotDeps): void {
  const { db, config, locale: t } = deps;
  const mb = Math.round((config.bot.cv_max_bytes / MB) * 10) / 10;

  bot.command("cv", async (ctx) => {
    await ctx.reply(t.plain(ctx.user ? "bot.cv.hint" : "bot.not_set_up", { mb }));
  });

  bot.on("message:document", async (ctx) => {
    const user = ctx.user;
    if (!user) {
      await ctx.reply(t.plain("bot.not_set_up"));
      return;
    }
    const doc = ctx.msg.document;
    if (doc.mime_type !== PDF) {
      await ctx.reply(t.plain("bot.cv.not_pdf"));
      return;
    }
    if ((doc.file_size ?? 0) > config.bot.cv_max_bytes) {
      await ctx.reply(t.plain("bot.cv.too_big", { mb }));
      return;
    }
    await setCvFile(db, user.id, doc.file_id, ctx.now);
    await ctx.reply(t.html("bot.cv.consent"), {
      parse_mode: "HTML",
      reply_markup: {
        inline_keyboard: [
          [
            { text: t.plain("bot.buttons.parse_cv"), callback_data: CV_YES },
            { text: t.plain("bot.buttons.cancel"), callback_data: CV_NO },
          ],
        ],
      },
    });
  });

  bot.callbackQuery([CV_YES, CV_NO], async (ctx) => {
    const user = ctx.user;
    if (!user || !(await getCvFile(db, user.id))) {
      await ctx.answerCallbackQuery(t.plain("bot.stale"));
      return;
    }
    await ctx.answerCallbackQuery();
    if (ctx.callbackQuery.data === CV_NO) {
      await setCvFile(db, user.id, null, ctx.now);
      await ctx.editMessageText(t.plain("bot.cv.cancelled"));
      return;
    }
    if (!(await deps.dispatch("parse-cv", { user_id: user.id }))) {
      await ctx.editMessageText(t.plain("bot.dispatch_failed"));
      return;
    }
    await ctx.editMessageText(t.plain("bot.cv.queued"));
    // Sent during setup: the skills step is done, the CV fills it in.
    const draft = await getOnboardingState(db, user.id);
    if (draft?.step === "skills") {
      const moved = withCv(draft);
      await setOnboardingState(db, user.id, moved, ctx.now);
      await show(ctx, render(moved, config, t), false);
    }
  });
}
