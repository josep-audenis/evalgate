# EvalGate
### Open-Source AI Regression Testing & Release Decision Engine

> Source-of-truth product spec.

**Project status:** Proposed
**Primary language:** Python
**Distribution:** Python package + CLI
**License:** MIT
**Target audience:** AI engineers, LLM developers, applied ML teams
**Initial focus:** LLM applications and agentic systems
**Long-term direction:** Framework-agnostic evaluation and release quality infrastructure

---

## 1. Project Vision

### 1.1 The Problem

Modern AI applications are increasingly nondeterministic and difficult to test using traditional software engineering practices.

When developers modify an AI system—by changing its prompt, model, orchestration logic, tools, retrieval pipeline, or configuration—they need to understand whether the new version performs better than the previous one.

Existing evaluation tools can provide quality scores, benchmark results, latency measurements, and other metrics. However, interpreting these results and translating them into reliable deployment decisions remains challenging.

Common problems include:

- Improvements in average quality hiding serious regressions in specific scenarios.
- Developers comparing different experiment runs manually.
- Metrics coming from different tools and incompatible formats.
- Subjective decisions about whether a candidate is acceptable.
- Difficulty incorporating AI-specific quality requirements into CI/CD.
- Evaluation pipelines becoming unnecessarily complex for small projects.
- Inconsistent statistical interpretation of small evaluation datasets.

### 1.2 The Solution

**EvalGate is a lightweight, local-first evaluation comparison and release decision engine.**

It accepts results from existing AI evaluation pipelines, compares an approved baseline against a new candidate, identifies improvements and regressions, applies configurable release policies, and produces an explainable decision.

EvalGate does not initially execute AI applications, host datasets, orchestrate experiments, or require LLM API access.

Instead, it standardizes the results of evaluations already performed by developers.

### 1.3 Core Product Statement

> EvalGate helps AI engineers answer three questions: What changed? What got worse? Is this version acceptable to release?

### 1.4 Design Principles

1. **Framework independence:** Compatible with any evaluation pipeline capable of producing structured results.
2. **Local first:** All core functionality operates offline, with no mandatory cloud account or external services.
3. **Evaluation agnosticism:** Accept deterministic checks, human ratings, model-generated scores, and quantitative measurements.
4. **Explainability:** Every release decision must be traceable to specific measurements and policy conditions.
5. **Minimal integration:** Developers should not need to restructure their applications to use EvalGate.
6. **Reproducibility:** Comparisons must identify the baseline, candidate, dataset, evaluator versions, and policy used.
7. **Modularity:** Evaluation collection, comparison, statistics, policy enforcement, and reporting remain separate components.
8. **Small core, optional extensions:** Advanced integrations should not increase the complexity of basic usage.

---

## 2. Target Users

### Independent AI Developer

An individual experimenting with multiple LLM prompts, model configurations, or agent architectures.

**Desired outcome:** Compare two iterations without manually inspecting every test result.

### AI Engineering Team

A team developing production AI applications with existing testing infrastructure.

**Desired outcome:** Enforce reproducible quality requirements before merging or deploying changes.

### Applied ML Researcher

A researcher comparing multiple model configurations, evaluation strategies, or experimental conditions.

**Desired outcome:** Identify which scenarios improved or regressed and quantify the uncertainty of observed differences.

The product will initially prioritize independent developers and small engineering teams.

---

## 3. Product Boundaries

### EvalGate Will Own

- A standard evaluation result format.
- Collection of results through a Python SDK.
- Import and validation of existing results.
- Comparison of baseline and candidate runs.
- Case-level regression identification.
- Aggregate metric analysis.
- Configurable release policies.
- Decision explanations.
- Reports and CI-friendly output.

### EvalGate Will Not Initially Own

- Executing user-defined LLM pipelines.
- Providing or managing LLM API credentials.
- Running LLM-as-a-judge evaluations.
- Hosting datasets.
- Collecting production traces.
- Building agent orchestration frameworks.
- Managing deployments.
- Providing a hosted observability dashboard.

These boundaries are essential to keeping the initial project small and useful.

---

## 4. Core Workflow

The user evaluates two versions of an application using their existing evaluation pipeline.

Each experiment produces a collection of results describing how the application performed on a defined set of test cases.

EvalGate consumes those results and processes them through five stages.

### Stage 1 — Ingestion

Accept results through:

- Python SDK.
- Standard JSONL files.
- Existing evaluation artifacts imported through adapters.

