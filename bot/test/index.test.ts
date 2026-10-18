import { env, exports } from "cloudflare:workers";
import { describe, expect, it } from "vitest";

import { ROUTES } from "../src/index";
import { SECRET_HEADER } from "../src/webhook";

const BASE = "https://bot.test";

function telegramPost(headers: Record<string, string>): Promise<Response> {
  return exports.default.fetch(`${BASE}${ROUTES.telegram}`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify({ update_id: 1 }),
  });
}

describe("worker routes", () => {
  it("serves the health check", async () => {
    const res = await exports.default.fetch(`${BASE}${ROUTES.health}`);
    expect(res.status).toBe(200);
    expect(await res.text()).toBe("ok");
  });

  it("accepts a webhook with the right secret", async () => {
    const res = await telegramPost({ [SECRET_HEADER]: env.TELEGRAM_WEBHOOK_SECRET });
    expect(res.status).toBe(200);
  });

  it.each([
    ["missing", {}],
    ["wrong", { [SECRET_HEADER]: "not-the-secret" }],
    ["prefix of the real one", { [SECRET_HEADER]: env.TELEGRAM_WEBHOOK_SECRET.slice(0, -1) }],
    ["same-length wrong", { [SECRET_HEADER]: "x".repeat(env.TELEGRAM_WEBHOOK_SECRET.length) }],
  ])("rejects a webhook with a %s secret", async (_label, headers) => {
    const res = await telegramPost(headers);
    expect(res.status).toBe(401);
  });

  it("rejects GET on the webhook route", async () => {
    const res = await exports.default.fetch(`${BASE}${ROUTES.telegram}`);
    expect(res.status).toBe(404);
  });

  it("returns 404 for unknown paths", async () => {
    const res = await exports.default.fetch(`${BASE}/nope`);
    expect(res.status).toBe(404);
  });
});
