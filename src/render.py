"""
Jinja2 → LaTeX → PDF pipeline.

Requires pdflatex (texlive). Falls back to weasyprint HTML→PDF if pdflatex missing.
Install on Ubuntu: sudo apt install texlive-xetex texlive-fonts-recommended texlive-latex-extra
"""
import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from jinja2 import Environment, FileSystemLoader
from src.models import TailoredCV, TailoredCoverLetter

log = logging.getLogger("jobhunt.render")

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


# ------------------------------------------------------------------ #
# Jinja2 setup

def _latex_escape(text: str) -> str:
    special = {
        "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
        "\\": r"\textbackslash{}",
    }
    for ch, rep in special.items():
        text = text.replace(ch, rep)
    return text


def _make_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["latex_escape"] = _latex_escape
    return env


# ------------------------------------------------------------------ #
# Public API

def render_cv(tailored: TailoredCV, profile: dict, output_dir: str) -> str:
    env = _make_jinja_env()
    tmpl = env.get_template("cv.tex.j2")
    tex = tmpl.render(tailored=tailored, personal=profile.get("personal", {}))
    slug = _make_slug(tailored)
    return _compile(tex, Path(output_dir) / f"{slug}_cv.pdf")


def render_cover_letter(letter: TailoredCoverLetter, profile: dict, output_dir: str,
                        job_title: str = "", company: str = "") -> str:
    env = _make_jinja_env()
    tmpl = env.get_template("cover_letter.tex.j2")
    tex = tmpl.render(
        letter=letter,
        personal=profile.get("personal", {}),
        job_title=job_title,
        company=company,
    )
    slug = _make_slug_from_strings(job_title, company)
    return _compile(tex, Path(output_dir) / f"{slug}_cover_letter.pdf")


# ------------------------------------------------------------------ #
# Compilation

def _compile(tex_source: str, output_path: Path) -> str:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if shutil.which("pdflatex"):
        return _compile_latex(tex_source, output_path)
    else:
        log.warning("pdflatex not found — falling back to weasyprint HTML rendering")
        return _compile_weasyprint(tex_source, output_path)


def _compile_latex(tex_source: str, output_path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        tex_file = Path(tmp) / "doc.tex"
        tex_file.write_text(tex_source, encoding="utf-8")

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode",
             "-output-directory", tmp, str(tex_file)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            log.error(f"pdflatex failed:\n{result.stdout[-2000:]}")
            raise RuntimeError("pdflatex compilation failed — check logs")

        pdf_src = Path(tmp) / "doc.pdf"
        pdf_src.replace(output_path)

    log.info(f"PDF written: {output_path}")
    return str(output_path)


def _compile_weasyprint(tex_source: str, output_path: Path) -> str:
    from weasyprint import HTML

    # Convert minimal LaTeX to HTML for fallback rendering
    html = _tex_to_html(tex_source)
    html_path = output_path.with_suffix(".html")
    html_path.write_text(html, encoding="utf-8")
    HTML(filename=str(html_path)).write_pdf(str(output_path))
    html_path.unlink(missing_ok=True)
    log.info(f"PDF (weasyprint fallback) written: {output_path}")
    return str(output_path)


def _tex_to_html(tex: str) -> str:
    # Minimal stripping for fallback — proper LaTeX rendering needs pdflatex
    text = re.sub(r"\\[a-zA-Z]+\*?\{([^}]*)\}", r"\1", tex)
    text = re.sub(r"\\[a-zA-Z]+\*?", "", text)
    text = re.sub(r"[{}]", "", text)
    lines = [f"<p>{ln}</p>" for ln in text.splitlines() if ln.strip()]
    return "<html><body>" + "\n".join(lines) + "</body></html>"


# ------------------------------------------------------------------ #
# Helpers

def _make_slug(tailored: TailoredCV) -> str:
    from src.utils import sanitize_filename
    exp = tailored.experience[0] if tailored.experience else None
    company = sanitize_filename(exp.company if exp else "company")
    return f"{company}"


def _make_slug_from_strings(*parts: str) -> str:
    from src.utils import sanitize_filename
    combined = "_".join(p for p in parts if p)
    return sanitize_filename(combined) or "job"


if __name__ == "__main__":
    from src.config import load_config
    from src.utils import setup_logging
    from src.models import TailoredCV, TailoredExperience, TailoredProject, TailoredCoverLetter

    setup_logging("INFO")
    config = load_config()

    sample_cv = TailoredCV(
        headline="Python Backend Engineer | Django & FastAPI | 2 Years",
        selected_summary="Backend engineer with 2 years of Python experience.",
        experience=[
            TailoredExperience(
                company="Sample Co", role="Software Engineer",
                dates="Jan 2023 – Present", location="Dhaka",
                selected_bullets=["Built REST API serving 50k requests/day"],
            )
        ],
        projects=[
            TailoredProject(
                name="Sample Project", url="github.com/sample",
                selected_bullets=["Built chat app with Django Channels"],
            )
        ],
        selected_skills={"languages": ["Python"], "tools": ["Docker"]},
        education=config.profile.get("education", []),
    )

    path = render_cv(sample_cv, config.profile, config.output_dir)
    print(f"Sample CV: {path}")
