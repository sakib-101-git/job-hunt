export interface AdminStatus {
  users: { status: string; n: number }[];
  queue: { status: string; n: number }[];
  llm: { provider: string; requests: number }[];
  lastRun: { trigger: string; status: string; started_at: string } | null;
  sources: {
    source: string;
    last_success_at: string | null;
    consecutive_failures: number;
    last_count: number | null;
  }[];
}

/** What /admin shows, in one round trip. `day` is YYYY-MM-DD (UTC, as llm_usage). */
export async function adminStatus(db: D1Database, day: string): Promise<AdminStatus> {
  const [users, queue, llm, runs, sources] = await db.batch([
    db.prepare("SELECT status, COUNT(*) AS n FROM users GROUP BY status ORDER BY status"),
    db.prepare(
      "SELECT status, COUNT(*) AS n FROM notifications WHERE status IN ('pending', 'failed') GROUP BY status",
    ),
    db
      .prepare(
        "SELECT provider, SUM(requests) AS requests FROM llm_usage WHERE day = ? GROUP BY provider ORDER BY provider",
      )
      .bind(day),
    db.prepare("SELECT trigger, status, started_at FROM runs ORDER BY started_at DESC LIMIT 1"),
    db.prepare(
      "SELECT source, last_success_at, consecutive_failures, last_count FROM source_state ORDER BY source",
    ),
  ]);
  return {
    users: (users?.results ?? []) as AdminStatus["users"],
    queue: (queue?.results ?? []) as AdminStatus["queue"],
    llm: (llm?.results ?? []) as AdminStatus["llm"],
    lastRun: ((runs?.results ?? [])[0] ?? null) as AdminStatus["lastRun"],
    sources: (sources?.results ?? []) as AdminStatus["sources"],
  };
}
