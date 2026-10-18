"""The evaluation's numbers, checked against `eval` in config/scoring.yaml, and
written as Markdown (brief §8.3). Gold and silver labels are reported apart."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from jobhunt.config import Config
from jobhunt.eval.evaluate import (
    EvalRun,
    JobResult,
    Oracle,
    PairResult,
    false_positive_rate,
    field_accuracy,
    gate_confusion,
    grounding_failure_rate,
    hard_gate_violations,
    precision_at_k,
    score_histogram,
    skills_scores,
    spearman,
)


@dataclass
class Metrics:
    jobs: int
    jobs_by_llm: int
    pairs: int
    accuracy: dict[str, float | None]
    skills: dict[str, float | None]
    grounding_failure_rate: float | None
    spearman: float | None
    precision_at_k: float | None
    false_positive_rate: float | None


@dataclass
class Report:
    metrics: dict[str, Metrics]  # "all", "gold", "silver"
    violations: list[tuple[PairResult, list[str]]]
    confusion: dict[str, int]
    histogram: dict[int, int]
    failures: list[str] = field(default_factory=list)  # thresholds not met

    @property
    def ok(self) -> bool:
        return not self.failures


def _metrics(jobs: Sequence[JobResult], pairs: Sequence[PairResult], k: int) -> Metrics:
    labelled = [p for p in pairs if p.label is not None]
    return Metrics(
        jobs=len(jobs),
        jobs_by_llm=sum(1 for r in jobs if r.by_llm),
        pairs=len(labelled),
        accuracy=field_accuracy(jobs),
        skills=skills_scores(jobs),
        grounding_failure_rate=grounding_failure_rate(jobs),
        spearman=spearman(
            [float(p.score_or_zero) for p in labelled], [float(p.label or 0) for p in labelled]
        ),
        precision_at_k=precision_at_k(labelled, k),
        false_positive_rate=false_positive_rate(labelled),
    )


def build_report(config: Config, run: EvalRun) -> Report:
    k = config.scoring.eval.precision_k
    metrics = {"all": _metrics(run.jobs, run.pairs, k)}
    for status in ("gold", "silver"):
        jobs = [r for r in run.jobs if r.job.label_status == status]
        pairs = [p for p in run.pairs if p.label_status == status]
        metrics[status] = _metrics(jobs, pairs, k)
    violations = hard_gate_violations(run.pairs, Oracle(config))
    report = Report(
        metrics=metrics,
        violations=violations,
        confusion=dict(gate_confusion(run.jobs)),
        histogram=score_histogram(run.pairs),
    )
    report.failures = _check(config, report)
    return report


def _check(config: Config, report: Report) -> list[str]:
    s = config.scoring.eval
    m = report.metrics["all"]
    failures = []
    if len(report.violations) > s.max_hard_gate_violations:
        failures.append(f"hard-gate violations: {len(report.violations)}")
    for name, minimum in s.min_accuracy.items():
        value = m.accuracy.get(name)
        if value is not None and value < minimum:
            failures.append(f"{name} accuracy {value:.2f} < {minimum}")
    checks = (
        ("skills F1", m.skills["f1"], s.min_skills_f1, True),
        ("Spearman", m.spearman, s.min_spearman, True),
        (f"precision@{s.precision_k}", m.precision_at_k, s.min_precision_at_k, True),
        ("false-positive rate", m.false_positive_rate, s.max_false_positive_rate, False),
        ("grounding failure rate", m.grounding_failure_rate, s.max_grounding_failure_rate, False),
    )
    for label, value, limit, at_least in checks:
        if value is None:
            continue
        if (at_least and value < limit) or (not at_least and value > limit):
            failures.append(f"{label} {value:.2f} {'<' if at_least else '>'} {limit}")
    return failures


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def markdown(report: Report, config: Config) -> str:
    k = config.scoring.eval.precision_k
    lines = ["## Golden-set evaluation", ""]
    lines.append("**Result: passed**" if report.ok else "**Result: FAILED**")
    lines += [f"- {failure}" for failure in report.failures]
    lines += [
        "",
        "| Metric | All | Gold | Silver |",
        "| --- | ---: | ---: | ---: |",
    ]
    splits = [report.metrics[name] for name in ("all", "gold", "silver")]

    def row(name: str, values: Sequence[object]) -> None:
        lines.append(f"| {name} | " + " | ".join(str(v) for v in values) + " |")

    row("jobs (by an LLM)", [f"{m.jobs} ({m.jobs_by_llm})" for m in splits])
    row("labelled pairs", [m.pairs for m in splits])
    for name in splits[0].accuracy:
        row(f"accuracy: {name}", [_fmt(m.accuracy[name]) for m in splits])
    row("skills precision", [_fmt(m.skills["precision"]) for m in splits])
    row("skills recall", [_fmt(m.skills["recall"]) for m in splits])
    row("skills F1", [_fmt(m.skills["f1"]) for m in splits])
    row("grounding failure rate", [_fmt(m.grounding_failure_rate) for m in splits])
    row("Spearman rho (score vs label)", [_fmt(m.spearman) for m in splits])
    row(f"precision@{k}", [_fmt(m.precision_at_k) for m in splits])
    row("false-positive rate at threshold", [_fmt(m.false_positive_rate) for m in splits])
    lines += ["", f"**Hard-gate violations: {len(report.violations)}**"]
    for pair, broken in report.violations[:20]:
        lines.append(
            f"- {pair.profile.id} x {pair.job.id} ({pair.job.listing.title}): {', '.join(broken)}"
        )
    lines += ["", "Job gates against the `eligible` label:", ""]
    lines += [f"- {name}: {count}" for name, count in sorted(report.confusion.items())]
    lines += ["", "Score histogram (matched pairs, all profiles x all jobs):", ""]
    lines.append("| " + " | ".join(str(b) for b in report.histogram) + " |")
    lines.append("|" + " ---: |" * len(report.histogram))
    lines.append("| " + " | ".join(str(c) for c in report.histogram.values()) + " |")
    return "\n".join(lines) + "\n"
