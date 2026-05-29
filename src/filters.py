import anthropic
from src.utils import env, load_config
from src import db


def apply_hard_filters(jobs: list[dict], config: dict) -> list[dict]:
    exclude = [kw.lower() for kw in config.get("filters", {}).get("exclude_keywords", [])]
    filtered = []
    for job in jobs:
        text = (job.get("title", "") + " " + job.get("description", "")).lower()
        if not any(kw in text for kw in exclude):
            filtered.append(job)
    return filtered


def score_jobs(profile: dict, config: dict):
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))
    unscored = db.get_unscored_jobs()

    for job in unscored:
        score = _llm_score(client, dict(job), profile)
        db.update_score(job["id"], score)
        print(f"  scored [{score:3d}] {job['title']} @ {job['company']}")


def _llm_score(client: anthropic.Anthropic, job: dict, profile: dict) -> int:
    prompt = f"""You are evaluating job fit. Score 0-100 how well this candidate fits the job.

Candidate summary: {profile.get('summary', '')}
Candidate skills: {profile.get('skills', {})}

Job title: {job['title']}
Company: {job.get('company', 'Unknown')}
Description: {job.get('description', 'No description')[:1000]}

Reply with only an integer between 0 and 100."""

    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=10,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        return int(msg.content[0].text.strip())
    except (ValueError, IndexError):
        return 0
