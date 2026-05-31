"""
Skill.jobs scraper.

Skill.jobs is a Nuxt app backed by a JSON API:

  list:   GET https://studio.skill.jobs/api/job_search/?search=<kw>&limit=&offset=
          -> { count, next, previous, results: [ {id, slug, title, type,
               company_info{name,...}, location, division, workplace,
               min_salary, max_salary, isNegotiable, created_at, ...} ] }
  detail: GET https://studio.skill.jobs/api/job_search/<slug>/
          -> { position_summary, job_responsibility, qualification, skills_list, ... }

Human-facing job page: https://skill.jobs/jobs/<slug>

JD is left empty at listing time so main.py only fetches detail for jobs that
survive hard_filter (efficient).
"""
import logging
from src.models import ScrapedJob
from src.scrapers.base import BaseScraper
from src.utils import parse_relative_date, clean_html

log = logging.getLogger("jobhunt.scrapers.skilljobs")

SEARCH_API = "https://studio.skill.jobs/api/job_search/"
DETAIL_PAGE = "https://skill.jobs/jobs/"
RESULTS_PER_PAGE = 30
MAX_KEYWORDS = 5


class SkillJobsScraper(BaseScraper):
    name = "skilljobs"

    def __init__(self, config):
        super().__init__(config)
        self._session.headers.update({
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://skill.jobs/",
            "Origin": "https://skill.jobs",
        })

    def fetch_listings(self) -> list[ScrapedJob]:
        jobs: list[ScrapedJob] = []
        seen_ids: set[str] = set()
        for keyword in self.config.search_keywords[:MAX_KEYWORDS]:
            try:
                resp = self._get(SEARCH_API, params={
                    "search": keyword, "limit": RESULTS_PER_PAGE, "offset": 0,
                })
                results = resp.json().get("results") or []
            except Exception as exc:
                log.warning(f"skilljobs search for '{keyword}' failed: {exc}")
                continue
            for item in results:
                job = self._parse_job(item)
                if job and job.source_job_id not in seen_ids:
                    seen_ids.add(job.source_job_id)
                    jobs.append(job)
        log.info(f"skilljobs: found {len(jobs)} listings")
        return jobs

    def fetch_job_detail(self, url: str) -> str:
        slug = url.rstrip("/").split("/")[-1]
        try:
            data = self._get(SEARCH_API + slug + "/").json()
        except Exception as exc:
            log.warning(f"skilljobs detail fetch failed for {url}: {exc}")
            return ""
        parts = []
        for key in ("position_summary", "job_responsibility", "qualification"):
            val = data.get(key)
            if val:
                parts.append(clean_html(str(val)))
        skills = data.get("skills_list") or []
        if isinstance(skills, list) and skills:
            parts.append("Skills: " + ", ".join(str(x).strip() for x in skills if str(x).strip()))
        return "\n".join(p for p in parts if p).strip()

    # ------------------------------------------------------------------ #

    def _parse_job(self, item: dict) -> ScrapedJob | None:
        try:
            job_id = str(item.get("id") or "").strip()
            slug = (item.get("slug") or "").strip()
            title = (item.get("title") or "").strip()
            if not job_id or not slug or not title:
                return None
            company_info = item.get("company_info") or {}
            company = (company_info.get("name") or item.get("company_name") or "").strip()
            workplace = (item.get("workplace") or "").strip().lower()
            return ScrapedJob(
                source=self.name,
                source_job_id=job_id,
                title=title,
                company=company or "Confidential",
                location=(item.get("location") or item.get("division") or "").strip(),
                url=DETAIL_PAGE + slug,
                posted_date=parse_relative_date(item.get("created_at") or ""),
                job_type=(item.get("type") or "").strip() or None,
                salary_range=self._parse_salary(item),
                is_remote="home" in workplace or "remote" in workplace,
            )
        except Exception as exc:
            log.debug(f"skilljobs card parse error: {exc}")
            return None

    @staticmethod
    def _parse_salary(item: dict) -> str | None:
        lo = item.get("min_salary") or 0
        hi = item.get("max_salary") or 0
        try:
            lo, hi = int(float(lo)), int(float(hi))
        except (TypeError, ValueError):
            lo, hi = 0, 0
        if lo or hi:
            return f"{lo}-{hi}"
        if item.get("isNegotiable"):
            return "Negotiable"
        return None


if __name__ == "__main__":
    import json
    from src.config import load_config
    from src.utils import setup_logging
    setup_logging("INFO")
    cfg = load_config()
    scraper = SkillJobsScraper(cfg)
    jobs = scraper.fetch_listings()
    print(json.dumps([j.model_dump(mode="json") for j in jobs[:5]], indent=2, default=str))
    if jobs:
        print("\n--- detail sample ---")
        print(scraper.fetch_job_detail(jobs[0].url)[:600])
