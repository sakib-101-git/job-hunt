import type { SharedConfig } from "./config";

// The pipeline does the heavy work (scoring, CV parsing, sending alerts); the
// bot asks for it with a GitHub repository_dispatch event.

export type DispatchEvent = "backfill-user" | "parse-cv" | "send-digest" | "broadcast";

export type Dispatcher = (event: DispatchEvent, payload: Record<string, unknown>) => Promise<boolean>;

const API_VERSION = "2022-11-28";

export function githubDispatcher(
  github: SharedConfig["github"],
  token: string | undefined,
  fetcher: typeof fetch = fetch,
): Dispatcher {
  return async (event, payload) => {
    if (!token) {
      console.warn(JSON.stringify({ msg: "dispatch skipped: no token", event }));
      return false;
    }
    const response = await fetcher(`${github.api_base_url}/repos/${github.repository}/dispatches`, {
      method: "POST",
      headers: {
        accept: "application/vnd.github+json",
        authorization: `Bearer ${token}`,
        "content-type": "application/json",
        "user-agent": "jobhunt-bot",
        "x-github-api-version": API_VERSION,
      },
      body: JSON.stringify({ event_type: event, client_payload: payload }),
    });
    if (!response.ok) {
      console.error(JSON.stringify({ msg: "dispatch failed", event, status: response.status }));
    }
    return response.ok;
  };
}
