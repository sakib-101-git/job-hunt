"""
Two-stage filtering:
  Stage 1 — hard_filter: free keyword / remote / location-workability / age checks
  Stage 2 — score_fit: LLM-based 0-10 relevance scoring via Groq (free)

Goal: entry-level (0-1 yr) remote roles, worldwide, doable from Chittagong, Bangladesh.
"""
import json
import logging
import re
from datetime import datetime, timezone
from groq import Groq
from src.models import ScrapedJob
from src.utils import truncate_text

log = logging.getLogger("jobhunt.filters")

# Sources that are 100% remote-first by platform design
GLOBAL_REMOTE_SOURCES = {
    "remotive", "remoteok", "weworkremotely", "jobicy", "workingnomads",
}

# BD-specific sources (English check not applied; English assumed)
BD_SOURCES = {"bdjobs", "shomvob", "skilljobs", "linkedin_email"}

REMOTE_LOCATION_KEYWORDS = {
    "remote", "work from home", "wfh", "anywhere", "worldwide",
    "global", "distributed", "telecommute", "telework", "flexible",
}

# Locations explicitly open to someone working from Bangladesh
WORKABLE_OPEN = {
    "anywhere", "worldwide", "global", "remote", "flexible", "international",
    "fully remote", "work from anywhere", "any location", "earth",
}
WORKABLE_REGIONS = {
    "bangladesh", "asia", "south asia", "apac", "asia pacific", "asia-pacific",
    "india", "pakistan", "sri lanka", "nepal", "middle east", "emea", "mena",
}
# Region tokens that usually exclude a Bangladesh-based candidate
RESTRICTED_REGIONS = {
    "usa", "u.s.", "us only", "us-only", "us based", "us-based", "united states",
    "canada", "uk", "u.k.", "united kingdom", "europe", "eu only", "european",
    "americas", "latam", "latin america", "north america", "australia",
    "new zealand", "germany", "france", "spain", "netherlands", "poland",
    "ireland", "singapore only", "philippines only", "est", "pst", "cst",
    "gmt+1", "gmt-5", "eastern time", "pacific time",
}


def _is_remote_job(job: ScrapedJob) -> bool:
    if job.is_remote or job.source in GLOBAL_REMOTE_SOURCES:
        return True
    loc = job.location.lower()
    jt = (job.job_type or "").lower()
    return any(kw in loc or kw in jt for kw in REMOTE_LOCATION_KEYWORDS)


def _location_workable_from_bd(location: str) -> bool:
    """True if a Chittagong/Bangladesh-based person could plausibly take this remote role."""
    loc = (location or "").strip().lower()
    if not loc:
        return True  # unspecified — give benefit of the doubt
    if any(tok in loc for tok in WORKABLE_OPEN):
        return True
    if any(tok in loc for tok in WORKABLE_REGIONS):
        return True
    if any(tok in loc for tok in RESTRICTED_REGIONS):
        return False
    # Some other specific place we don't recognise as open → reject to be safe
    return False


def _is_english(text: str) -> bool:
    """Proxy check: if >20% of characters are non-ASCII, treat as non-English."""
    if not text:
        return True
    non_ascii = sum(1 for c in text if ord(c) > 127)
    return (non_ascii / len(text)) < 0.20


def hard_filter(job: ScrapedJob, config) -> bool:
    title_lower = job.title.lower()
    company_lower = job.company.lower()

    if not any(kw.lower() in title_lower for kw in config.search_keywords):
        return False

    if config.remote_only:
        if not _is_remote_job(job):
            return False
        if not _location_workable_from_bd(job.location):
            return False
    else:
        location_lower = job.location.lower()
        if job.location and not any(loc.lower() in location_lower for loc in config.search_locations):
            return False

    if any(ex.lower() in title_lower for ex in config.exclude_keywords):
        return False

    if any(ex.lower() in company_lower for ex in config.exclude_companies):
        return False

    if job.posted_date:
        age_hours = (datetime.now(timezone.utc) - job.posted_date).total_seconds() / 3600
        if age_hours > config.max_age_hours:
            return False

    # English check — skip for BD + global-remote platforms (English by design)
    if config.require_english and job.source not in BD_SOURCES and job.source not in GLOBAL_REMOTE_SOURCES:
        if not _is_english(job.title + " " + job.company):
            return False

    return True


