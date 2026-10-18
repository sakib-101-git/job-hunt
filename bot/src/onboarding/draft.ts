import { z } from "zod";

import { Delivery, JobType, Level, Location, QuietHours, type SharedConfig } from "../config";

// Onboarding and /settings edit a draft kept in `users.onboarding_state`, so a
// half-finished setup survives restarts. Each button press is an action that
// is valid on one step only; a press on an older menu is "stale". This module
// is pure: the handlers load the draft, apply the action and save the result.

export const STEPS = [
  "tracks",
  "refine",
  "level",
  "where",
  "cities",
  "job_types",
  "skills",
  "skills_confirm",
  "score",
  "delivery",
  "quiet",
  "summary",
] as const;
export type Step = (typeof STEPS)[number];

// The summary's "Edit" buttons, each opening its first step.
export const SECTIONS = ["tracks", "level", "where", "job_types", "skills", "score"] as const;
export type Section = (typeof SECTIONS)[number];

export const Draft = z.strictObject({
  mode: z.enum(["onboarding", "settings"]),
  step: z.enum(STEPS),
  tracks: z.array(z.string()),
  deselected: z.record(z.string(), z.array(z.string())),
  level: Level.nullable(),
  locations: z.array(Location),
  cities: z.array(z.string()),
  jobTypes: z.array(JobType),
  skills: z.array(z.string()),
  unknownSkills: z.array(z.string()),
  minScore: z.number(),
  delivery: Delivery,
  quiet: QuietHours.nullable(),
});
export type Draft = z.infer<typeof Draft>;

export const ACTIONS = {
  t: "tracks", // t:<track> toggle a track
  r: "tracks", // open refine
  d: "refine", // d:<track>:<domain> toggle a domain inside a track
  b: "refine", // back to tracks
  l: "level", // l:<level> pick
  w: "where", // w:<location> toggle
  c: "cities", // c:<city> toggle
  j: "job_types", // j:<type> toggle
  s: "skills", // skip skills
  k: "skills_confirm", // the parsed skills look right
  y: "skills_confirm", // type them again
  m: "score", // m:<option> pick the minimum score
  v: "delivery", // v:<mode> pick
  q: "quiet", // q:<option> pick quiet hours; q alone means none
  e: "summary", // e:<section> edit a section
  f: "summary", // finish: save the settings
} as const satisfies Record<string, Step>;
export type ActionCode = keyof typeof ACTIONS;

export interface Action {
  code: ActionCode | "n"; // n: Next, valid on every multi-select step
  args: number[];
}

const NEXT_STEPS: readonly Step[] = ["tracks", "where", "cities", "job_types"];

export type Outcome =
  | { kind: "ok"; draft: Draft }
  | { kind: "finish"; draft: Draft }
  | { kind: "pick_one" }
  | { kind: "stale" };

/** Parse "o:t:3" style callback data. Anything malformed is null. */
export function parseAction(data: string): Action | null {
  const match = /^o:([a-z])((?::\d{1,3}){0,2})$/.exec(data);
  if (!match) {
    return null;
  }
  const code = match[1] ?? "";
  if (code !== "n" && !(code in ACTIONS)) {
    return null;
  }
  const args = (match[2] ?? "").split(":").filter(Boolean).map(Number);
  return { code: code as Action["code"], args };
}

export function actionData(code: Action["code"], ...args: number[]): string {
  return ["o", code, ...args.map(String)].join(":");
}

export function newDraft(config: SharedConfig): Draft {
  const defaults = config.bot.defaults;
  return {
    mode: "onboarding",
    step: "tracks",
    tracks: [],
    deselected: {},
    level: null,
    locations: [],
    cities: [],
    jobTypes: [],
    skills: [],
    unknownSkills: [],
    minScore: defaults.min_score,
    delivery: defaults.delivery_mode,
    quiet: null,
  };
}

function toggle<T>(list: readonly T[], item: T): T[] {
  return list.includes(item) ? list.filter((x) => x !== item) : [...list, item];
}

/** Where a step leads once it is done. /settings returns to the summary. */
export function after(draft: Draft, step: Step): Step {
  const settings = draft.mode === "settings";
  switch (step) {
    case "tracks":
    case "refine":
      return settings ? "summary" : "level";
    case "level":
      return settings ? "summary" : "where";
    case "where":
      if (draft.locations.includes("bangladesh")) return "cities";
      return settings ? "summary" : "job_types";
    case "cities":
      return settings ? "summary" : "job_types";
    case "job_types":
      return settings ? "summary" : "skills";
    case "skills":
    case "skills_confirm":
      return settings ? "summary" : "score";
    case "score":
      return "delivery";
    case "delivery":
      return "quiet";
    case "quiet":
    case "summary":
      return "summary";
  }
}

