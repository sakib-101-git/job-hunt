import { describe, expect, it } from "vitest";

import { CONFIG } from "../src/config";
import { EN } from "../src/locale";
import {
  actionData,
  apply,
  type Draft,
  newDraft,
  parseAction,
  STEPS,
} from "../src/onboarding/draft";
import { parseSkills, skillIndex } from "../src/onboarding/skills";
import { render } from "../src/onboarding/view";

function draft(changes: Partial<Draft> = {}): Draft {
  return { ...newDraft(CONFIG), ...changes };
}

function step(d: Draft, data: string) {
  const action = parseAction(data);
  if (!action) throw new Error(`bad action ${data}`);
  return apply(d, action, CONFIG);
}

function ok(d: Draft, data: string): Draft {
  const outcome = step(d, data);
  if (outcome.kind !== "ok") throw new Error(`${data} gave ${outcome.kind}`);
  return outcome.draft;
}

describe("parseAction", () => {
  it.each([
    ["o:n", { code: "n", args: [] }],
    ["o:t:3", { code: "t", args: [3] }],
    ["o:d:0:4", { code: "d", args: [0, 4] }],
    ["o:q", { code: "q", args: [] }],
  ])("parses %s", (data, expected) => {
    expect(parseAction(data)).toEqual(expected);
  });

  it.each(["o:", "o:z", "o:t:x", "o:t:1:2:3", "o:t:1234", "x:t:1", "o:tt:1", "o:t:-1"])(
    "rejects %s",
    (data) => {
      expect(parseAction(data)).toBeNull();
    },
  );
});

describe("apply", () => {
  it("walks the onboarding steps in order", () => {
    let d = draft();
    expect(step(d, "o:n")).toEqual({ kind: "pick_one" });
    d = ok(d, "o:t:0");
    expect(d.tracks).toEqual(["ai_ml"]);
    d = ok(d, "o:n");
    expect(d.step).toBe("level");
    d = ok(d, "o:l:1");
    expect([d.level, d.step]).toEqual(["fresh_grad", "where"]);
    d = ok(ok(d, "o:w:0"), "o:n");
    expect(d.step).toBe("cities");
    d = ok(ok(d, "o:c:0"), "o:n");
    expect([d.cities, d.step]).toEqual([["dhaka"], "job_types"]);
    expect(step(d, "o:n")).toEqual({ kind: "pick_one" });
    d = ok(ok(d, "o:j:0"), "o:n");
    expect(d.step).toBe("skills");
    d = ok(d, "o:s");
    expect(d.step).toBe("score");
    d = ok(d, "o:m:2");
    expect([d.minScore, d.step]).toEqual([7, "delivery"]);
    d = ok(d, "o:v:1");
    expect([d.delivery, d.step]).toEqual(["digest", "quiet"]);
    d = ok(d, "o:q:0");
    expect([d.quiet, d.step]).toEqual([{ start: "22:00", end: "08:00" }, "summary"]);
    expect(step(d, "o:f")).toEqual({ kind: "finish", draft: d });
  });

  it("skips preferred cities for a remote-only user and clears them", () => {
    let d = draft({ step: "where", locations: ["bangladesh"], cities: ["dhaka"] });
    d = ok(d, "o:w:0"); // Bangladesh off
    d = ok(d, "o:w:1"); // remote on
    expect(d.cities).toEqual([]);
    expect(ok(d, "o:n").step).toBe("job_types");
  });

  it("refines a track but keeps at least one of its domains", () => {
    let d = ok(draft(), "o:t:0");
    d = ok(d, "o:r");
    expect(d.step).toBe("refine");
    d = ok(d, "o:d:0:1");
    expect(d.deselected).toEqual({ ai_ml: ["emerging-ai"] });
    d = ok(d, "o:d:0:1");
    expect(d.deselected).toEqual({});
    const domains = CONFIG.tracks[0]?.primary_domains ?? [];
    for (let i = 1; i < domains.length; i++) d = ok(d, `o:d:0:${String(i)}`);
    expect(step(d, "o:d:0:0")).toEqual({ kind: "pick_one" });
    // Unticking the track drops its refinements.
    d = ok(ok(d, "o:b"), "o:t:0");
    expect([d.tracks, d.deselected]).toEqual([[], {}]);
  });

  it("refuses refine with no track chosen", () => {
    expect(step(draft(), "o:r")).toEqual({ kind: "pick_one" });
  });

  it("returns to the summary in /settings mode", () => {
    const d = draft({
      mode: "settings",
      step: "level",
      tracks: ["ai_ml"],
      level: "junior",
      locations: ["remote"],
      jobTypes: ["full_time"],
    });
    expect(ok(d, "o:l:0").step).toBe("summary");
    expect(ok({ ...d, step: "job_types" }, "o:n").step).toBe("summary");
    expect(ok({ ...d, step: "score" }, "o:m:0").step).toBe("delivery");
  });

  it("calls a press from an older menu stale", () => {
    expect(step(draft(), "o:l:0")).toEqual({ kind: "stale" });
    expect(step(draft({ step: "level" }), "o:n")).toEqual({ kind: "stale" });
    expect(step(draft(), "o:t:99")).toEqual({ kind: "stale" });
    expect(step(draft({ step: "summary" }), "o:f")).toEqual({ kind: "stale" }); // incomplete
  });
});

