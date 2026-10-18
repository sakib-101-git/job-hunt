/** `/export`: everything stored about one user, as plain JSON. */
export async function exportUserData(db: D1Database, userId: number): Promise<object | null> {
  const [user, prefs, profile, matches, feedback] = await db.batch([
    db
      .prepare(
        "SELECT id, username, first_name, status, paused_until, locale, created_at, updated_at FROM users WHERE id = ?",
      )
      .bind(userId),
    db.prepare("SELECT * FROM user_prefs WHERE user_id = ?").bind(userId),
    db
      .prepare(
        "SELECT skills_json, education_json, summary, cv_parsed_at, profile_version, updated_at FROM user_profiles WHERE user_id = ?",
      )
      .bind(userId),
    db
      .prepare(
        `SELECT m.id, m.score, m.scorer_version, m.created_at, j.title, j.company, j.url
         FROM matches m JOIN jobs j ON j.id = m.job_id
         WHERE m.user_id = ? ORDER BY m.created_at`,
      )
      .bind(userId),
    db
      .prepare(
        "SELECT match_id, kind, reason, created_at FROM feedback WHERE user_id = ? ORDER BY created_at",
      )
      .bind(userId),
  ]);
  const first = user?.results[0];
  if (!first) {
    return null;
  }
  return {
    user: first,
    preferences: prefs?.results[0] ?? null,
    profile: profile?.results[0] ?? null,
    matches: matches?.results ?? [],
    feedback: feedback?.results ?? [],
  };
}
