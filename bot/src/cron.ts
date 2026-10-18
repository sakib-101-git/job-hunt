import type { SharedConfig } from "./config";
import { deleteKv, getKv, setKv } from "./db/kv";
import type { Dispatcher } from "./dispatch";
import type { Locale } from "./locale";

// Cron Triggers (brief §4.2, §10.5). Which cron does what is set in
// wrangler.toml's [vars], next to the cron expressions themselves.

export type SendHtml = (chatId: number, html: string) => Promise<void>;

export interface CronDeps {
  db: D1Database;
  config: SharedConfig;
  locale: Locale;
  dispatch: Dispatcher;
  send: SendHtml;
  adminIds: ReadonlySet<number>;
  now: Date;
}

export type HeartbeatState = "ok" | "alerted" | "still_stale" | "recovered";

/**
 * Alert admins once when the last successful pipeline run is older than
 * `max_age_minutes` (or there has never been one), and once more when runs
 * succeed again.
 */
export async function checkHeartbeat(deps: CronDeps): Promise<HeartbeatState> {
  const { db, config, locale: t, now } = deps;
  const { kv_key, alert_kv_key, max_age_minutes } = config.heartbeat;
  const last = await getKv(db, kv_key);
  const open = await getKv(db, alert_kv_key);
  const stale = last === null || now.getTime() - Date.parse(last) > max_age_minutes * 60_000;
  if (stale) {
    if (open !== null) return "still_stale";
    await tellAdmins(deps, last ? t.html("bot.heartbeat.stale", { at: last }) : t.html("bot.heartbeat.never"));
    await setKv(db, alert_kv_key, last ?? "never", now);
    return "alerted";
  }
  if (open !== null) {
    await tellAdmins(deps, t.html("bot.heartbeat.recovered", { at: last }));
    await deleteKv(db, alert_kv_key);
    return "recovered";
  }
  return "ok";
}

async function tellAdmins(deps: CronDeps, html: string): Promise<void> {
  for (const id of deps.adminIds) {
    try {
      await deps.send(id, html);
    } catch (error) {
      console.error(JSON.stringify({ msg: "admin alert failed", error: String(error) }));
    }
  }
}

export interface CronRoutes {
  pipeline: string;
  heartbeat: string;
}

export async function runCron(cron: string, routes: CronRoutes, deps: CronDeps): Promise<void> {
  if (cron === routes.pipeline) {
    await deps.dispatch("run-pipeline", {});
  } else if (cron === routes.heartbeat) {
    const state = await checkHeartbeat(deps);
    console.log(JSON.stringify({ msg: "heartbeat", state }));
  } else {
    console.warn(JSON.stringify({ msg: "unknown cron", cron }));
  }
}
