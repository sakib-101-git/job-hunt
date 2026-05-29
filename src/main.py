import click
from src import db, filters, tailor, notify
from src.utils import load_config, load_profile
from src.scrapers.bdjobs import BDJobsScraper
from src.scrapers.shomvob import ShomvobScraper
from src.scrapers.linkedin_email import LinkedInEmailScraper
from src.scrapers.skilljobs import SkillJobsScraper

SCRAPERS = {
    "bdjobs": BDJobsScraper,
    "shomvob": ShomvobScraper,
    "linkedin_email": LinkedInEmailScraper,
    "skilljobs": SkillJobsScraper,
}


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


@cli.command()
def run():
    """Full pipeline: scrape → filter → score → notify."""
    config = load_config()
    profile = load_profile()
    db.init_db()

    click.echo("Scraping jobs...")
    all_jobs = _scrape(config)
    all_jobs = filters.apply_hard_filters(all_jobs, config)

    new_count = 0
    for job in all_jobs:
        row_id = db.upsert_job(job)
        if row_id:
            new_count += 1
    click.echo(f"  {new_count} new jobs saved")

    click.echo("Scoring jobs...")
    filters.score_jobs(profile, config)

    cfg_notify = config.get("notifications", {})
    if cfg_notify.get("on_new_job"):
        from src import db as _db
        scored = [dict(r) for r in _db.get_unscored_jobs()]
        notify.notify_new_jobs(scored, cfg_notify.get("min_score_to_notify", 70))

    click.echo("Done.")


@cli.command()
def scrape():
    """Scrape only, no scoring."""
    config = load_config()
    db.init_db()
    jobs = _scrape(config)
    jobs = filters.apply_hard_filters(jobs, config)
    count = sum(1 for j in jobs if db.upsert_job(j))
    click.echo(f"{count} new jobs saved")


@cli.command()
@click.argument("job_id", type=int)
def tailor_cmd(job_id):
    """Generate tailored CV + cover letter for JOB_ID."""
    profile = load_profile()
    tailor.tailor_job(job_id, profile)


def _scrape(config: dict) -> list[dict]:
    keywords = config["search"]["keywords"]
    locations = config["search"]["locations"]
    sources = config["search"]["sources"]
    jobs = []
    for source in sources:
        cls = SCRAPERS.get(source)
        if not cls:
            click.echo(f"  unknown source: {source}", err=True)
            continue
        try:
            click.echo(f"  {source}...")
            jobs.extend(cls().scrape(keywords, locations))
        except Exception as exc:
            click.echo(f"  {source} failed: {exc}", err=True)
    return jobs


if __name__ == "__main__":
    cli()
