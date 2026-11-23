# Runtime contract

Bohrium Playground Period 4 submission — challenge `challenge-cbe96f01` (track: `academic-review`).

This repository contains the complete skill package for this challenge: the skill
definition, the executable entry point and every supporting script needed to run it.
No precomputed answers, no private data and no credentials are included.

## Skill

- Name: `citation-and-fact-verification`
- Summary: Use when a manuscript's citations and factual statements must be verified, requiring claim extraction, numeric and author-year marker resolution, claim-to-source lexical support checks, reference metadata and duplicate audits, numeric consistency checks and explicit cannot-verify items without online lookup.

## Contents

| File |
|------|
| `SKILL.md` |
| `scripts/run_skill.py` |
| `README.md` |

## How to run

```bash
python scripts/run_skill.py --case /input/case.json --output /app/output/result.json
```

The entry point is `SKILL.md`; input and output contracts, error handling and a
minimal usage example are documented there.

## Notes

- Pure Python standard library; no network access required.
- The result JSON keeps the input case id, the main results, per-item evidence,
  a run status and an uncertainty section.
