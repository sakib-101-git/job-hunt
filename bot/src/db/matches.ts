import { isoSeconds } from "./time";

export type FeedbackKind = "applied" | "saved" | "hidden" | "thumbs_up" | "thumbs_down";

export interface OwnedMatch {
  id: number;
  score: number;
  whyHtml: string | null;
  title: string;
  company: string | null;
  url: string;
}

interface MatchRow {
  id: number;
  score: number;
  why_html: string | null;
  title: string;
  company: string | null;
  url: string;
}

function toMatch(row: MatchRow): OwnedMatch {
  return {
    id: row.id,
    score: row.score,
    whyHtml: row.why_html,
    title: row.title,
    company: row.company,
    url: row.url,
  };
}

/** The match, only if it belongs to `userId`: every button press is checked. */
export async function getOwnedMatch(
  db: D1Database,
  userId: number,
  matchId: number,
): Promise<OwnedMatch | null> {
  const row = await db
    .prepare(
      `SELECT m.id, m.score, m.why_html, j.title, j.company, j.url
       FROM matches m JOIN jobs j ON j.id = m.job_id
       WHERE m.id = ?1 AND m.user_id = ?2`,
    )
    .bind(matchId, userId)
    .first<MatchRow>();
  return row ? toMatch(row) : null;
}

/** Matches the user marked `kind`, newest mark first. */
export async function listByFeedback(
  db: D1Database,
  userId: number,
  kind: FeedbackKind,
  limit: number,
): Promise<OwnedMatch[]> {
  const { results } = await db
    .prepare(
      `SELECT m.id, m.score, m.why_html, j.title, j.company, j.url
       FROM feedback f
       JOIN matches m ON m.id = f.match_id
       JOIN jobs j ON j.id = m.job_id
       WHERE f.user_id = ?1 AND f.kind = ?2
       ORDER BY f.created_at DESC, f.id DESC
       LIMIT ?3`,
    )
    .bind(userId, kind, limit)
    .all<MatchRow>();
  return results.map(toMatch);
}

export async function hasFeedback(db: D1Database, matchId: number, kind: FeedbackKind): Promise<boolean> {
  const row = await db
    .prepare("SELECT 1 AS found FROM feedback WHERE match_id = ? AND kind = ?")
    .bind(matchId, kind)
    .first<{ found: number }>();
  return row !== null;
}

/** Record feedback; a repeat press updates the reason. `removes` are cleared. */
export async function addFeedback(
  db: D1Database,
  userId: number,
  matchId: number,
  kind: FeedbackKind,
  now: Date,
  options: { reason?: string | null; removes?: FeedbackKind[] } = {},
): Promise<void> {
  const statements = (options.removes ?? []).map((other) =>
    db.prepare("DELETE FROM feedback WHERE match_id = ? AND kind = ?").bind(matchId, other),
  );
  statements.push(
    db
      .prepare(
        `INSERT INTO feedback (user_id, match_id, kind, reason, created_at) VALUES (?1, ?2, ?3, ?4, ?5)
         ON CONFLICT (match_id, kind) DO UPDATE SET reason = COALESCE(excluded.reason, reason)`,
      )
      .bind(userId, matchId, kind, options.reason ?? null, isoSeconds(now)),
  );
  await db.batch(statements);
}

export async function removeFeedback(db: D1Database, matchId: number, kind: FeedbackKind): Promise<void> {
  await db.prepare("DELETE FROM feedback WHERE match_id = ? AND kind = ?").bind(matchId, kind).run();
}

/** Notifications still waiting for the user (what /digest would send). */
export async function waitingCount(db: D1Database, userId: number, maxAttempts: number): Promise<number> {
  const row = await db
    .prepare(
      "SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND status IN ('pending', 'failed') AND attempts < ?",
    )
    .bind(userId, maxAttempts)
    .first<{ n: number }>();
  return row?.n ?? 0;
}