function at<T>(list: readonly T[], index: number | undefined): T | undefined {
  return index === undefined ? undefined : list[index];
}

export function apply(draft: Draft, action: Action, config: SharedConfig): Outcome {
  const step: Step | undefined = action.code === "n" ? undefined : ACTIONS[action.code];
  if (step === undefined ? !NEXT_STEPS.includes(draft.step) : step !== draft.step) {
    return { kind: "stale" };
  }
  const [a, b] = action.args;
  const ok = (changes: Partial<Draft>): Outcome => ({ kind: "ok", draft: { ...draft, ...changes } });
  const bot = config.bot;

  switch (action.code) {
    case "n":
      return next(draft);
    case "t": {
      const track = at(config.tracks, a);
      if (!track) return { kind: "stale" };
      const tracks = toggle(draft.tracks, track.id);
      const deselected = Object.fromEntries(
        Object.entries(draft.deselected).filter(([id]) => tracks.includes(id)),
      );
      return ok({ tracks, deselected });
    }
    case "r":
      return draft.tracks.length ? ok({ step: "refine" }) : { kind: "pick_one" };
    case "d": {
      const track = at(config.tracks, a);
      const domain = track && at(track.primary_domains, b);
      if (!track || !domain || !draft.tracks.includes(track.id)) return { kind: "stale" };
      const off = toggle(draft.deselected[track.id] ?? [], domain);
      if (off.length >= track.primary_domains.length) return { kind: "pick_one" };
      const others = Object.entries(draft.deselected).filter(([id]) => id !== track.id);
      const deselected = Object.fromEntries(off.length ? [...others, [track.id, off]] : others);
      return ok({ deselected });
    }
    case "b":
      return ok({ step: "tracks" });
    case "l": {
      const level = at(bot.experience_levels, a);
      return level ? ok({ level, step: after(draft, "level") }) : { kind: "stale" };
    }
    case "w": {
      const location = at(bot.locations, a);
      if (!location) return { kind: "stale" };
      const locations = toggle(draft.locations, location);
      return ok({ locations, cities: locations.includes("bangladesh") ? draft.cities : [] });
    }
    case "c": {
      const city = at(config.preferred_cities, a);
      return city ? ok({ cities: toggle(draft.cities, city.id) }) : { kind: "stale" };
    }
    case "j": {
      const type = at(bot.job_types, a);
      return type ? ok({ jobTypes: toggle(draft.jobTypes, type) }) : { kind: "stale" };
    }
    case "s":
    case "k":
      return ok({ step: after(draft, draft.step) });
    case "y":
      return ok({ step: "skills" });
    case "m": {
      const score = at(bot.min_score_options, a);
      return score === undefined ? { kind: "stale" } : ok({ minScore: score, step: "delivery" });
    }
    case "v": {
      const mode = at(Delivery.options, a);
      return mode ? ok({ delivery: mode, step: "quiet" }) : { kind: "stale" };
    }
    case "q": {
      if (a === undefined) return ok({ quiet: null, step: "summary" });
      const hours = at(bot.quiet_hours_options, a);
      return hours ? ok({ quiet: hours, step: "summary" }) : { kind: "stale" };
    }
    case "e": {
      const section = at(SECTIONS, a);
      return section ? ok({ step: section }) : { kind: "stale" };
    }
    case "f":
      return isComplete(draft) ? { kind: "finish", draft } : { kind: "stale" };
  }
}

function next(draft: Draft): Outcome {
  const required: Partial<Record<Step, readonly unknown[]>> = {
    tracks: draft.tracks,
    where: draft.locations,
    job_types: draft.jobTypes,
  };
  const chosen = required[draft.step];
  if (chosen !== undefined && chosen.length === 0) {
    return { kind: "pick_one" };
  }
  return { kind: "ok", draft: { ...draft, step: after(draft, draft.step) } };
}

/** Text typed during the skills step. */
export function withSkills(draft: Draft, ids: string[], unknown: string[]): Draft {
  return { ...draft, skills: ids, unknownSkills: unknown, step: "skills_confirm" };
}

export function acceptsSkillText(draft: Draft): boolean {
  return draft.step === "skills" || draft.step === "skills_confirm";
}

export function isComplete(draft: Draft): boolean {
  return (
    draft.tracks.length > 0 &&
    draft.level !== null &&
    draft.locations.length > 0 &&
    draft.jobTypes.length > 0
  );
}
