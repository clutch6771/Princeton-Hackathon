---
paths:
  - "**/*.py"
  - "**/*.ipynb"
---
# Machine Learning (project rule)

> ECC ships no ML rule set. This file fills that gap and extends
> [../ecc/python/coding-style.md](../ecc/python/coding-style.md) and the
> Machine Learning Guidelines in `CLAUDE.md`.

## ECC resources (from the `ecc@ecc` plugin)

- Skills: `mle-workflow`, `pytorch-patterns`, `ml-adoption-playbook`
- Agents: `ecc:mle-reviewer` (ML code review), `ecc:pytorch-build-resolver`

## Practices

- Seed every source of randomness (Python, NumPy, framework) in experiments and tests.
- Keep notebooks for exploration; move reusable logic into `.py` modules with tests.
- Never commit datasets, weights, or checkpoints (see `.gitignore`).
