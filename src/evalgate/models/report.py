"""Structured comparison results and decision. Reports render these; JSON export dumps them."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from evalgate.models.policy import Direction, Policy
from evalgate.models.run import RunManifest

Status = Literal["PASS", "FAIL", "INVALID"]


class Diagnostic(BaseModel):
    """Validation finding. Any `error` makes the decision INVALID."""

    level: Literal["error", "warning"]
    code: str
    message: str


class ConfidenceInterval(BaseModel):
    """CI for `delta_abs` (paired mean difference).

    Percent bounds divide by the observed baseline mean, i.e. treat it as fixed.
    """

    confidence: float
    low: float
    high: float
    pct_low: float | None  # None when the baseline mean is 0
    pct_high: float | None
    method: Literal["bootstrap", "normal"]
    resamples: int  # 0 for the normal method


class MetricDelta(BaseModel):
    name: str
    path: str
    direction: Direction
    cases: int
    baseline_mean: float
    candidate_mean: float
    delta_abs: float
    delta_pct: float | None  # None when the baseline mean is 0
    ci: ConfidenceInterval


class CaseDelta(BaseModel):
    case_id: str
    metric: str
    baseline: float
    candidate: float
    delta_abs: float


class CheckChange(BaseModel):
    case_id: str
    check: str


class CategoryDelta(BaseModel):
    category: str
    metric: str
    cases: int
    baseline_mean: float
    candidate_mean: float
    delta_abs: float
    delta_pct: float | None  # None when the category's baseline mean is 0
    ci: ConfidenceInterval


class CheckStat(BaseModel):
    """One check over compared cases where it is present in both runs."""

    check: str
    cases: int
    baseline_failures: int
    candidate_failures: int
    newly_failing: int
    newly_passing: int
    # One-sided exact McNemar test that the candidate fails this check more often.
    p_value: float


class RepeatCheck(BaseModel):
    check: str
    # Mean over compared cases of each case's fraction of passing repeats.
    baseline_pass_rate: float
    candidate_pass_rate: float


class RepeatMetric(BaseModel):
    name: str
    # Mean over compared cases of the SD across a case's repeats. None when that run has k=1.
    baseline_sd: float | None
    candidate_sd: float | None


class RepeatSummary(BaseModel):
    """Run-to-run noise. Present only when a run has more than one repeat per case."""

    baseline_repeats: int
    candidate_repeats: int
    checks: list[RepeatCheck]
    metrics: list[RepeatMetric]


class CheckGroup(BaseModel):
    check: str
    failing: int  # == len(cases); kept for symmetry with the other groups
    new: int  # passing in the baseline
    persistent: int  # failing in the baseline too
    no_baseline: int  # the baseline case has no such check
    cases: list[str]


class CategoryGroup(BaseModel):
    category: str
    failing: int  # failing (case, check) pairs
    new: int
    persistent: int
    no_baseline: int
    checks: list[str]
    cases: list[str]  # distinct cases


class CheckCategoryGroup(BaseModel):
    check: str
    category: str
    failing: int
    new: int
    persistent: int
    no_baseline: int
    cases: list[str]


class FailureGroups(BaseModel):
    """Failing checks of compared cases, grouped. Reporting only: no rule reads this.

    Every group has failing == new + persistent + no_baseline. Lists are sorted by failing
    (descending), then key. Categories are the baseline's string `metadata.category`.
    """

    by_check: list[CheckGroup]
    by_category: list[CategoryGroup]
    by_check_category: list[CheckCategoryGroup]
    uncategorized: int  # failing pairs whose case has no (string) baseline category

    def top(self, n: int = 5) -> list[tuple[str, str | None, "CheckGroup | CheckCategoryGroup"]]:
        """The n largest groups as (check, category, group): by check x category, or by check
        alone (category None) when no failing case has a category."""
        if self.by_check_category:
            return [(g.check, g.category, g) for g in self.by_check_category[:n]]
        return [(g.check, None, g) for g in self.by_check[:n]]


class Comparison(BaseModel):
    matched_cases: int
    compared_cases: int  # matched cases with `status: ok` in both runs
    baseline_errors: list[str]
    candidate_errors: list[str]
    metrics: list[MetricDelta]
    regressed_cases: list[CaseDelta]
    improved_cases: list[CaseDelta]
    newly_failing_checks: list[CheckChange]
    newly_passing_checks: list[CheckChange]
    candidate_failing_checks: list[CheckChange]
    check_stats: list[CheckStat]
    categories: list[CategoryDelta]
    repeats: RepeatSummary | None = None
    # Since 0.12; None only in reports written before it.
    failure_groups: FailureGroups | None = None


class RuleResult(BaseModel):
    rule: str
    passed: bool
    message: str
    # Metric rules: whether the whole CI lies on one side of the threshold. None otherwise.
    conclusive: bool | None = None


REPORT_VERSION: Literal["1"] = "1"


class Report(BaseModel):
    # Bumped on breaking changes to this JSON shape; see docs/report-format.md.
    report_version: Literal["1"] = REPORT_VERSION
    evalgate_version: str
    status: Status
    baseline: RunManifest | None = None
    candidate: RunManifest | None = None
    policy: Policy | None = None
    diagnostics: list[Diagnostic] = []
    comparison: Comparison | None = None
    rules: list[RuleResult] = []

    # SDK conveniences; imported lazily so models stay free of I/O and rendering deps.
    def summary(self) -> str:
        """The same text the CLI prints."""
        from io import StringIO

        from rich.console import Console

        from evalgate.reporting.terminal import render

        console = Console(file=StringIO(), width=100, record=True)
        render(self, console)
        return console.export_text()

    def to_json(self, path: str | Path) -> None:
        """Write the same JSON the CLI writes with `--json`."""
        from evalgate.io.writer import write_json_report

        write_json_report(self, Path(path))

    def to_html(self, path: str | Path) -> None:
        """Write the same standalone HTML the CLI writes with `--report`."""
        from evalgate.io.writer import write_html_report

        write_html_report(self, Path(path))

    def to_markdown(self, path: str | Path) -> None:
        """Write the same Markdown summary the CLI writes with `--markdown`."""
        from evalgate.io.writer import write_markdown_report

        write_markdown_report(self, Path(path))
