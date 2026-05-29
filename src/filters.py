"""
Two-stage filtering:
  Stage 1 — hard_filter: free, keyword/location/age checks
  Stage 2 — score_fit: LLM-based 0-10 relevance scoring
"""
import json
import logging
import re
from datetime import datetime, timezone
import anthropic
from src.models import ScrapedJob
from src.utils import truncate_text

log = logging.getLogger("jobhunt.filters")


def hard_filter(job: ScrapedJob, config) -> bool:
    title_lower = job.title.lower()
    location_lower = job.location.lower()
    company_lower = job.company.lower()

    # Must match at least one search keyword
    if not any(kw.lower() in title_lower for kw in config.search_keywords):
        return False

    # Must match at least one target location (or location field is empty)
    if job.location and not any(loc.lower() in location_lower for loc in config.search_locations):
        return False

    # Must not contain excluded title keywords
    if any(ex.lower() in title_lower for ex in config.exclude_keywords):
        return False

    # Must not be an excluded company
    if any(ex.lower() in company_lower for ex in config.exclude_companies):
        return False

    # Must be recent enough
    if job.posted_date:
        age_hours = (datetime.now(timezone.utc) - job.posted_date).total_seconds() / 3600
        if age_hours > config.max_age_hours:
            return False

    return True


def score_fit(job: ScrapedJob, profile: dict, config) -> tuple[int, str]:
    if not job.jd_text or len(job.jd_text.strip()) < 50:
        return 5, "JD too short to evaluate"

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    jd_snippet = truncate_text(job.jd_text, 3000)
    profile_summary = _format_profile_for_scoring(profile)

    system = (
        'You are a job fit evaluator. Given a job description and a candidate profile, '
        'rate the fit from 0-10 and give a one-line reason. Consider: '
        'skills match (do required skills appear in the profile?), experience level match, '
        'location/remote compatibility, and domain relevance. '
        'Respond ONLY in valid JSON with no markdown: {"score": 7, "reason": "..."}'
    )
    user = f"## Job Description\n{jd_snippet}\n\n## Candidate Profile\n{profile_summary}"

    try:
        msg = client.messages.create(
            model=config.scoring_model,
            max_tokens=config.max_tokens_scoring,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = msg.content[0].text.strip()
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

    exp = profile.get("experience", [])
    for e in exp[:2]:
        lines.append(f"Role: {e.get('role')} at {e.get('company')} ({e.get('dates')})")
        for b in e.get("bullets", [])[:3]:
            lines.append("  - " + b.get("text", ""))

    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    from src.config import load_config
    from src.utils import setup_logging
    from src.models import ScrapedJob

    setup_logging("INFO")
    config = load_config()

    sample = ScrapedJob(
        source="test",
        source_job_id="test_001",
        title="Python Backend Developer",
        company="Test Company",
        location="Dhaka, Bangladesh",
        url="https://example.com/job/001",
        jd_text=(sys.argv[1] if len(sys.argv) > 1
                 else "We need a Python developer with Django experience. 2+ years required."),
    )

    passes = hard_filter(sample, config)
    print(f"Hard filter: {'PASS' if passes else 'FAIL'}")
    if passes:
        score, reason = score_fit(sample, config.profile, config)
        print(f"Score: {score}/10 — {reason}")
