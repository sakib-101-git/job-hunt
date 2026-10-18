// Header Telegram sends with every webhook update when `secret_token` is set.
export const SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token";

const encoder = new TextEncoder();

/**
 * Constant-time check of the webhook secret header. Fails closed: a missing
 * header or an unset/empty configured secret is always rejected.
 */
export function isValidWebhookSecret(
  received: string | null,
  expected: string | undefined,
): boolean {
  if (!expected || received === null) {
    return false;
  }
  const a = encoder.encode(received);
  const b = encoder.encode(expected);
  if (a.byteLength !== b.byteLength) {
    return false;
  }
  return crypto.subtle.timingSafeEqual(a, b);
}
