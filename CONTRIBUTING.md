# Contributing

This is a learning-oriented reference implementation. Bug reports, doc fixes, and pattern improvements are all welcome.

## Before you start

- Read [`.specify/memory/constitution.md`](./.specify/memory/constitution.md). Six principles, all non-negotiable. PRs that conflict need a constitution amendment first (see the §Governance block in that file).
- Read [`README.md`](./README.md) for the architectural overview and [`docs/runbook.md`](./docs/runbook.md) for operational details.
- Read [`CLAUDE.md`](./CLAUDE.md) if you're using Claude Code — it has the deploy hazards, gotchas, and skill-alignment expectations.

## Local setup

```bash
git clone <fork>
cd databricks
python -m venv .venv
.venv/bin/pip install -r agent/requirements.txt -r evals/requirements.txt
```

## Running tests + validation

```bash
.venv/bin/python -m pytest agent/tests/ -q     # 18 unit tests
databricks bundle validate --strict -t dev     # YAML schema + interpolation
bash -n scripts/bootstrap-dev.sh               # bash syntax
```

End-to-end is exercised by `./scripts/bootstrap-dev.sh` against a real Databricks workspace; see [`specs/001-doc-intel-10k/quickstart.md`](./specs/001-doc-intel-10k/quickstart.md).

## Working with the spec-kit

The repo uses [Spec-Kit](https://github.com/github/spec-kit) for spec-driven development. New features should flow through the standard cycle:

```
/speckit-specify  →  /speckit-clarify  →  /speckit-plan
                →  /speckit-tasks      →  /speckit-analyze
                →  /speckit-implement
```

Each phase auto-commits. Plans must include a Constitution Check that maps decisions to the relevant principle.

## Working with the Databricks skill bundles

The original implementation was driven by Claude Code's Databricks skill bundles (`databricks-core`, `databricks-dabs`, `databricks-pipelines`, `databricks-jobs`, `databricks-apps`, `databricks-lakebase`, `databricks-model-serving`). Those bundles are distributed by Databricks via the Databricks CLI / Claude Code plugin channel, not vendored in this repo. If you have them installed locally, Claude Code loads them on demand; otherwise consult the official Databricks docs directly:

| Skill | Canonical docs |
|---|---|
| databricks-core (CLI/auth) | https://docs.databricks.com/aws/en/dev-tools/cli/ |
| databricks-dabs | https://docs.databricks.com/aws/en/dev-tools/bundles/ |
| databricks-pipelines (Lakeflow SDP) | https://docs.databricks.com/aws/en/dlt/ |
| databricks-jobs | https://docs.databricks.com/aws/en/jobs/ |
| databricks-apps | https://docs.databricks.com/aws/en/dev-tools/databricks-apps/ |
| databricks-lakebase | https://docs.databricks.com/aws/en/oltp/ |
| databricks-model-serving | https://docs.databricks.com/aws/en/machine-learning/model-serving/ |

When extending the project, link to the Databricks docs (or the local skill file path if you have it) in your PR description so reviewers can verify alignment with current platform behavior. The constitution's principle III ("declarative over imperative") plus the spec-kit cycle below replaces "ignore the docs and guess."

Spec-Kit lives at https://github.com/github/spec-kit; Anthropic's general-purpose skills are at https://github.com/anthropics/skills.

## PR style

- Keep PRs small and per-concern. The history on `main` favors well-scoped commits with clear messages.
- Bundle YAML + Python + docs that change together can ship together.
- Avoid the temptation to "fix" the deploy ordering with `depends_on` between heterogeneous DAB resources or by switching `serving.yml` to UC alias syntax — both have been tried, both don't work in this workspace family. The staged-deploy script is the canonical fix; see [`docs/runbook.md`](./docs/runbook.md).

## Licensing

By contributing, you agree your contributions are licensed under the [MIT License](./LICENSE) of this project.
