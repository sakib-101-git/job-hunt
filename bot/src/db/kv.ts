import { isoSeconds } from "./time";

/** Small key-value settings: heartbeat timestamps and feature flags. */
export async function getKv(db: D1Database, key: string): Promise<string | null> {
  const row = await db
    .prepare("SELECT value FROM kv WHERE key = ?")
    .bind(key)
    .first<{ value: string }>();
  return row?.value ?? null;
}

export async function setKv(db: D1Database, key: string, value: string, now: Date): Promise<void> {
  await db
    .prepare(
      `INSERT INTO kv (key, value, updated_at) VALUES (?1, ?2, ?3)
       ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
    )
    .bind(key, value, isoSeconds(now))
    .run();
}

export async function deleteKv(db: D1Database, key: string): Promise<void> {
  await db.prepare("DELETE FROM kv WHERE key = ?").bind(key).run();
}
