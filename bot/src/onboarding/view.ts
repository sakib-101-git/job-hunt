import type { InlineKeyboardButton } from "grammy/types";

import { Delivery, type SharedConfig } from "../config";
import type { Locale } from "../locale";
import { actionData, type Draft, SECTIONS } from "./draft";

export type Keyboard = InlineKeyboardButton[][];

export interface View {
  text: string;
  keyboard: Keyboard;
}

function button(text: string, data: string): InlineKeyboardButton {
  return { text, callback_data: data };
}

function rows<T>(items: readonly T[], perRow: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += perRow) {
    out.push(items.slice(i, i + perRow));
  }
  return out;
}

export function render(draft: Draft, config: SharedConfig, t: Locale): View {
  const bot = config.bot;
  const mark = (on: boolean, label: string): string =>
    on ? t.plain("bot.options.selected", { label }) : label;
  const next = button(t.plain("bot.buttons.next"), actionData("n"));
  const domainNames = new Map(config.domains.map((d) => [d.slug, d.name]));

  switch (draft.step) {
    case "tracks": {
      const toggles = config.tracks.map((track, i) =>
        button(mark(draft.tracks.includes(track.id), track.label), actionData("t", i)),
      );
      return {
        text: t.html("bot.steps.tracks"),
        keyboard: [
          ...rows(toggles, 2),
          [button(t.plain("bot.buttons.refine"), actionData("r")), next],
        ],
      };
    }
    case "refine": {
      const keyboard: Keyboard = [];
      config.tracks.forEach((track, ti) => {
        if (!draft.tracks.includes(track.id)) return;
        const off = draft.deselected[track.id] ?? [];
        track.primary_domains.forEach((slug, di) => {
          const name = domainNames.get(slug) ?? slug;
          keyboard.push([button(mark(!off.includes(slug), name), actionData("d", ti, di))]);
        });
      });
      keyboard.push([button(t.plain("bot.buttons.done"), actionData("b"))]);
      return { text: t.html("bot.steps.refine"), keyboard };
    }
    case "level":
      return {
        text: t.html("bot.steps.level"),
        keyboard: bot.experience_levels.map((level, i) => [
          button(
            mark(draft.level === level, t.plain(`bot.options.levels.${level}`)),
            actionData("l", i),
          ),
        ]),
      };
    case "where":
      return {
        text: t.html("bot.steps.where"),
        keyboard: [
          ...bot.locations.map((loc, i) => [
            button(
              mark(draft.locations.includes(loc), t.plain(`bot.options.locations.${loc}`)),
              actionData("w", i),
            ),
          ]),
          [next],
        ],
      };
    case "cities": {
      const toggles = config.preferred_cities.map((city, i) =>
        button(mark(draft.cities.includes(city.id), city.name), actionData("c", i)),
      );
      return { text: t.html("bot.steps.cities"), keyboard: [...rows(toggles, 3), [next]] };
    }
    case "job_types":
      return {
        text: t.html("bot.steps.job_types"),
        keyboard: [
          ...rows(
            bot.job_types.map((type, i) =>
              button(
                mark(draft.jobTypes.includes(type), t.plain(`employment.${type}`)),
                actionData("j", i),
              ),
            ),
            2,
          ),
          [next],
        ],
      };
    case "skills":
      return {
        text: t.html("bot.steps.skills"),
        keyboard: [[button(t.plain("bot.buttons.skip"), actionData("s"))]],
      };
    case "skills_confirm": {
      const lines = [t.html("bot.steps.skills_confirm", { skills: skillList(draft, config, t) })];
      if (draft.unknownSkills.length) {
        lines.push(t.html("bot.skills_unknown", { skills: draft.unknownSkills.join(", ") }));
      }
      return {
        text: lines.join("\n"),
        keyboard: [
          [
            button(t.plain("bot.buttons.looks_right"), actionData("k")),
            button(t.plain("bot.buttons.retype"), actionData("y")),
          ],
        ],
      };
    }
    case "score":
      return {
        text: t.html("bot.steps.score"),
        keyboard: [
          bot.min_score_options.map((score, i) =>
            button(
              mark(draft.minScore === score, t.plain("bot.options.score", { score })),
              actionData("m", i),
            ),
          ),
        ],
      };
    case "delivery":
      return {
        text: t.html("bot.steps.delivery"),
        keyboard: [
          Delivery.options.map((mode, i) =>
            button(
              mark(draft.delivery === mode, t.plain(`bot.options.delivery.${mode}`)),
              actionData("v", i),
            ),
          ),
        ],
      };
    case "quiet":
      return {
        text: t.html("bot.steps.quiet", { zone: config.timezone }),
        keyboard: [
          ...bot.quiet_hours_options.map((hours, i) => [
            button(
              mark(
                draft.quiet?.start === hours.start && draft.quiet.end === hours.end,
                t.plain("bot.options.quiet", hours),
              ),
              actionData("q", i),
            ),
          ]),
          [button(mark(draft.quiet === null, t.plain("bot.options.no_quiet")), actionData("q"))],
        ],
      };
    case "summary":
      return summary(draft, config, t);
  }
}

