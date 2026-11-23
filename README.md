# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-cbe96f01` (track: `academic-review`).

This repository holds the complete skill package for the challenge: the skill
definition, its executable pipeline and all supporting documents. No precomputed
answers, private data or credentials are included.

## Skill

- Name: `citation-and-fact-verification`
- Summary: >-

## Contents

| File |
|------|
| `SKILL.md` |
| `assets/policy.json` |
| `examples/demo-case/case.json` |
| `examples/demo-case/manuscript.md` |
| `examples/demo-case/sources.json` |
| `examples/quickstart.md` |
| `references/evidence-locators.md` |
| `references/verdicts-and-severity.md` |
| `references/verification-protocol.md` |
| `scripts/run_skill.py` |
| `scripts/sk_entry.py` |
| `README.md` |

## How to run

```bash
python scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

Two passes are intended: the first writes `/app/work/agent_tasks.json` (evidence
skeleton) and `/app/output/result.json`; after the agent writes
`/app/work/agent_judgement.json`, the second pass merges and validates the result.
See `SKILL.md` and `references/two-phase-workflow.md`.

## Notes

- Pure Python standard library; no network access required.
- Every claim is anchored to paragraph-level evidence; unknown items are reported
  in `uncertainty` instead of being fabricated.
