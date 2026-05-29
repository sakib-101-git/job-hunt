"""
Main orchestration cycle. Called by cron every 2 hours.
Usage: python -m src.main
"""
import logging
import sys
from src.config import load_config
from src.utils import setup_logging
from src import db
from src.filters import hard_filter, score_fit
from src.tailor import tailor_cv, tailor_cover_letter
from src.render import render_cv, render_cover_letter
from src.notify import send_job_alert
from src.scrapers import ALL_SCRAPERS
from src.models import ScoredJob

log = logging.getLogger("jobhunt.main")


def run_cycle():
    config = load_config()
    setup_logging(config.log_level)
    log.info("=" * 60)
    log.info("JobHunt cycle starting")

    db.init_db(config.db_path)

    # ── Step 1: Scrape ──────────────────────────────────────────────
    all_jobs = []
    for scraper_cls in ALL_SCRAPERS:
        scraper = scraper_cls(config)
        try:
            jobs = scraper.fetch_listings()
            all_jobs.extend(jobs)
            log.info(f"{scraper.name}: fetched {len(jobs)} listings")
        except Exception as exc:
            log.error(f"{scraper.name} failed entirely: {exc}")

    log.info(f"Total scraped: {len(all_jobs)}")

    # ── Step 2: Deduplicate ─────────────────────────────────────────
    new_jobs = []
    for job in all_jobs:
        if not db.job_exists(config.db_path, job.source, job.source_job_id):
            job_id = db.insert_job(config.db_path, job)
            job.db_id = job_id
            new_jobs.append(job)

    log.info(f"New after dedup: {len(new_jobs)}")

    # ── Step 3: Hard filter ─────────────────────────────────────────
    filtered = [j for j in new_jobs if hard_filter(j, config)]
    log.info(f"After hard filter: {len(filtered)}")

    # ── Step 4: Fetch full JD for filtered jobs ─────────────────────
    scraper_map = {cls(config).name: cls(config) for cls in ALL_SCRAPERS}
    for job in filtered:
        if not job.jd_text or len(job.jd_text) < 50:
            scraper = scraper_map.get(job.source)
            if scraper:
                try:
                    job.jd_text = scraper.fetch_job_detail(job.url)
                    log.debug(f"Fetched JD for {job.title} ({len(job.jd_text)} chars)")
                except Exception as exc:
                    log.warning(f"JD fetch failed for {job.url}: {exc}")

    # ── Step 5: LLM scoring ─────────────────────────────────────────
    above_threshold: list[ScoredJob] = []
    for job in filtered:
        try:
            score, reason = score_fit(job, config.profile, config)
            db.update_score(config.db_path, job.db_id, score, reason)
            log.info(f"  [{score:2d}/10] {job.title} @ {job.company} — {reason}")
            if score >= config.min_fit_score:
                scored = ScoredJob(**job.model_dump(), fit_score=score, fit_reason=reason)
                above_threshold.append(scored)
        except Exception as exc:
            log.error(f"Scoring failed for '{job.title}': {exc}")

    log.info(f"Above threshold ({config.min_fit_score}): {len(above_threshold)}")

    # ── Steps 6+7: Tailor CV + notify ───────────────────────────────
    for job in above_threshold:
        cv_path = cl_path = None
        try:
            tailored_cv = tailor_cv(job, config.profile, config)
            tailored_cl = tailor_cover_letter(job, config.profile, config)
            cv_path = render_cv(tailored_cv, config.profile, config.output_dir)
            cl_path = render_cover_letter(
                tailored_cl, config.profile, config.output_dir,
                job_title=job.title, company=job.company,
            )
            db.update_cv_paths(config.db_path, job.db_id, cv_path, cl_path)
            log.info(f"Tailored: {job.title} @ {job.company}")
        except Exception as exc:
            log.error(f"Tailoring/render failed for '{job.title}': {exc}")

        if config.telegram_enabled:
            try:
                sent = send_job_alert(job, cv_path, cl_path, config)
                if sent:
                    db.update_status(config.db_path, job.db_id, "notified")
            except Exception as exc:
                log.error(f"Notification failed for '{job.title}': {exc}")

    # ── Step 8: Summary log ─────────────────────────────────────────
    stats = db.get_stats(config.db_path)
    log.info(f"Cycle complete. DB stats: {stats}")
    log.info("=" * 60)


if __name__ == "__main__":
    run_cycle()
