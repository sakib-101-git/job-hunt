import { env } from "cloudflare:workers";
import type { Update, User } from "grammy/types";
import { beforeEach, describe, expect, it } from "vitest";

import { CONFIG } from "../src/config";
import { parseAdminIds } from "../src/context";
import { parseAlertData } from "../src/handlers/alerts";
import { ADMIN, ALICE, answers, BOB, buttons, harness, resetDatabase, texts } from "./harness";

beforeEach(resetDatabase);

const C = CONFIG.callbacks;
const WHY = "<b>Why 8.5/10 for “ML &amp; Data Engineer”</b>";

async function user(who: User): Promise<void> {
  await env.DB.prepare("INSERT INTO users (id, chat_id, first_name, locale) VALUES (?, ?, ?, 'en')")
    .bind(who.id, who.id, who.first_name)
    .run();
}

async function seedMatch(matchId: number, owner: User, why: string | null = WHY): Promise<void> {
  const jobId = matchId + 500;
  await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO jobs (id, source, source_job_id, url, canonical_url, fingerprint, title, company,
         posted_at_source, first_seen_at, last_seen_at)
       VALUES (?1, 'bdjobs', ?1, ?2, ?2, ?3, 'ML & Data Engineer', 'Acme <Ltd>', 'api',
         '2026-10-18T00:00:00Z', '2026-10-18T00:00:00Z')`,
    ).bind(jobId, `https://jobs.test/${String(jobId)}`, `fp${String(jobId)}`),
    env.DB.prepare(
      `INSERT INTO matches (id, user_id, job_id, score, breakdown_json, explanation_json,
         gate_results_json, scorer_version, why_html)
       VALUES (?, ?, ?, 8.5, '{}', '{}', '{}', 'scorer.v1', ?)`,
    ).bind(matchId, owner.id, jobId, why),
  ]);
}

async function feedback(matchId: number): Promise<{ kind: string; reason: string | null }[]> {
  const { results } = await env.DB.prepare(
    "SELECT kind, reason FROM feedback WHERE match_id = ? ORDER BY kind",
  )
    .bind(matchId)
    .all<{ kind: string; reason: string | null }>();
  return results;
}

function documentUpdate(from: User, mime: string, size: number): Update {
  return {
    update_id: Math.floor(Math.random() * 1e9),
    message: {
      message_id: 1,
      date: 0,
      chat: { id: from.id, type: "private", first_name: from.first_name },
      from,
      document: { file_id: "FILE-1", file_unique_id: "u1", mime_type: mime, file_size: size },
    },
  };
}

describe("parseAlertData", () => {
  it.each([
    ["wy:12", { code: "wy", matchId: 12, option: undefined }],
    ["rs:12:3", { code: "rs", matchId: 12, option: 3 }],
  ])("parses %s", (data, expected) => {
    expect(parseAlertData(data)).toEqual(expected);
  });

  it.each(["wy", "wy:", "wy:x", "wy:1:2:3", "WY:1", "wy:9999999999999999", "toolong:1"])(
    "rejects %s",
    (data) => {
      expect(parseAlertData(data)).toBeNull();
    },
  );
});

