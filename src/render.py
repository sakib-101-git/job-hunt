import subprocess
import tempfile
from pathlib import Path


def compile_pdf(tex_source: str, output_path: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        tex_file = Path(tmp) / "doc.tex"
        tex_file.write_text(tex_source, encoding="utf-8")

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmp, str(tex_file)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdflatex failed:\n{result.stdout}\n{result.stderr}")

        pdf_src = Path(tmp) / "doc.pdf"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_src.replace(output_path)

    return output_path