def score_fit(job: ScrapedJob, profile: dict, config) -> tuple[int, str]:
    if not job.jd_text or len(job.jd_text.strip()) < 50:
        return 5, "JD too short to evaluate"

    client = Groq(api_key=config.groq_api_key)
    jd_snippet = truncate_text(job.jd_text, 3000)
    profile_summary = _format_profile_for_scoring(profile)

    system = (
        "You are a job fit evaluator for an entry-level candidate: a final-year CS student "
        "in Bangladesh with 0-1 years professional experience, seeking REMOTE roles doable "
        "from home (any timezone — flexible, can work nights). Rate fit 0-10 with a one-line reason.\n"
        "Scoring rules:\n"
        "- Senior / lead / staff / 3+ years required roles: score 0-2.\n"
        "- Mid-level / 2+ years required: score 3-4.\n"
        "- Junior / entry-level / graduate / internship / 0-1 yr roles: score 6-10 if skills match.\n"
        "- ML / data-science / research / paper-writing roles matching the profile: boost score; "
        "paid is preferred but unpaid research is acceptable.\n"
        "- Reward skill overlap (Python, TypeScript/Next.js, React, ML, SQL) and clear remote/worldwide eligibility.\n"
        'Respond ONLY in valid JSON with no markdown: {"score": 7, "reason": "..."}'
    )
    user = f"## Job Description\n{jd_snippet}\n\n## Candidate Profile\n{profile_summary}"

    try:
        resp = client.chat.completions.create(
            model=config.scoring_model,
            max_tokens=config.max_tokens_scoring,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        text = resp.choices[0].message.content.strip()
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL)
        data = json.loads(text)
        return int(data["score"]), str(data.get("reason", ""))
    except Exception as exc:
        log.error(f"score_fit failed for '{job.title}': {exc}")
        return 5, "Scoring error — manual review needed"


def _format_profile_for_scoring(profile: dict) -> str:
    lines = []
    summaries = profile.get("summary_pool", [])
    if summaries:
        lines.append("Summary: " + summaries[0].get("text", ""))

    skills = profile.get("skills", {})
    langs = [l["name"] for l in skills.get("languages", [])]
    tools = skills.get("tools", [])
    if langs:
        lines.append("Languages: " + ", ".join(langs))
    if tools:
        lines.append("Tools: " + ", ".join(str(t) for t in tools))
    ml = skills.get("ml_and_research", [])
    if ml:
        lines.append("ML/Research: " + ", ".join(str(m) for m in ml))

    for e in profile.get("experience", [])[:2]:
        lines.append(f"Role: {e.get('role')} at {e.get('company')} ({e.get('dates')})")
        for b in e.get("bullets", [])[:3]:
            lines.append("  - " + b.get("text", ""))

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from src.config import load_config
    from src.utils import setup_logging

    setup_logging("INFO")
    config = load_config()

    sample = ScrapedJob(
        source="test", source_job_id="test_001",
        title="Junior Python Developer", company="Test Company",
        location="Worldwide", url="https://example.com/job/001", is_remote=True,
        jd_text=(sys.argv[1] if len(sys.argv) > 1
                 else "We need a junior Python developer with Django experience. Entry-level, remote."),
    )

    passes = hard_filter(sample, config)
    print(f"Hard filter: {'PASS' if passes else 'FAIL'}")
    if passes:
        score, reason = score_fit(sample, config.profile, config)
        print(f"Score: {score}/10 — {reason}")
