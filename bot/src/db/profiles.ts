import { isoSeconds } from "./time";

/** Remember (or forget, with null) the Telegram file id of the user's CV. */
export async function setCvFile(
  db: D1Database,
  userId: number,
  fileId: string | null,
  now: Date,
): Promise<void> {
  await db
    .prepare(
      `INSERT INTO user_profiles (user_id, cv_file_id, updated_at) VALUES (?1, ?2, ?3)
       ON CONFLICT (user_id) DO UPDATE SET cv_file_id = excluded.cv_file_id, updated_at = excluded.updated_at`,
    )
    .bind(userId, fileId, isoSeconds(now))
    .run();
}

export async function getCvFile(db: D1Database, userId: number): Promise<string | null> {
  const row = await db
    .prepare("SELECT cv_file_id FROM user_profiles WHERE user_id = ?")
    .bind(userId)
    .first<{ cv_file_id: string | null }>();
  return row?.cv_file_id ?? null;
}
