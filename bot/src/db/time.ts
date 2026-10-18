/** ISO 8601 UTC to the second, the timestamp format every table uses. */
export function isoSeconds(date: Date): string {
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}
