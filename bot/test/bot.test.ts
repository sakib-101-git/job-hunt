import { env } from "cloudflare:workers";
import type { Update } from "grammy/types";
import { beforeEach, describe, expect, it } from "vitest";

import { CONFIG } from "../src/config";
import { getUser } from "../src/db/users";
import { ALICE, answers, BOB, buttons, harness, resetDatabase, T0, texts } from "./harness";

beforeEach(resetDatabase);

async function prefs(userId: number) {
  return env.DB.prepare(
    "SELECT p.*, pr.skills_json FROM user_prefs p JOIN user_profiles pr USING (user_id) WHERE user_id = ?",
  )
    .bind(userId)
    .first();
}

async function state(userId: number): Promise<unknown> {
  const row = await env.DB.prepare("SELECT onboarding_state FROM users WHERE id = ?")
    .bind(userId)
    .first<{ onboarding_state: string | null }>();
  return row?.onboarding_state ? JSON.parse(row.onboarding_state) : null;
}

/** Onboard Alice: AI track, fresh grad, Bangladesh (Dhaka), full-time. */
async function onboard(h: ReturnType<typeof harness>): Promise<void> {
  await h.send("/start");
  for (const data of ["o:t:0", "o:n", "o:l:1", "o:w:0", "o:n", "o:c:0", "o:n", "o:j:0", "o:n"]) {
    await h.tap(data);
  }
  await h.send("Python, sql, Quantum Knitting");
  for (const data of ["o:k", "o:m:1", "o:v:0", "o:q:0", "o:f"]) {
    await h.tap(data);
  }
}

describe("onboarding", () => {
  it("greets a new user and opens the tracks menu", async () => {
    const h = harness();
    const calls = await h.send("/start");
    const [welcome, menu] = texts(calls);
    expect(welcome).toContain("Hi Alice!");
    expect(menu).toContain("1/7 · Tracks");
    expect(buttons(calls[1]).flat()).toHaveLength(CONFIG.tracks.length + 2);
    expect(await getUser(env.DB, ALICE.id)).toMatchObject({ username: "alice", status: "active" });
    expect(await state(ALICE.id)).toMatchObject({ mode: "onboarding", step: "tracks" });
  });

  it("saves what the pipeline reads and asks for a backfill", async () => {
    const h = harness();
    await onboard(h);
    expect(await prefs(ALICE.id)).toMatchObject({
      tracks_json: '["ai_ml"]',
      domain_overrides_json: "{}",
      include_adjacent: 1,
      experience_level: "fresh_grad",
      locations_json: '["bangladesh"]',
      preferred_cities_json: '["dhaka"]',
      job_types_json: '["full_time"]',
      min_score: 6,
      delivery_mode: "instant",
      quiet_hours_json: '{"start":"22:00","end":"08:00"}',
      max_per_cycle: CONFIG.bot.defaults.max_per_cycle,
      skills_json: '["python","sql","quantum knitting"]',
    });
    expect(await state(ALICE.id)).toBeNull();
    expect(h.dispatched).toEqual([{ event: "backfill-user", payload: { user_id: ALICE.id } }]);
    expect(texts(h.calls).at(-1)).toContain(`from the last ${String(CONFIG.max_age_days)} days`);
  });

  it("edits the menu in place and confirms typed skills", async () => {
    const h = harness();
    await h.send("/start");
    const calls = await h.tap("o:t:0");
    expect(calls.map((c) => c.method)).toEqual(["answerCallbackQuery", "editMessageText"]);
    expect(buttons(calls[1])[0]?.[0]?.text).toBe("✅ 🤖 AI & Machine Learning");
    for (const data of ["o:n", "o:l:1", "o:w:1", "o:n", "o:j:0", "o:n"]) await h.tap(data);
    const [confirm] = texts(await h.send("python3, <b>Rust</b>"));
    // Unknown skills are kept as typed, lower-cased, and escaped.
    expect(confirm).toContain("Python, &lt;b&gt;rust&lt;/b&gt;");
  });

  it("answers pick-one and stale presses without changing anything", async () => {
    const h = harness();
    await h.send("/start");
    expect(answers(await h.tap("o:n"))).toEqual(["Pick at least one."]);
    expect(answers(await h.tap("o:l:0"))).toEqual(["That menu is out of date."]);
    expect(answers(await h.tap("o:garbage"))).toEqual(["That menu is out of date."]);
    expect(await state(ALICE.id)).toMatchObject({ step: "tracks", tracks: [] });
  });

  it("resumes a half-finished setup on /start", async () => {
    const h = harness();
    await h.send("/start");
    await h.tap("o:t:0");
    await h.tap("o:n");
    const [resume, menu] = texts(await h.send("/start"));
    expect(resume).toBe("Let's finish setting you up.");
    expect(menu).toContain("2/7 · Experience");
  });

  it("says when matching starts with the next run instead", async () => {
    const h = harness({ dispatchOk: false });
    await onboard(h);
    expect(texts(h.calls).at(-1)).toContain("first matches arrive with the next run");
  });
});

