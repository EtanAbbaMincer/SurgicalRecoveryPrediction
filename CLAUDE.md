# SurgicalRecoveryPrediction

Surgical recovery prediction on **All of Us Research Program** data, built on the
**Verily Workbench**.

## Read this first

**Per the All of Us DUA, only plain code may be committed to this repo. No data at all.**
`.gitignore` is deny-by-default. The non-obvious consequence: **notebook outputs must be
stripped before committing**, because saved cell outputs embed rendered tables and figures —
that is data. Compliance obligation, outranks every convenience.

This is enforced mechanically by two redundant layers: the `nbstripout` clean filter
(`.gitattributes`) and a versioned `.githooks/pre-commit` guard. **Both need one-time setup per
clone** — `.git/config` is not cloned, so in a new workspace the filter is silently absent:

```
pip install -r requirements.txt && nbstripout --install --attributes .gitattributes
git config core.hooksPath .githooks
```

Never use `git commit --no-verify` here. Details in `allofus-data-governance`.

Two skills govern this repo. Consult them rather than relying on this summary:

| Skill | Scope | Owns |
|---|---|---|
| `allofus-data-governance` (`.claude/skills/`, project-local) | All of Us / Verily specifics | DUA commit rules, `.gitignore`, output stripping, small-cell suppression, OMOP/BigQuery access |
| `pipeline-manager` (`~/.claude/skills/`, user-global) | Portable layout conventions | Task-scoped directories, per-variable notebooks, `*_figures/`, `_cache/`, caching strategy |

Where they conflict, **`allofus-data-governance` wins** — it carries the compliance rules;
`pipeline-manager` is deliberately domain-neutral and loads for unrelated projects too.

## Layout

Task-scoped directories at the repo root, one notebook per variable, no filename prefixes:

```
compliance/
  bmi.ipynb
  bmi_figures/            <- git-ignored
  blood_pressure.ipynb
  blood_pressure_figures/
  _cache/                 <- git-ignored
```

Invoke `pipeline-manager` before creating a directory or notebook, or when deciding where a new
file belongs.

## Environment

- **Analysis runs in the Verily Workbench cloud workspace**, not on this laptop. Assume no local
  data files.
- **Row-level data never leaves the workspace.** See `allofus-data-governance` for the egress and
  dissemination rules, including small-cell suppression.
- **Data access is BigQuery over the OMOP CDM**, via the workspace-provided dataset variable
  (never a hard-coded project ID), with the **CDR / data-release version pinned** in output.
- **Cache aggressively to minimize BigQuery.** Highest leverage: materialize a shared cohort
  extract once per CDR release and have per-variable notebooks read that instead of the full CDR.
  Full tiered strategy in `pipeline-manager`'s `references/layout.md`.

## Conventions

- Keep cohort definitions (concept sets, inclusion/exclusion logic) in version-controlled code,
  not only in Workbench UI state, so the cohort is reproducible.
- Cohort construction → feature extraction → modeling → evaluation organize **sections within**
  a notebook, never separate files or directories.

## Project goal

Predict recovery outcomes after surgery from All of Us EHR/survey data. Outcome definition,
cohort inclusion criteria, prediction window, and modeling approach are **TBD** — ask before
assuming any of these.
