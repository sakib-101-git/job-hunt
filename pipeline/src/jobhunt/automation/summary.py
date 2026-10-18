"""A run's summary as Markdown, for `$GITHUB_STEP_SUMMARY` (public in the run
page, so counts and source names only, never user data)."""

from jobhunt.automation.pipeline import RunSummary


def markdown(summary: RunSummary) -> str:
    status = "succeeded" if summary.ok else "failed"
    lines = [
        f"## Pipeline run: {summary.trigger}, {status}",
        "",
        "| Stage | Time | Result |",
        "| --- | ---: | --- |",
    ]
    for stage in summary.stages:
        if stage.error:
            result = f"**failed**: {_cell(stage.error)}"
        else:
            result = ", ".join(f"{_cell(k)}: {v}" for k, v in sorted(stage.counts.items()))
        lines.append(f"| {stage.name} | {stage.seconds:.1f} s | {result or '-'} |")
    details = [(s.name, d) for s in summary.stages for d in s.details]
    if details:
        lines += ["", "<details><summary>Details</summary>", ""]
        lines += [f"- {name}: {_cell(detail)}" for name, detail in details]
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")