### Stage 2 — Validation

Verify that the supplied artifacts are structurally valid and comparable.

Required checks include:

- Valid schema and supported format version.
- Unique test case identifiers.
- Compatible dataset identities and versions.
- Compatible evaluator definitions and versions.
- Matching test cases.
- Presence of required metrics and checks.
- Valid metric types and values.

Missing test cases and failed evaluations must never be silently counted as successful results.

### Stage 3 — Comparison

Compare the two experiments at both aggregate and individual test-case levels.

Calculate:

- Metric changes.
- Relative and absolute improvements.
- Newly failing test cases.
- Previously failing cases that improved.
- Regressions in cost and latency.
- Changes in constraint violation frequency.
- Performance differences by scenario category, when metadata is available.

### Stage 4 — Release Policy Evaluation

Apply user-defined requirements to determine whether the candidate satisfies release criteria.

Policies can include:

- Minimum acceptable quality.
- Maximum permitted quality regression.
- Maximum latency or cost regression.
- Zero new violations of critical constraints.
- Mandatory passing checks.
- Explicit protection of previously approved cases.

### Stage 5 — Reporting

Generate an explainable outcome containing:

- Release decision.
- Baseline and candidate identifiers.
- Overall metric comparison.
- Policy violations.
- Regressed cases.
- Improved cases.
- Missing or invalid data diagnostics.
- Optional uncertainty estimates.
- Machine-readable results.

---

## 5. Data Model

EvalGate should define a versioned, framework-independent representation of evaluation results.

The fundamental entities are:

### 5.1 Evaluation Run

A run represents the results of evaluating one version of an application.

Each run has a manifest:

```json
{
  "schema_version": "1.0",
  "run_id": "candidate-v2",
  "application": "my-agent",
  "application_version": "git-sha-or-tag",
  "dataset_id": "regression-suite",
  "dataset_version": "v3",
  "evaluator_version": "rubric-v2"
}
```

Additional metadata, timestamps, model configurations, and experiment identifiers can be included.

### 5.2 Evaluation Case

Each test case has a stable ID and a corresponding result.

```json
{
  "case_id": "scenario-001",
  "status": "ok",
  "scores": {
    "quality": 0.91,
    "correctness": 0.95
  },
  "checks": {
    "schema_valid": true,
    "safety_valid": true
  },
  "metrics": {
    "latency_ms": 4200,
    "cost_usd": 0.014
  },
  "metadata": {
    "category": "complex"
  }
}
```

A case can also explicitly represent an execution failure. Failed executions must be distinguished from missing evaluations or invalid input records.

Raw model inputs, outputs, and evaluation explanations may be stored as optional fields. They should not be required for core comparison functionality.

### 5.3 Run Artifact Structure

Recommended canonical format:

```text
evals/
  baseline/
    run.json
    cases.jsonl
  candidate/
    run.json
    cases.jsonl
```

JSONL is preferred for case results because it is portable, easy to inspect, and suitable for streaming or incremental writes.

### 5.4 Metric Semantics

EvalGate must not assume that higher values are always better.

Each comparable numeric metric has a declared optimization direction:

- `maximize`: Higher values are preferred.
- `minimize`: Lower values are preferred.

Metric definitions may also contain units, supported ranges, and evaluation policy information.

A score of 0.9 is meaningless without knowing what it measures, how it was generated, and whether it is comparable across runs.

---

## 6. Python SDK

The SDK provides the most convenient integration experience for Python developers.

### 6.1 Recording Results

Proposed API:

```python
from evalgate import EvalRun

with EvalRun("candidate-v2", dataset="regression-v3") as run:
    for case in dataset:
        output, latency, cost = execute_agent(case)

        run.record(
            case_id=case["id"],
            scores={
                "quality": evaluate_quality(output)
            },
            checks={
                "schema_valid": validate_schema(output)
            },
            metrics={
                "latency_ms": latency,
                "cost_usd": cost
            }
        )
```

`EvalRun` manages validation, serialization, metadata, and safe writing of artifacts.

It should support explicit recording of pipeline errors rather than requiring user code to invent placeholder scores.

### 6.2 Programmatic Comparison

```python
from evalgate import compare

report = compare(
    baseline="evals/baseline",
    candidate="evals/candidate",
    policy="evalgate.yaml"
)

print(report.summary())

report.to_html("report.html")
report.to_json("report.json")
```

### 6.3 SDK Design Requirements