describe("alert buttons", () => {
  beforeEach(async () => {
    await user(ALICE);
    await user(BOB);
    await seedMatch(1, ALICE);
    await seedMatch(2, BOB);
  });

  it("shows the stored explanation, only to the match's owner", async () => {
    const h = harness();
    expect(texts(await h.tap(`${C.why}:1`))).toEqual([WHY]);
    expect(answers(await h.tap(`${C.why}:2`))).toEqual(["That job is no longer available."]);
    expect(answers(await h.tap(`${C.applied}:2`))).toEqual(["That job is no longer available."]);
    expect(await feedback(2)).toEqual([]);
    expect(answers(await h.tap(`${C.why}:999`))).toEqual(["That job is no longer available."]);
  });

  it("says when an old match has no stored explanation", async () => {
    await seedMatch(3, ALICE, null);
    expect(texts(await harness().tap(`${C.why}:3`))[0]).toContain("isn't available any more");
  });

  it("toggles applied and saved, and lists them", async () => {
    const h = harness();
    expect(answers(await h.tap(`${C.applied}:1`))).toEqual(["✅ Marked as applied. /applied lists them."]);
    expect(answers(await h.tap(`${C.applied}:1`))).toEqual(["Removed from applied."]);
    expect(texts(await h.send("/applied"))[0]).toContain("Nothing marked as applied yet");
    await h.tap(`${C.save}:1`);
    expect(await feedback(1)).toEqual([{ kind: "saved", reason: null }]);
    const [list] = texts(await h.send("/saved"));
    expect(list).toContain(
      '1. <a href="https://jobs.test/501">ML &amp; Data Engineer</a> · Acme &lt;Ltd&gt; · 8.5/10',
    );
  });

  it("hides an alert and removes the message", async () => {
    const h = harness();
    const calls = await h.tap(`${C.hide}:1`);
    expect(calls.map((c) => c.method)).toEqual(["answerCallbackQuery", "deleteMessage"]);
    expect(await feedback(1)).toEqual([{ kind: "hidden", reason: null }]);
  });

  it("keeps one thumb per match and records a 👎 reason", async () => {
    const h = harness();
    expect(answers(await h.tap(`${C.thumbs_up}:1`))).toEqual(["👍 Thanks, noted."]);
    const calls = await h.tap(`${C.thumbs_down}:1`);
    expect(await feedback(1)).toEqual([{ kind: "thumbs_down", reason: null }]);
    const reasons = buttons(calls.find((c) => c.method === "sendMessage")).flat();
    expect(reasons.map((b) => b.callback_data)).toEqual(
      CONFIG.bot.feedback_reasons.map((_, i) => `${C.reason}:1:${String(i)}`),
    );
    expect(texts(await h.tap(`${C.reason}:1:0`))).toEqual(["Thanks, noted: Too senior."]);
    expect(await feedback(1)).toEqual([{ kind: "thumbs_down", reason: "too_senior" }]);
    expect(answers(await h.tap(`${C.reason}:1:99`))).toEqual(["That menu is out of date."]);
  });
});

describe("commands", () => {
  beforeEach(async () => {
    await user(ALICE);
    await user(BOB);
    await seedMatch(1, ALICE);
    await seedMatch(2, BOB);
  });

  it("/why explains one of the user's own matches", async () => {
    const h = harness();
    expect(texts(await h.send("/why 1"))).toEqual([WHY]);
    expect(texts(await h.send("/why 2"))).toEqual(["That job is no longer available."]);
    expect(texts(await h.send("/why"))[0]).toContain("Use /why N");
  });

  it("/digest asks the pipeline only when something is waiting", async () => {
    const h = harness();
    expect(texts(await h.send("/digest"))[0]).toContain("No matches are waiting");
    await env.DB.prepare(
      "INSERT INTO notifications (match_id, user_id, job_id, fingerprint) VALUES (1, ?, 501, 'fp501')",
    )
      .bind(ALICE.id)
      .run();
    expect(texts(await h.send("/digest"))).toEqual(["📬 Sending your waiting matches now."]);
    expect(h.dispatched).toEqual([{ event: "send-digest", payload: { user_id: ALICE.id } }]);
  });
});

describe("CV intake", () => {
  beforeEach(() => user(ALICE));

  async function cvFile(): Promise<string | null> {
    const row = await env.DB.prepare("SELECT cv_file_id FROM user_profiles WHERE user_id = ?")
      .bind(ALICE.id)
      .first<{ cv_file_id: string | null }>();
    return row?.cv_file_id ?? null;
  }

  it("accepts a PDF within the limit and asks before parsing", async () => {
    const h = harness();
    expect(texts(await h.run(documentUpdate(ALICE, "image/png", 10)))).toEqual([
      "Please send your CV as a PDF file.",
    ]);
    const big = documentUpdate(ALICE, "application/pdf", CONFIG.bot.cv_max_bytes + 1);
    expect(texts(await h.run(big))[0]).toContain("too big");
    const [consent] = texts(await h.run(documentUpdate(ALICE, "application/pdf", 1000)));
    expect(consent).toContain("Groq");
    expect(h.dispatched).toEqual([]);
    expect(texts(await h.tap("cv:y"))[0]).toContain("Reading your CV");
    expect(h.dispatched).toEqual([{ event: "parse-cv", payload: { user_id: ALICE.id } }]);
    expect(await cvFile()).toBe("FILE-1");
  });

  it("forgets the file when the user says no", async () => {
    const h = harness();
    await h.run(documentUpdate(ALICE, "application/pdf", 1000));
    expect(texts(await h.tap("cv:n"))).toEqual(["OK, your CV wasn't read."]);
    expect(await cvFile()).toBeNull();
    expect(answers(await h.tap("cv:y"))).toEqual(["That menu is out of date."]);
  });

  it("moves setup on and keeps the skills the CV gives", async () => {
    await resetDatabase();
    const h = harness();
    await h.send("/start");
    for (const data of ["o:t:0", "o:n", "o:l:1", "o:w:1", "o:n", "o:j:0", "o:n"]) await h.tap(data);
    await h.run(documentUpdate(ALICE, "application/pdf", 1000));
    const calls = await h.tap("cv:y");
    expect(texts(calls).at(-1)).toContain("6/7 · Match strictness");
    // The pipeline parses the CV before setup ends.
    await env.DB.prepare("UPDATE user_profiles SET skills_json = '[\"python\"]' WHERE user_id = ?")
      .bind(ALICE.id)
      .run();
    for (const data of ["o:m:1", "o:v:0", "o:q", "o:f"]) await h.tap(data);
    const row = await env.DB.prepare("SELECT skills_json FROM user_profiles WHERE user_id = ?")
      .bind(ALICE.id)
      .first<{ skills_json: string }>();
    expect(row?.skills_json).toBe('["python"]');
  });
});

