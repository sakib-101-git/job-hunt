import { env } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { getKv, setKv } from "../src/db/kv";
import { isoSeconds } from "../src/db/time";
import {
  deleteUserData,
  getUser,
  markBlocked,
  pauseUser,
  resumeUser,
  upsertTelegramUser,
} from "../src/db/users";

const T0 = new Date("2026-10-18T09:00:00.123Z");
const T1 = new Date("2026-10-18T10:00:00Z");
const ALICE = { id: 1001, chatId: 1001, username: "alice", firstName: "Alice" };

describe("migrations", () => {
  it("creates every table as STRICT", async () => {
    const { results } = await env.DB.prepare(
      "SELECT name, strict FROM pragma_table_list WHERE schema = 'main' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '_cf_%' AND name != 'd1_migrations'",
    ).all<{ name: string; strict: number }>();
    expect(results.map((r) => r.name).sort()).toEqual([
      "audits",
      "feedback",
      "job_domains",
      "job_facts",
      "jobs",
      "kv",
      "llm_usage",
      "matches",
      "notifications",
      "runs",
      "source_state",
      "user_prefs",
      "user_profiles",
      "users",
    ]);
    expect(results.every((r) => r.strict === 1)).toBe(true);
  });
});

describe("users repository", () => {
  it("creates a user on first contact", async () => {
    const user = await upsertTelegramUser(env.DB, ALICE, "en", T0);
    expect(user).toMatchObject({
      id: 1001,
      chatId: 1001,
      username: "alice",
      firstName: "Alice",
      status: "active",
      pausedUntil: null,
      onboardingState: null,
      locale: "en",
      createdAt: "2026-10-18T09:00:00Z",
    });
    expect(await getUser(env.DB, 1001)).toEqual(user);
  });

  it("refreshes Telegram details without resetting other fields", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    await pauseUser(env.DB, ALICE.id, null, T0);
    const user = await upsertTelegramUser(env.DB, { ...ALICE, username: "alice2" }, "bn", T1);
    expect(user).toMatchObject({ username: "alice2", status: "paused", locale: "en" });
    expect(user.createdAt).toBe(isoSeconds(T0));
    expect(user.updatedAt).toBe(isoSeconds(T1));
  });

  it("reactivates a user who blocked the bot and came back", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    await markBlocked(env.DB, ALICE.id, T0);
    expect((await getUser(env.DB, ALICE.id))?.status).toBe("blocked");
    expect((await upsertTelegramUser(env.DB, ALICE, "en", T1)).status).toBe("active");
  });

  it("pauses until a date and resumes", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    expect(await pauseUser(env.DB, ALICE.id, T1, T0)).toBe(true);
    expect(await getUser(env.DB, ALICE.id)).toMatchObject({
      status: "paused",
      pausedUntil: "2026-10-18T10:00:00Z",
    });
    expect(await resumeUser(env.DB, ALICE.id, T1)).toBe(true);
    expect(await getUser(env.DB, ALICE.id)).toMatchObject({ status: "active", pausedUntil: null });
    expect(await resumeUser(env.DB, ALICE.id, T1)).toBe(false);
  });

  it("does not pause a blocked user", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    await markBlocked(env.DB, ALICE.id, T0);
    expect(await pauseUser(env.DB, ALICE.id, null, T1)).toBe(false);
  });

  it("parses onboarding state JSON", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    await env.DB.prepare("UPDATE users SET onboarding_state = ? WHERE id = ?")
      .bind(JSON.stringify({ step: "tracks", tracks: ["ai_ml"] }), ALICE.id)
      .run();
    expect((await getUser(env.DB, ALICE.id))?.onboardingState).toEqual({
      step: "tracks",
      tracks: ["ai_ml"],
    });
  });

  it("deletes a user and everything about them", async () => {
    await upsertTelegramUser(env.DB, ALICE, "en", T0);
    await env.DB.batch([
      env.DB.prepare(
        "INSERT INTO jobs (id, source, source_job_id, url, canonical_url, fingerprint, title, posted_at_source, first_seen_at, last_seen_at) VALUES (1, 'bdjobs', '1', 'https://x', 'https://x', 'fp', 'Engineer', 'api', ?1, ?1)",
      ).bind(isoSeconds(T0)),
      env.DB.prepare(
        "INSERT INTO matches (id, user_id, job_id, score, breakdown_json, explanation_json, gate_results_json, scorer_version) VALUES (1, ?, 1, 8, '{}', '{}', '{}', 'v1')",
      ).bind(ALICE.id),
      env.DB.prepare(
        "INSERT INTO notifications (match_id, user_id, job_id, fingerprint) VALUES (1, ?, 1, 'fp')",
      ).bind(ALICE.id),
    ]);
    expect(await deleteUserData(env.DB, ALICE.id)).toBe(true);
    expect(await getUser(env.DB, ALICE.id)).toBeNull();
    for (const table of ["matches", "notifications"]) {
      const row = await env.DB.prepare(`SELECT COUNT(*) AS n FROM ${table}`).first<{ n: number }>();
      expect(row?.n).toBe(0);
    }
    expect(await deleteUserData(env.DB, ALICE.id)).toBe(false);
  });

  it("returns null for an unknown user", async () => {
    expect(await getUser(env.DB, 999)).toBeNull();
  });
});

describe("kv repository", () => {
  it("sets, overwrites and reads values", async () => {
    expect(await getKv(env.DB, "last_successful_run")).toBeNull();
    await setKv(env.DB, "last_successful_run", "2026-10-18T09:00:00Z", T0);
    await setKv(env.DB, "last_successful_run", "2026-10-18T10:00:00Z", T1);
    expect(await getKv(env.DB, "last_successful_run")).toBe("2026-10-18T10:00:00Z");
  });
});