- Typed public interfaces.
- Minimal required configuration.
- Clear validation errors.
- Safe handling of interrupted writes.
- No automatic external network calls.
- Explicit failure recording.
- No dependence on a particular LLM provider.
- Stable serialization format.
- Public APIs documented with runnable examples.

The SDK should remain a thin interface over the shared core engine rather than implementing separate comparison logic.

---

## 7. Command-Line Interface

The CLI should expose the same core functionality independently of the Python SDK.

### 7.1 Initial Commands

**Initialize configuration**

```bash
evalgate init
```

Creates a documented example policy and project configuration.

**Validate results**

```bash
evalgate validate evals/candidate/
```

Checks artifact structure, completeness, and metric validity.

**Compare experiments**

```bash
evalgate compare \
  evals/baseline/ \
  evals/candidate/ \
  --policy evalgate.yaml
```

**Export a report**

```bash
evalgate compare \
  evals/baseline/ \
  evals/candidate/ \
  --policy evalgate.yaml \
  --report report.html
```

### 7.2 Terminal Experience

The CLI should display:

- Comparison identity.
- Number of matched cases.
- Aggregate differences.
- Notable regressions.
- Failed policies.
- Final status.

The desired style is compact, informative, and appropriate for both interactive terminal use and CI environments.

### 7.3 Exit Codes

Define stable exit codes:

- `0`: Valid comparison, all release conditions passed.
- `1`: Valid comparison, release policy failed.
- `2`: Invalid input, incomparable runs, or an internal execution error.

A policy failure must be distinguished from a tool failure in machine-readable output.

---

## 8. Release Policy Engine

Release policies are one of EvalGate's most important components.

They define the conditions under which a candidate should be accepted.

### 8.1 Configuration Format

Proposed YAML configuration:

```yaml
version: 1

comparison:
  require_same_cases: true
  require_same_dataset: true
  require_same_evaluator: true

metrics:
  quality:
    path: scores.quality
    direction: maximize
    max_mean_regression_abs: 0.02

  latency:
    path: metrics.latency_ms
    direction: minimize
    max_mean_regression_pct: 10

  cost:
    path: metrics.cost_usd
    direction: minimize
    max_mean_regression_pct: 15

checks:
  required:
    - schema_valid
    - safety_valid

  max_new_failures: 0
```

This is the proposed configuration design, not a finalized schema.

### 8.2 Decision Model

EvalGate should implement three primary outcomes:

**PASS**

All required policies are satisfied, and the comparison is valid.

**FAIL**

The comparison is valid, but one or more release conditions are violated.

**INVALID**

A trustworthy release decision cannot be produced because required information is missing, incompatible, or malformed.

The CLI exit codes map to these outcomes.

### 8.3 Policy Semantics

Every policy must have a precise, testable definition.

For example:

- Mean regression refers to the candidate-versus-baseline change in the arithmetic mean across matched cases.
- A newly failed check is one that passed in the baseline and failed for the same case in the candidate.
- A critical-case policy protects specifically identified test cases.
- Relative percentage changes require a meaningful nonzero baseline; otherwise an absolute threshold must be used.

Policy conditions should not be combined into a single weighted quality score by default, because this could hide critical regressions.

### 8.4 Baseline Approval

An automatically generated candidate must never silently become the approved baseline.

Baseline updates should be explicit, reviewable actions.

The comparison report should record the baseline identity and policy configuration so decisions can be reproduced.

---

## 9. Comparison and Statistical Analysis

### 9.1 Initial Comparison Features

The first release will support:

- Matched case comparison using stable identifiers.
- Aggregate arithmetic means.
- Absolute metric differences.
- Relative metric changes where mathematically valid.
- Newly passing and newly failing checks.
- Constraint failure counts.
- Sorted lists of improved and regressed cases.
- Metric distribution summaries.

These operations are deterministic and require no additional model calls.

### 9.2 Advanced Statistics

Later releases may add:

- Paired bootstrap confidence intervals.
- Exact tests for paired binary outcomes.
- Confidence intervals for mean score changes.
- Comparison of repeated stochastic runs.
- Stratified analysis across scenario categories.
- Multiple-comparison corrections where appropriate.
- Sensitivity analysis for release policies.

The statistical methodology must account for the evaluation design.

For example, paired comparisons assume the same cases are evaluated under both versions. Repeated model generations for one case should not automatically be treated as independent test cases.

A statistically inconclusive result must not be presented as proof of improvement or equivalence.

### 9.3 Transparency