function skillList(draft: Draft, config: SharedConfig, t: Locale): string {
  const names = new Map(config.skills.map((s) => [s.id, s.name]));
  const all = [...draft.skills.map((id) => names.get(id) ?? id), ...draft.unknownSkills];
  return all.length ? all.join(", ") : t.plain("bot.skills_none");
}

export function summary(draft: Draft, config: SharedConfig, t: Locale): View {
  const sep = t.plain("bot.summary.list_separator");
  const none = t.plain("bot.summary.none");
  const list = (items: string[]): string => (items.length ? items.join(sep) : none);
  const domainNames = new Map(config.domains.map((d) => [d.slug, d.name]));
  const tracks = config.tracks
    .filter((track) => draft.tracks.includes(track.id))
    .map((track) => {
      const off = draft.deselected[track.id] ?? [];
      if (!off.length) return track.label;
      const domains = off.map((slug) => domainNames.get(slug) ?? slug).join(sep);
      return t.plain("bot.summary.refined", { track: track.label, domains });
    });
  const cities = config.preferred_cities.filter((c) => draft.cities.includes(c.id));
  const quiet = draft.quiet ? t.plain("bot.options.quiet", draft.quiet) : none;
  const lines = [
    t.html("bot.steps.summary"),
    "",
    t.html("bot.summary.tracks", { value: list(tracks) }),
    t.html("bot.summary.level", {
      value: draft.level ? t.plain(`bot.options.levels.${draft.level}`) : none,
    }),
    t.html("bot.summary.where", {
      value: list(draft.locations.map((loc) => t.plain(`bot.options.locations.${loc}`))),
    }),
  ];
  if (draft.locations.includes("bangladesh")) {
    lines.push(t.html("bot.summary.cities", { value: list(cities.map((c) => c.name)) }));
  }
  lines.push(
    t.html("bot.summary.job_types", {
      value: list(draft.jobTypes.map((type) => t.plain(`employment.${type}`))),
    }),
    t.html("bot.summary.skills", { value: skillList(draft, config, t) }),
    t.html("bot.summary.score", { value: draft.minScore }),
    t.html("bot.summary.delivery", { value: t.plain(`bot.options.delivery.${draft.delivery}`) }),
    t.html("bot.summary.quiet", { value: quiet }),
  );
  const edits = SECTIONS.map((section, i) =>
    button(
      t.plain("bot.buttons.edit", { section: t.plain(`bot.sections.${section}`) }),
      actionData("e", i),
    ),
  );
  const finish = draft.mode === "onboarding" ? "bot.buttons.start" : "bot.buttons.save";
  return {
    text: lines.join("\n"),
    keyboard: [...rows(edits, 2), [button(t.plain(finish), actionData("f"))]],
  };
}
