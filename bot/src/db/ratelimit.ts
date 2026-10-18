import { isoSeconds } from "./time";

const WINDOW_MS = 60_000;

/**
 * Count one update against the user's fixed one-minute window and return the
 * count so far, the current update included. One row per user.
 */
export async function countUpdate(db: D1Database, userId: number, now: Date): Promise<number> {
  const row = await db
    .prepare(
      `INSERT INTO rate_limits (user_id, window_start, count) VALUES (?1, ?2, 1)
       ON CONFLICT (user_id) DO UPDATE SET
         count = CASE WHEN window_start <= ?3 THEN 1 ELSE count + 1 END,
         window_start = CASE WHEN window_start <= ?3 THEN ?2 ELSE window_start END
       RETURNING count`,
    )
    .bind(userId, isoSeconds(now), isoSeconds(new Date(now.getTime() - WINDOW_MS)))
    .first<{ count: number }>();
  return row?.count ?? 1;
}