Every reported statistical result should include its method, sample size, assumptions, and relevant uncertainty.

EvalGate must avoid presenting small numerical differences as conclusive improvements without appropriate evidence.

---

## 10. Reporting

### 10.1 Terminal Report

A concise summary intended for local development and CI logs.

### 10.2 HTML Report

A standalone HTML file that opens locally without requiring a server.

Recommended content:

1. Release outcome.
2. Baseline versus candidate summary.
3. Metric comparison table.
4. Visual changes in quality, cost, and latency.
5. Regression and improvement tables.
6. Failed policy explanations.
7. Individual case inspection.
8. Run and policy metadata.

The HTML report should be functional without external CDNs or third-party services.

Raw prompts, model responses, or sensitive case data should only be included when explicitly requested.

### 10.3 JSON Report

A stable machine-readable format for automation and integrations.

The report should include decision status, metric deltas, policy evaluations, validation diagnostics, and case-level changes.

---

## 11. Technical Architecture

### 11.1 Suggested Technology Stack

| Component | Technology |
|---|---|
| Core language | Python 3.11+ |
| Package management | uv |
| CLI | Typer |
| Terminal output | Rich |
| Data validation | Pydantic |
| Configuration | YAML |
| Data interchange | JSON / JSONL |
| HTML reporting | Jinja2 + static assets |
| Statistical methods | NumPy / SciPy, when needed |
| Tests | pytest |
| Linting | Ruff |
| Type checking | Pyright or mypy |
| CI | GitHub Actions |
| Package distribution | PyPI |

Avoid introducing database servers, message brokers, or external infrastructure for the initial release.

### 11.2 Suggested Repository Structure

