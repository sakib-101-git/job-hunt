import type { SharedConfig } from "../config";
import { Draft } from "../onboarding/draft";
import { isoSeconds } from "./time";

interface PrefsRow {
  tracks_json: string;
  domain_overrides_json: string;
  experience_level: string;
  locations_json: string;
  preferred_cities_json: string;
  job_types_json: string;
  min_score: number;
  delivery_mode: string;
  quiet_hours_json: string | null;
  skills_json: string | null;
}

export async function hasPreferences(db: D1Database, userId: number): Promise<boolean> {
  const row = await db
    .prepare("SELECT 1 AS found FROM user_prefs WHERE user_id = ?")
    .bind(userId)
    .first<{ found: number }>();
  return row !== null;
}

export async function getOnboardingState(db: D1Database, userId: number): Promise<Draft | null> {
  const row = await db
    .prepare("SELECT onboarding_state FROM users WHERE id = ?")
    .bind(userId)
    .first<{ onboarding_state: string | null }>();
  if (!row?.onboarding_state) {
    return null;
  }
  // A draft saved by an older Worker that no longer parses restarts the flow.
  const parsed = Draft.safeParse(JSON.parse(row.onboarding_state));
  return parsed.success ? parsed.data : null;
}

export async function setOnboardingState(
  db: D1Database,
  userId: number,
  draft: Draft | null,
  now: Date,
): Promise<void> {
  await db
    .prepare("UPDATE users SET onboarding_state = ?2, updated_at = ?3 WHERE id = ?1")
    .bind(userId, draft === null ? null : JSON.stringify(draft), isoSeconds(now))
    .run();
}

/** The saved settings as a /settings draft, opened at `step`. */
export async function draftFromPreferences(
  db: D1Database,
  userId: number,
  config: SharedConfig,
  step: Draft["step"],
): Promise<Draft | null> {
  const row = await db
    .prepare(
      `SELECT p.*, pr.skills_json FROM user_prefs p
       LEFT JOIN user_profiles pr ON pr.user_id = p.user_id
       WHERE p.user_id = ?`,
    )
    .bind(userId)
    .first<PrefsRow>();
  if (!row) {
    return null;
  }
  const known = new Set(config.skills.map((s) => s.id));
  const json = (text: string): unknown => JSON.parse(text);
  const skills = json(row.skills_json ?? "[]") as string[];
  return Draft.parse({
    mode: "settings",
    step,
    tracks: json(row.tracks_json),
    deselected: json(row.domain_overrides_json),
    level: row.experience_level,
    locations: json(row.locations_json),
    cities: json(row.preferred_cities_json),
    jobTypes: json(row.job_types_json),
    skills: skills.filter((s) => known.has(s)),
    unknownSkills: skills.filter((s) => !known.has(s)),
    minScore: row.min_score,
    delivery: row.delivery_mode,
    quiet: row.quiet_hours_json === null ? null : json(row.quiet_hours_json),
  });
}

/**
 * Save a finished draft and end the onboarding session, in one batch. Settings
 * the bot doesn't edit (max_per_cycle, include_adjacent) keep their values.
 */
export async function savePreferences(
  db: D1Database,
  userId: number,
  draft: Draft,
  config: SharedConfig,
  now: Date,
): Promise<void> {
  const at = isoSeconds(now);
  const defaults = config.bot.defaults;
  await db.batch([
    db
      .prepare(
        `INSERT INTO user_prefs (user_id, tracks_json, domain_overrides_json, include_adjacent,
           experience_level, locations_json, preferred_cities_json, job_types_json, min_score,
           delivery_mode, quiet_hours_json, max_per_cycle, updated_at)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13)
         ON CONFLICT (user_id) DO UPDATE SET
           tracks_json = excluded.tracks_json,
           domain_overrides_json = excluded.domain_overrides_json,
           experience_level = excluded.experience_level,
           locations_json = excluded.locations_json,
           preferred_cities_json = excluded.preferred_cities_json,
           job_types_json = excluded.job_types_json,
           min_score = excluded.min_score,
           delivery_mode = excluded.delivery_mode,
           quiet_hours_json = excluded.quiet_hours_json,
           updated_at = excluded.updated_at`,
      )
      .bind(
        userId,
        JSON.stringify(draft.tracks),
        JSON.stringify(draft.deselected),
        defaults.include_adjacent ? 1 : 0,
        draft.level,
        JSON.stringify(draft.locations),
        JSON.stringify(draft.cities),
        JSON.stringify(draft.jobTypes),
        draft.minScore,
        draft.delivery,
        draft.quiet === null ? null : JSON.stringify(draft.quiet),
        defaults.max_per_cycle,
        at,
      ),
    db
      .prepare(
        `INSERT INTO user_profiles (user_id, skills_json, updated_at) VALUES (?1, ?2, ?3)
         ON CONFLICT (user_id) DO UPDATE SET
           skills_json = excluded.skills_json,
           profile_version = profile_version + (skills_json != excluded.skills_json),
           updated_at = excluded.updated_at`,
      )
      .bind(userId, JSON.stringify([...draft.skills, ...draft.unknownSkills]), at),
    db
      .prepare("UPDATE users SET onboarding_state = NULL, updated_at = ?2 WHERE id = ?1")
      .bind(userId, at),
  ]);
}
