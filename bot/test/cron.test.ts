import { env } from "cloudflare:workers";

import en from "../../locales/en.json";
import { beforeEach, describe, expect, it } from "vitest";

import { CONFIG } from "../src/config";
import { checkHeartbeat, type CronDeps, runCron } from "../src/cron";
import { setKv } from "../src/db/kv";
import type { DispatchEvent } from "../src/dispatch";
import { EN } from "../src/locale";
import { ALLOWED_UPDATES, setupCalls, webhookUrl } from "../scripts/telegram-setup";

const HB = CONFIG.heartbeat;
const NOW = new Date("2026-10-18T06:00:00Z");
const ROUTES = { pipeline: "*/10 * * * *", heartbeat: "5 * * * *" };

function deps(now: Date = NOW) {
  const sent: { chatId: number; html: string }[] = [];
  const dispatched: DispatchEvent[] = [];
  const value: CronDeps = {
    db: env.DB,
    config: CONFIG,
    locale: EN,
    dispatch: (event) => {
      dispatched.push(event);
      return Promise.resolve(true);
    },
    send: (chatId, html) => {
      sent.push({ chatId, html });
      return Promise.resolve();
    },
    adminIds: new Set([9001, 9002]),
    now,
  };
  return { value, sent, dispatched };
}

beforeEach(async () => {
  await env.DB.prepare("DELETE FROM kv").run();
});

describe("heartbeat", () => {
  it("alerts once when no run has ever finished", async () => {
    const d = deps();
    expect(await checkHeartbeat(d.value)).toBe("alerted");
    expect(d.sent.map((s) => s.chatId)).toEqual([9001, 9002]);
    expect(d.sent[0]?.html).toContain("has not completed a run yet");
    expect(await checkHeartbeat(d.value)).toBe("still_stale");
    expect(d.sent).toHaveLength(2);
  });

  it("is quiet while runs are recent", async () => {
    await setKv(env.DB, HB.kv_key, "2026-10-18T05:30:00Z", NOW);
    const d = deps();
    expect(await checkHeartbeat(d.value)).toBe("ok");
    expect(d.sent).toEqual([]);
  });

  it("alerts when the last run is too old, then reports the recovery", async () => {
    const old = new Date(NOW.getTime() - (HB.max_age_minutes + 1) * 60_000).toISOString();
    await setKv(env.DB, HB.kv_key, old, NOW);
    const d = deps();
    expect(await checkHeartbeat(d.value)).toBe("alerted");
    expect(d.sent[0]?.html).toContain("has stalled");
    await setKv(env.DB, HB.kv_key, "2026-10-18T05:59:00Z", NOW);
    expect(await checkHeartbeat(d.value)).toBe("recovered");
    expect(d.sent.at(-1)?.html).toContain("running again");
    expect(await checkHeartbeat(d.value)).toBe("ok");
  });

  it("keeps going when an admin can't be reached", async () => {
    const d = deps();
    d.value.send = () => Promise.reject(new Error("blocked"));
    expect(await checkHeartbeat(d.value)).toBe("alerted");
  });
});

describe("cron routing", () => {
  it("dispatches a pipeline run on the pipeline cron", async () => {
    const d = deps();
    await runCron(ROUTES.pipeline, ROUTES, d.value);
    expect(d.dispatched).toEqual(["run-pipeline"]);
    expect(d.sent).toEqual([]);
  });

  it("checks the heartbeat on the heartbeat cron, and ignores others", async () => {
    const d = deps();
    await runCron(ROUTES.heartbeat, ROUTES, d.value);
    expect(d.sent).toHaveLength(2);
    await runCron("0 0 * * *", ROUTES, d.value);
    expect(d.dispatched).toEqual([]);
  });

  it("matches the crons in wrangler.toml", () => {
    expect([env.CRON_PIPELINE, env.CRON_HEARTBEAT]).toEqual([ROUTES.pipeline, ROUTES.heartbeat]);
  });
});

describe("webhook setup", () => {
  const input = {
    workerUrl: "https://jobhunt-bot.example.workers.dev",
    webhookPath: CONFIG.bot.webhook_path,
    secret: "s3cret_value-1",
    commands: { start: "Set up", help: "Help" },
  };

  it("sets the webhook with its secret and only the handled updates", () => {
    const [webhook, commands] = setupCalls(input);
    expect(webhook).toEqual({
      method: "setWebhook",
      body: {
        url: `https://jobhunt-bot.example.workers.dev${CONFIG.bot.webhook_path}`,
        secret_token: "s3cret_value-1",
        allowed_updates: ALLOWED_UPDATES,
        drop_pending_updates: false,
      },
    });
    expect(commands?.body).toEqual({
      commands: [
        { command: "start", description: "Set up" },
        { command: "help", description: "Help" },
      ],
    });
  });

  it("rejects secrets Telegram would refuse and plain-http URLs", () => {
    expect(() => setupCalls({ ...input, secret: "has space" })).toThrow(/WEBHOOK_SECRET/);
    expect(() => webhookUrl("http://x.test", "/telegram")).toThrow(/https/);
  });

  it("menus every command the bot answers, each within Telegram's limits", () => {
    const commands = Object.entries(en.bot.commands);
    expect(commands.length).toBeGreaterThan(0);
    for (const [command, description] of commands) {
      expect(command).toMatch(/^[a-z0-9_]{1,32}$/);
      expect(description.length).toBeLessThanOrEqual(256);
    }
  });
});