```text
evalgate/
├── src/
│   └── evalgate/
│       ├── __init__.py
│       ├── cli.py
│       ├── sdk/
│       │   └── run.py
│       ├── models/
│       │   ├── run.py
│       │   ├── case.py
│       │   └── policy.py
│       ├── io/
│       │   ├── reader.py
│       │   └── writer.py
│       ├── comparison/
│       │   ├── engine.py
│       │   └── metrics.py
│       ├── policies/
│       │   └── engine.py
│       ├── reporting/
│       │   ├── terminal.py
│       │   ├── json.py
│       │   └── html.py
│       └── integrations/
├── tests/
├── examples/
├── docs/
├── .github/
│   └── workflows/
├── pyproject.toml
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

Directory boundaries are organizational suggestions, not a requirement to create every module before functionality exists.

### 11.3 Architecture Rules

- Core analysis functions should be pure wherever practical.
- File handling must remain separate from comparison calculations.
- Policy definitions should be declarative.
- Reports should consume structured comparison results.
- CLI and SDK must use the same engines.
- Integration adapters should be optional.
- External API calls must never be required for core comparisons.

---

## 12. Development Roadmap

### Phase 0 — Product Validation & Technical Design

**Goal:** Confirm a sufficiently useful, differentiated problem before building the full package.

**Deliverables**

- A concise competitor comparison.
- A finalized minimum viable feature list.
- Canonical JSONL and run manifest specifications.
- Metric and policy semantics.
- A small synthetic evaluation dataset.
- Three realistic example comparisons.

**Validation Scenarios**

1. Candidate improves average quality but introduces a critical regression.
2. Candidate improves quality while increasing cost and latency.
3. Candidate contains missing, duplicate, or incompatible evaluation records.

**Acceptance Criteria**

- Each scenario has a clearly defined expected outcome.
- Data and policy schemas are sufficiently precise to implement independently.
- The intended advantage over existing tools can be demonstrated through a real workflow.
- The MVP does not require an LLM API key, hosted infrastructure, or a database.

**Exit gate:** Freeze the first public schemas and MVP scope.

### Phase 1 — Core Comparison Engine

**Goal:** Build a working offline comparison tool before developing a sophisticated SDK or UI.

**Deliverables**

- Run and case data models.
- JSONL reader and validator.
- Baseline/candidate matching.
- Metric comparison engine.
- Check regression detection.
- YAML policy loader.
- Deterministic policy evaluation.
- Initial CLI commands: `validate` and `compare`.
- Plain terminal summary.
- Structured JSON result export.

**Required Tests**

- Valid identical runs.
- Candidate improvement.
- Candidate quality regression.
- Newly failing critical check.
- Missing case ID.
- Duplicate case ID.
- Missing required metric.
- Incompatible evaluator versions.
- Failed pipeline execution.
- Undefined percentage change due to zero baseline.

**Acceptance Criteria**

A developer can provide two valid run artifacts and receive a correct, explainable PASS or FAIL without writing any Python integration code.

Invalid comparisons do not produce misleading PASS results.

**Milestone:** First functional end-to-end prototype.

### Phase 2 — Python SDK & Developer Experience

**Goal:** Make EvalGate convenient to integrate into existing Python evaluation scripts.

**Deliverables**

- `EvalRun` context manager.
- Case recording API.
- Explicit execution-failure recording.
- Safe JSONL serialization.
- Run manifest generation.
- Programmatic comparison API.
- Typed results and structured exceptions.
- Runnable examples.

**Acceptance Criteria**

A developer can integrate EvalGate into an existing Python evaluation loop with a small amount of code.

The resulting artifacts can be compared through both the SDK and CLI, producing equivalent results.

Interrupted runs are not silently treated as complete experiments.

**Milestone:** Usable alpha Python package.

### Phase 3 — Reporting, CI/CD & First Public Release

**Goal:** Turn the prototype into a polished open-source developer tool.

**Deliverables**

- HTML report generator.
- Improved Rich terminal reporting.
- Stable CLI exit codes.
- JSON report specification.
- GitHub Actions integration example.
- Complete README.
- Installation and quick-start documentation.
- At least two complete demonstration projects.
- Automated linting, type checking, tests, and package builds.
- PyPI publishing workflow.
- Project license and contribution guidelines.

**Acceptance Criteria**

A developer can:

1. Install EvalGate with pip or uv.
2. Generate or import evaluation results.
3. Compare runs.
4. Inspect an HTML report.
5. Fail a CI job when a release policy is violated.

All of this must work locally without a cloud account.

**Milestone:** Public MVP, version `0.1.0`.

### Phase 4 — Advanced Comparison & Statistics

**Goal:** Strengthen EvalGate's analytical capabilities beyond straightforward metric comparison.

**Deliverables**

- Paired bootstrap confidence intervals.
- Methodologically appropriate handling of binary outcomes.
- Repeated-run comparison support.
- Per-category regression analysis.
- Better interpretation of score distributions.
- Configurable uncertainty-based policies.
- Explicit inconclusive statistical outcomes.
- Documented statistical assumptions and limitations.

**Acceptance Criteria**

Statistical results are validated against known reference implementations or manually verified examples.

The tool distinguishes observed numerical changes from conclusions supported by statistical evidence.

Small or insufficient datasets receive appropriate warnings or inconclusive results.

**Milestone:** Statistical analysis release.

### Phase 5 — Interoperability & Framework Integrations

**Goal:** Make adoption easier for developers who already use established evaluation tooling.

**Candidate Integrations**

- Generic CSV and JSON import mapping.
- DeepEval result adapter.
- Langfuse experiment export adapter.
- promptfoo result adapter.
- Additional framework integrations based on demonstrated user demand.

**Implementation Strategy**

Begin with generic import mappings before building dedicated integrations.

Adapters should translate external artifacts into the existing EvalGate schema.

They should not duplicate the core comparison logic or require credentials unless the integration specifically retrieves remote results.

**Acceptance Criteria**

At least two external evaluation result formats can be transformed into EvalGate runs without manually rewriting their records.

Version incompatibilities and unsupported fields are reported clearly.

**Milestone:** Framework interoperability release.

### Phase 6 — Optional Native Evaluators

**Goal:** Allow EvalGate to calculate selected evaluation results itself without compromising its core responsibilities.

**Potential Features**

- Deterministic validation functions.
- JSON schema checks.
- Structured output constraints.
- Custom Python evaluator interfaces.
- Optional LLM-as-a-judge providers.
- Reusable rubric definitions.
- Judge-result caching.
- Pairwise preference evaluation.
- Evaluator versioning and calibration utilities.

**Important Constraint**

Native evaluation must remain optional.

EvalGate should continue supporting fully offline comparisons using precomputed results.

Users should not be forced to use our evaluation mechanisms in order to benefit from the regression analysis and release policy engine.

**Milestone:** Optional evaluation ecosystem.

### Phase 7 — Future Exploration

These features are not commitments and should only be considered if real usage justifies them.

- TypeScript SDK.
- Dedicated GitHub Action.
- Pull request comments.
- Comparison of more than two candidates.
- Local history of experiments.
- Interactive report viewer.
- Automated identification of recurring failure patterns.
- Custom report plugins.
- Integration with experiment tracking systems.

A hosted SaaS platform is explicitly outside the planned roadmap unless user demand provides a compelling reason.

---

## 13. Release Scope

### MVP — Version 0.1.0

The first public version should include:

- Canonical run/result schema.
- Python SDK for recording results.
- File-based ingestion.
- Run validation.
- Case-level and aggregate comparisons.
- Configurable release policies.
- Explainable PASS/FAIL/INVALID decisions.
- CLI interface.
- JSON and standalone HTML reports.
- CI-friendly exit codes.
- Comprehensive tests and examples.

### Explicitly Deferred

- Built-in LLM judge.
- External service integrations.
- Advanced statistical tests.
- Hosted dashboard.
- Remote execution.
- Dataset management.
- Telemetry collection.
- Multi-language SDKs.

The guiding rule is simple: **do not expand the MVP until its primary comparison workflow is complete, reliable, and well documented.**

---

## 14. Quality Requirements

### Correctness

Invalid input must never produce a successful release decision.

Metric and policy semantics must be consistent across CLI and SDK interfaces.

### Determinism

Given identical inputs, configuration, and software version, the comparison engine must produce identical decisions and metric calculations.

Reports may contain generation timestamps, but calculations and policy outcomes must remain reproducible.

### Privacy

No automatic upload of user data, prompts, outputs, or evaluation results.

Optional inclusion of raw case data in reports must be explicit.

### Performance

The tool should handle ordinary evaluation datasets containing thousands of cases comfortably on a developer laptop.

Large datasets should not require loading raw model outputs into memory when they are unnecessary for the comparison.

### Maintainability

Strong type annotations, isolated components, meaningful tests, stable public interfaces, and clear contribution guidelines.

### Documentation

Every public feature should have a working example or documented integration pattern.

---

## 15. Principal Risks

### Risk 1 — Insufficient Differentiation

Existing evaluation frameworks already provide portions of this functionality, including regression detection and CI enforcement.

**Mitigation:** Focus on portable evaluation artifacts, framework-independent comparison, transparent policies, low infrastructure requirements, and strong statistical methodology.

If these benefits do not produce a meaningfully simpler workflow, reconsider the product's scope.

### Risk 2 — Building Another Evaluation Framework

Adding judges, datasets, orchestrators, and experiment runners too early could turn EvalGate into a large competitor to established tools.

**Mitigation:** Maintain a strict separation between generating evaluation results and comparing them.

### Risk 3 — Misleading Statistical Claims

LLM evaluations are noisy, correlated, and sensitive to evaluator design.

**Mitigation:** Use paired comparisons, explicit evaluator versions, documented assumptions, and appropriately qualified uncertainty estimates.

### Risk 4 — Incomplete Experiment Comparisons

Missing or invalid cases could artificially improve average results.

**Mitigation:** Validate completeness and comparability before policy evaluation. Fail closed on missing required results.

### Risk 5 — Integration Friction

Developers may be unwilling to adopt another library simply to compare two experiment runs.

**Mitigation:** Prioritize direct file ingestion, simple schemas, excellent CLI usability, and optional SDK integration.

---

## 16. Definition of Success

The MVP is successful when an external developer can complete the following workflow without assistance:

1. Install EvalGate from PyPI.
2. Understand the expected input format.
3. Export or record two evaluation runs.
4. Define a meaningful release policy.
5. Execute the comparison.
6. Understand exactly why the candidate passed or failed.
7. Integrate the result into CI.

The first release should prioritize proving this complete workflow over maximizing the number of supported features.

### Open-Source Success Indicators

- Independent developers can use the project without modifying its source.
- At least one external evaluation workflow is demonstrated.
- New contributors can understand and extend the architecture.
- Reports identify regressions that would otherwise be obscured by aggregate scores.
- The project maintains a coherent public API and stable data format.
- Documentation makes the purpose and limitations immediately clear.

Stars, downloads, and GitHub attention are secondary indicators—not the primary definition of engineering success.

---

## 17. Development Philosophy

EvalGate should be a small, high-quality engineering tool rather than an ambitious, unfinished platform.

The priority order is:

**Correctness → Developer Experience → Reliability → Interoperability → Advanced Features**

The project should demonstrate disciplined software engineering, sound evaluation methodology, clear abstractions, and thoughtful open-source design.

Its ultimate value is not merely displaying that one model scored higher than another.

**EvalGate should make AI application regressions visible, release requirements explicit, and deployment decisions reproducible.**
