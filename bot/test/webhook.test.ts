import { describe, expect, it } from "vitest";

import { isValidWebhookSecret } from "../src/webhook";

describe("isValidWebhookSecret", () => {
  it("accepts an exact match", () => {
    expect(isValidWebhookSecret("s3cret-value", "s3cret-value")).toBe(true);
  });

  it.each([
    ["different value, same length", "s3cret-valuX", "s3cret-value"],
    ["shorter", "s3cret", "s3cret-value"],
    ["longer", "s3cret-value-and-more", "s3cret-value"],
    ["case differs", "S3CRET-VALUE", "s3cret-value"],
  ])("rejects when %s", (_label, received, expected) => {
    expect(isValidWebhookSecret(received, expected)).toBe(false);
  });

  it("fails closed when the header is missing", () => {
    expect(isValidWebhookSecret(null, "s3cret-value")).toBe(false);
  });

  it.each([undefined, ""])("fails closed when the configured secret is %j", (expected) => {
    expect(isValidWebhookSecret("", expected)).toBe(false);
    expect(isValidWebhookSecret("anything", expected)).toBe(false);
  });
});