describe("render", () => {
  const full = draft({
    tracks: CONFIG.tracks.map((t) => t.id),
    level: "fresh_grad",
    locations: ["bangladesh", "remote"],
    jobTypes: ["full_time"],
    skills: ["python"],
    unknownSkills: ["foo & bar"],
  });

  it.each(STEPS)("keeps %s within Telegram's limits", (s) => {
    const view = render({ ...full, step: s }, CONFIG, EN);
    expect(view.text.length).toBeLessThanOrEqual(4096);
    const all = view.keyboard.flat();
    expect(all.length).toBeLessThanOrEqual(100);
    for (const b of all) {
      expect("callback_data" in b ? new TextEncoder().encode(b.callback_data).length : 0).toBeLessThanOrEqual(64);
    }
  });

  it("marks selections and escapes values in HTML", () => {
    const view = render({ ...full, step: "summary" }, CONFIG, EN);
    expect(view.text).toContain("AI &amp; Machine Learning");
    expect(view.text).toContain("Python, foo &amp; bar");
    const tracks = render({ ...full, step: "tracks" }, CONFIG, EN);
    expect(tracks.keyboard[0]?.[0]?.text).toBe("✅ 🤖 AI & Machine Learning");
  });

  it("offers Start for onboarding and Save for /settings", () => {
    const last = (d: Draft) => render(d, CONFIG, EN).keyboard.at(-1)?.[0];
    expect(last({ ...full, step: "summary" })).toEqual({
      text: "🚀 Start matching",
      callback_data: actionData("f"),
    });
    expect(last({ ...full, step: "summary", mode: "settings" })?.text).toBe("💾 Save");
  });
});

describe("parseSkills", () => {
  const index = skillIndex(CONFIG.skills);

  it("maps names and aliases to ids and keeps unknown ones", () => {
    expect(parseSkills(" python3, SQL;React.js\nPostgres , Quantum  Knitting,sql", index, CONFIG.bot)).toEqual({
      ids: ["python", "sql", "react", "postgresql"],
      unknown: ["quantum knitting"],
    });
  });

  it("drops overlong entries and stops at the limit", () => {
    const long = "x".repeat(CONFIG.bot.max_skill_length + 1);
    expect(parseSkills(`${long}, python`, index, CONFIG.bot).ids).toEqual(["python"]);
    const many = Array.from({ length: CONFIG.bot.max_skills + 5 }, (_, i) => `skill${String(i)}`);
    expect(parseSkills(many.join(","), index, CONFIG.bot).unknown).toHaveLength(CONFIG.bot.max_skills);
  });
});
