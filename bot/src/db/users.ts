import { isoSeconds } from "./time";

export type UserStatus = "active" | "paused" | "blocked" | "deleted";

export interface User {
  id: number;
  chatId: number;
  username: string | null;
  firstName: string | null;
  status: UserStatus;
  pausedUntil: string | null;
  onboardingState: unknown;
  locale: string;
  createdAt: string;
  updatedAt: string;
}

export interface TelegramIdentity {
  id: number;
  chatId: number;
  username?: string | undefined;
  firstName?: string | undefined;
}

interface UserRow {
  id: number;
  chat_id: number;
  username: string | null;
  first_name: string | null;
  status: UserStatus;
  paused_until: string | null;
  onboarding_state: string | null;
  locale: string;
  created_at: string;
  updated_at: string;
}

function toUser(row: UserRow): User {
  return {
    id: row.id,
    chatId: row.chat_id,
    username: row.username,
    firstName: row.first_name,
    status: row.status,
    pausedUntil: row.paused_until,
    onboardingState: row.onboarding_state === null ? null : JSON.parse(row.onboarding_state),
    locale: row.locale,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  };
}

export async function getUser(db: D1Database, id: number): Promise<User | null> {
  const row = await db.prepare("SELECT * FROM users WHERE id = ?").bind(id).first<UserRow>();
  return row ? toUser(row) : null;
}

/**
 * Create the user on first contact, or refresh their Telegram details. A user
 * who blocked the bot or deleted their data and comes back starts as active.
 */
export async function upsertTelegramUser(
  db: D1Database,
  who: TelegramIdentity,
  locale: string,
  now: Date,
): Promise<User> {
  const at = isoSeconds(now);
  const row = await db
    .prepare(
      `INSERT INTO users (id, chat_id, username, first_name, locale, created_at, updated_at)
       VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?6)
       ON CONFLICT (id) DO UPDATE SET
         chat_id = excluded.chat_id,
         username = excluded.username,
         first_name = excluded.first_name,
         status = CASE WHEN status IN ('blocked', 'deleted') THEN 'active' ELSE status END,
         updated_at = excluded.updated_at
       RETURNING *`,
    )
    .bind(who.id, who.chatId, who.username ?? null, who.firstName ?? null, locale, at)
    .first<UserRow>();
  if (!row) {
    throw new Error(`upsert of user ${String(who.id)} returned no row`);
  }
  return toUser(row);
}

/** Pause notifications; `until` null means until the user resumes. */
export async function pauseUser(
  db: D1Database,
  id: number,
  until: Date | null,
  now: Date,
): Promise<boolean> {
  const result = await db
    .prepare(
      "UPDATE users SET status = 'paused', paused_until = ?2, updated_at = ?3 WHERE id = ?1 AND status IN ('active', 'paused')",
    )
    .bind(id, until ? isoSeconds(until) : null, isoSeconds(now))
    .run();
  return result.meta.changes > 0;
}

export async function resumeUser(db: D1Database, id: number, now: Date): Promise<boolean> {
  const result = await db
    .prepare(
      "UPDATE users SET status = 'active', paused_until = NULL, updated_at = ?2 WHERE id = ?1 AND status = 'paused'",
    )
    .bind(id, isoSeconds(now))
    .run();
  return result.meta.changes > 0;
}

/** Telegram answered 403: the user blocked the bot. */
export async function markBlocked(db: D1Database, id: number, now: Date): Promise<void> {
  await db
    .prepare("UPDATE users SET status = 'blocked', updated_at = ?2 WHERE id = ?1")
    .bind(id, isoSeconds(now))
    .run();
}

/** `/delete`: remove the user and, through foreign keys, everything about them. */
export async function deleteUserData(db: D1Database, id: number): Promise<boolean> {
  const result = await db.prepare("DELETE FROM users WHERE id = ?").bind(id).run();
  return result.meta.changes > 0;
}
