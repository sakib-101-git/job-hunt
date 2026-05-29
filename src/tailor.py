"""
CV and cover letter tailoring via Groq API (free).

tailor_cv() and tailor_cover_letter() are the two main entry points.
Models used: llama-3.3-70b-versatile (best free model for complex tasks).
"""
import json
import logging
import re
import sys
import yaml
from groq import Groq
from src.models import ScoredJob, TailoredCV, TailoredCoverLetter
from src.utils import truncate_text

log = logging.getLogger("jobhunt.tailor")

CV_SYSTEM = """You are an expert CV tailor. Given a job description and a candidate's master profile,
select and arrange CV content to maximise relevance for this specific role.

RULES:
1. NEVER invent experience, skills, or achievements not in the master profile.
2. SELECT the most relevant bullets from each experience entry. Pick 3-5 per role.
   Prioritise bullets whose tags match the JD's required skills.
3. REORDER bullets within each role so the most relevant ones come first.
4. SELECT the best summary from summary_pool based on JD match.
5. Write a one-line HEADLINE like "Python Backend Engineer | Django & FastAPI | 3 Years"
   using only real skills from the profile.
6. LIGHTLY REPHRASE bullets to echo JD keywords where truthful.
   Example: if JD says "RESTful APIs" and bullet says "Built REST API", change to
   "Designed and built RESTful APIs" — but never fabricate.
7. Select 2-3 most relevant projects.
8. For skills section, prioritise skills mentioned in the JD.
9. Keep the final CV content to what fits on ONE PAGE when rendered.

Respond ONLY in this exact JSON structure with no markdown fences:
{
  "headline": "...",
  "selected_summary": "...",
  "experience": [
    {
      "company": "...",
      "role": "...",
      "dates": "...",
      "location": "...",
      "selected_bullets": ["bullet 1", "bullet 2"]
    }
  ],
  "projects": [
    {
      "name": "...",
      "url": "...",
      "selected_bullets": ["..."]
    }
  ],
  "selected_skills": {
    "languages": ["Python", "JavaScript"],
    "frameworks": ["Django", "FastAPI"],
    "tools": ["Docker", "AWS"],
    "databases": ["PostgreSQL"]
  },
  "education": [],
  "certifications": []
}"""

CL_SYSTEM = """Write a concise, professional cover letter for this job application.

RULES:
1. 4-6 sentences in the body. No fluff.
2. Open with which role you're applying for and one compelling reason you're a fit.
3. Middle: 2-3 specific achievements from the profile that directly relate to JD requirements.
   Use numbers/metrics from the profile where available.
4. Close with enthusiasm and availability.
5. Tone: confident, professional, not desperate. No "I would be honoured" or "I humbly request".
6. NEVER invent achievements not in the profile.

Respond ONLY in valid JSON with no markdown fences:
{
  "greeting": "Dear Hiring Manager,",
  "body": "...",
  "closing": "Best regards,\\n[Name]"
}"""


def tailor_cv(job: ScoredJob, profile: dict, config) -> TailoredCV:
    client = Groq(api_key=config.groq_api_key)
    jd_snippet = truncate_text(job.jd_text, 3000)
    profile_yaml = yaml.dump(profile, allow_unicode=True)

    user = f"## Job Description\n{jd_snippet}\n\n## Master Profile\n{profile_yaml}"
    raw = _call(client, config.llm_model, config.max_tokens_tailoring, CV_SYSTEM, user)
    data = _parse_json(raw, client, config.llm_model)

    if not data.get("education"):
        data["education"] = profile.get("education", [])
    if not data.get("certifications"):
        data["certifications"] = profile.get("certifications", [])

    return TailoredCV(job_url=job.url, **data)


def tailor_cover_letter(job: ScoredJob, profile: dict, config) -> TailoredCoverLetter:
    client = Groq(api_key=config.groq_api_key)
    jd_snippet = truncate_text(job.jd_text, 2000)
    name = profile.get("personal", {}).get("name", "")
    bullets_sample = _extract_top_bullets(profile, 6)

    user = (
        f"Candidate name: {name}\n"
        f"Top achievements:\n{bullets_sample}\n\n"
        f"## Job Title\n{job.title} at {job.company}\n\n"
        f"## Job Description\n{jd_snippet}"
    )
    raw = _call(client, config.llm_model, 600, CL_SYSTEM, user)
    data = _parse_json(raw, client, config.llm_model)
    return TailoredCoverLetter(**data)


# ------------------------------------------------------------------ #

def _call(client: Groq, model: str, max_tokens: int, system: str, user: str) -> str:
    resp = client.chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


def _parse_json(text: str, client: Groq, model: str) -> dict:
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.DOTALL).strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        log.warning("JSON parse failed — retrying with explicit instruction")
        resp = client.chat.completions.create(
            model=model,
            max_tokens=200,
            messages=[
                {"role": "user", "content": "Reformat the following as valid JSON only, no markdown:\n\n" + text},
            ],
        )
        clean2 = re.sub(r"^```(?:json)?\s*|\s*```$", "",
                        resp.choices[0].message.content.strip(), flags=re.DOTALL)
        return json.loads(clean2)


def _extract_top_bullets(profile: dict, n: int) -> str:
    bullets = []
    for exp in profile.get("experience", []):
        for b in exp.get("bullets", []):
            bullets.append("- " + b.get("text", ""))
    return "\n".join(bullets[:n])


if __name__ == "__main__":
    from src.config import load_config
    from src.utils import setup_logging
    from src.models import ScoredJob

    setup_logging("INFO")
    config = load_config()
    jd = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Python Developer needed. Must know Django, PostgreSQL, REST APIs. 2+ years experience."
    )
    job = ScoredJob(
        source="test", source_job_id="test_001", title="Python Developer",
        company="Test Co", location="Dhaka", url="https://example.com",
        jd_text=jd, fit_score=8, fit_reason="test",
    )
    cv = tailor_cv(job, config.profile, config)
    print(cv.model_dump_json(indent=2))