describe("settings", () => {
  it("edits one section and saves without a backfill", async () => {
    const h = harness();
    await onboard(h);
    h.dispatched.length = 0;
    const [summary] = texts(await h.send("/settings"));
    expect(summary).toContain("🎓 Experience: Fresh graduate");
    await h.tap("o:e:1"); // Experience
    await h.tap("o:l:2"); // junior
    const done = await h.tap("o:f");
    expect(texts(done).at(-1)).toBe("💾 Saved.");
    expect(await prefs(ALICE.id)).toMatchObject({ experience_level: "junior", min_score: 6 });
    expect(await state(ALICE.id)).toBeNull();
    expect(h.dispatched).toEqual([]);
  });

  it("opens /skills with the saved skills", async () => {
    const h = harness();
    await onboard(h);
    await h.send("/skills");
    await h.send("postgres");
    await h.tap("o:k");
    await h.tap("o:f");
    expect((await prefs(ALICE.id))?.skills_json).toBe('["postgresql"]');
  });

  it("needs a finished setup", async () => {
    const h = harness();
    expect(texts(await h.send("/settings"))).toEqual(["You're not set up yet. Send /start."]);
    await h.send("/start");
    const [first] = texts(await h.send("/tracks"));
    expect(first).toBe("Let's finish setting you up first.");
  });
});

describe("pause and resume", () => {
  it("pauses for N days, until resumed, and validates the argument", async () => {
    const h = harness();
    await onboard(h);
    expect(texts(await h.send("/pause 7"))).toEqual([
      "⏸ Paused until 25 Oct. /resume starts alerts again.",
    ]);
    expect(await getUser(env.DB, ALICE.id)).toMatchObject({
      status: "paused",
      pausedUntil: "2026-10-25T06:00:00Z",
    });
    for (const bad of ["/pause 0", "/pause 999", "/pause soon"]) {
      expect(texts(await h.send(bad))[0]).toContain("Use /pause");
    }
    await h.send("/pause");
    expect((await getUser(env.DB, ALICE.id))?.pausedUntil).toBeNull();
    expect(texts(await h.send("/resume"))).toEqual([
      "▶️ Resumed. New matches will reach you again.",
    ]);
    expect(texts(await h.send("/resume"))).toEqual(["You're not paused."]);
  });
});

describe("data rights", () => {
  it("exports the user's data as a JSON file", async () => {
    const h = harness();
    await onboard(h);
    const calls = await h.send("/export");
    expect(calls.map((c) => c.method)).toEqual(["sendDocument"]);
  });

  it("deletes everything after confirmation", async () => {
    const h = harness();
    await onboard(h);
    await h.send("/delete");
    expect(texts(await h.tap("x:n"))).toEqual(["Nothing was deleted."]);
    expect(await getUser(env.DB, ALICE.id)).not.toBeNull();
    await h.send("/delete");
    await h.tap("x:y");
    expect(await getUser(env.DB, ALICE.id)).toBeNull();
    expect(await prefs(ALICE.id)).toBeNull();
  });
});

describe("guards", () => {
  it("limits updates per user per minute and says so once", async () => {
    const h = harness();
    await h.send("/start"); // creates the user; counting starts after
    const limit = CONFIG.bot.updates_per_minute;
    for (let i = 0; i < limit; i++) await h.send("/help");
    const over = [...(await h.send("/help")), ...(await h.send("/help"))];
    expect(texts(over)).toEqual(["Too many requests. Please wait a minute."]);
    h.clock.now = new Date(T0.getTime() + 61_000);
    expect(texts(await h.send("/help"))[0]).toContain("What I can do");
  });

  it("ignores group chats", async () => {
    const h = harness();
    const update = {
      update_id: 1,
      message: {
        message_id: 1,
        date: 0,
        chat: { id: -100, type: "group", title: "g" },
        from: BOB,
        text: "/start",
        entities: [{ type: "bot_command", offset: 0, length: 6 }],
      },
    } as Update;
    expect(await h.run(update)).toEqual([]);
    expect(await getUser(env.DB, BOB.id)).toBeNull();
  });

  it("asks strangers to /start and answers unknown buttons", async () => {
    const h = harness();
    expect(texts(await h.send("hello", BOB))).toEqual(["You're not set up yet. Send /start."]);
    expect(answers(await h.tap("zz:1", BOB))).toEqual(["That menu is out of date."]);
  });
});
