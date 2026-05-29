import anthropic
from jinja2 import Environment, FileSystemLoader
from pathlib import Path
from src.utils import env, ROOT
from src import db, render


TEMPLATES_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "output"


def tailor_job(job_id: int, profile: dict):
    job = db.get_job(job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    job = dict(job)
    client = anthropic.Anthropic(api_key=env("ANTHROPIC_API_KEY"))

    tailored_summary = _tailor_summary(client, job, profile)
    cover_letter_body = _write_cover_letter(client, job, profile)

    OUTPUT_DIR.mkdir(exist_ok=True)
    slug = f"job_{job_id}"

    cv_tex = _render_template("cv.tex.j2", profile=profile, tailored_summary=tailored_summary)
    cl_tex = _render_template("cover_letter.tex.j2", profile=profile, job=job, cover_letter_body=cover_letter_body)

    cv_path = render.compile_pdf(cv_tex, OUTPUT_DIR / f"{slug}_cv.pdf")
    cl_path = render.compile_pdf(cl_tex, OUTPUT_DIR / f"{slug}_cover_letter.pdf")

    db.set_job_paths(job_id, str(cv_path), str(cl_path))
    print(f"CV:    {cv_path}")
    print(f"Cover: {cl_path}")


def _tailor_summary(client: anthropic.Anthropic, job: dict, profile: dict) -> str:
    prompt = f"""Rewrite this candidate summary to be tailored for the job below.
Keep it under 3 sentences. Return only the summary text.

Original summary: {profile.get('summary', '')}

Job title: {job['title']}
Description: {job.get('description', '')[:800]}"""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _write_cover_letter(client: anthropic.Anthropic, job: dict, profile: dict) -> str:
    prompt = f"""Write a concise, professional cover letter body (3 short paragraphs) for:

Candidate: {profile['personal']['name']}
Summary: {profile.get('summary', '')}

Job: {job['title']} at {job.get('company', 'the company')}
Description: {job.get('description', '')[:800]}

Return only the letter body, no salutation or sign-off."""

    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def _render_template(name: str, **ctx) -> str:
    env_j2 = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    tmpl = env_j2.get_template(name)
    return tmpl.render(**ctx)