describe("CV summary buttons", () => {
  async function setUp(h: ReturnType<typeof harness>): Promise<void> {
    await h.send("/start");
    for (const data of ["o:t:0", "o:n", "o:l:1", "o:w:1", "o:n", "o:j:0", "o:n", "o:s"]) {
      await h.tap(data);
    }
    for (const data of ["o:m:1", "o:v:0", "o:q", "o:f"]) await h.tap(data);
  }

  it("thanks the user and removes the buttons", async () => {
    const h = harness();
    await setUp(h);
    const calls = await h.tap(C.cv_ok);
    expect(answers(calls)).toEqual(["👍 Thanks! Your profile is saved."]);
    expect(calls.map((c) => c.method)).toContain("editMessageReplyMarkup");
  });

  it("opens the skills editor", async () => {
    const h = harness();
    await setUp(h);
    const calls = await h.tap(C.cv_edit);
    expect(calls.map((c) => c.method)).toContain("editMessageReplyMarkup");
    expect(texts(calls).at(-1)).toContain("Skills");
  });
});

describe("admin", () => {
  beforeEach(async () => {
    await user(ALICE);
    await user(ADMIN);
  });

  it("is invisible to other users", async () => {
    const h = harness();
    expect(texts(await h.send("/admin"))).toEqual(["I didn't get that. /help lists what I can do."]);
    expect(texts(await h.send("/broadcast hi"))).toEqual([
      "I didn't get that. /help lists what I can do.",
    ]);
  });

  it("shows users, queue, LLM use and source health", async () => {
    await env.DB.batch([
      env.DB.prepare(
        "INSERT INTO llm_usage (day, provider, model, requests) VALUES ('2026-10-18', 'groq', 'm', 12)",
      ),
      env.DB.prepare(
        "INSERT INTO source_state (source, last_success_at, consecutive_failures, last_count) VALUES ('bdjobs', '2026-10-18T05:00:00Z', 0, 303), ('remoteok', NULL, 3, NULL)",
      ),
    ]);
    const [text] = texts(await harness().send("/admin", ADMIN));
    expect(text).toContain("👥 Users: active 2");
    expect(text).toContain("📬 Queue: 0 pending, 0 failed");
    expect(text).toContain("🧠 LLM today: groq 12");
    expect(text).toContain("✅ bdjobs: 303 jobs, last ok 2026-10-18T05:00:00Z");
    expect(text).toContain("⚠️ remoteok: 0 jobs, last ok never, 3 failures in a row");
  });

  it("broadcasts through the pipeline after a preview", async () => {
    const h = harness();
    const [preview] = texts(await h.send("/broadcast New <b>feature</b> & fixes", ADMIN));
    expect(preview).toContain("New &lt;b&gt;feature&lt;/b&gt; &amp; fixes");
    await h.tap("bc:y", ADMIN);
    expect(h.dispatched).toEqual([
      {
        event: "broadcast",
        payload: { html: "New &lt;b&gt;feature&lt;/b&gt; &amp; fixes", admin_id: ADMIN.id },
      },
    ]);
    expect(texts(await h.tap("bc:y", ADMIN))).toEqual(["That menu is out of date."]);
    await h.send("/broadcast again", ADMIN);
    expect(texts(await h.tap("bc:n", ADMIN))).toEqual(["Broadcast cancelled."]);
    expect(h.dispatched).toHaveLength(1);
  });

  it("parses the admin id list", () => {
    expect([...parseAdminIds(" 1, 22 ,x,,-3,4.5")]).toEqual([1, 22]);
    expect(parseAdminIds(undefined).size).toBe(0);
  });
});
