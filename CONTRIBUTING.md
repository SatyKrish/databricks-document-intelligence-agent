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

## Working with the Databricks skills

When you're touching anything Databricks-specific (DABs, pipelines, jobs, apps, model serving, lakebase), **read the matching skill in `.claude/skills/databricks-*/`** before designing. The skills encode current platform behavior; ignoring them produces stale recommendations. Cite the skill file + section in your PR description so reviewers can verify alignment.

## PR style

- Keep PRs small and per-concern. The history on `main` favors well-scoped commits with clear messages.
- Bundle YAML + Python + docs that change together can ship together.
- Avoid the temptation to "fix" the deploy ordering with `depends_on` between heterogeneous DAB resources or by switching `serving.yml` to UC alias syntax — both have been tried, both don't work in this workspace family. The staged-deploy script is the canonical fix; see [`docs/runbook.md`](./docs/runbook.md).

## Licensing

By contributing, you agree your contributions are licensed under the [MIT License](./LICENSE) of this project.
