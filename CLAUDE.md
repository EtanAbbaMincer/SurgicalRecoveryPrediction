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

**Suppressed figures are the exception.** Aggregates meeting the All of Us small-cell
threshold may leave the workspace, so `*_suppressed.{png,svg,pdf}` is committable; every
other image is ignored and rejected by the hook. Both rules key on the filename, never a
directory path. The threshold is not the whole policy — subgroup and
variable-combination rules also apply.

Never use `git commit --no-verify` here. Details in `allofus-data-governance`.

### Refreshing a workspace: `git sync`

The workbench checkout is a scratch copy, not a place work is authored, so the wanted
behaviour on a refresh is "make this directory match `origin`, and drop everything else" —
stale figures, orphaned notebooks from a renamed task, `__pycache__`. Plain `git pull` does
neither: it refuses on a conflict and never removes an untracked file, so old output
accumulates and gets mistaken for current output.

**`_cache/` is the one thing that must survive.** It holds materialized BigQuery extracts
that cost real query spend to rebuild, and it is git-ignored, so an unqualified clean
deletes it.

That is a third piece of **one-time setup**, this one per machine rather than per clone
(`--global`):

```
git config --global alias.sync '!b=$(git rev-parse --abbrev-ref HEAD) && git fetch origin && git reset --hard "origin/$b" && git clean -fdx -e "_cache*" -e ".venv" -e ".env"'
git config --global alias.sync-preview '!b=$(git rev-parse --abbrev-ref HEAD) && git fetch origin && echo "--- files that differ from origin/$b ---" && git diff --stat "HEAD..origin/$b" && echo "--- local files that would be DELETED ---" && git clean -fdxn -e "_cache*" -e ".venv" -e ".env"'
```

Then use `git sync` in place of `git pull` in the workbench, and `git sync-preview` first
whenever it is not obvious what is about to be thrown away.

The `-e` patterns are gitignore syntax and carry no slash, so `_cache*` matches at any depth
and keeps working if the layout changes — the same reasoning as the `*_suppressed.*` rules.
`.venv` and `.env` are excluded because deleting an environment or a credentials file on
every refresh is never what was meant. Nested git repositories, including
`.claude/worktrees/`, are skipped by `git clean` unless `-ff` is given; do not add it.

**`git sync` discards uncommitted local work, by design.** Commit or push anything worth
keeping before running it. That makes it wrong for a checkout where notebooks are actually
being edited, which is why it is a separate command rather than a redefinition of `pull`.

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
  bmi_figures/            <- *_suppressed.png committable; other figures git-ignored
  blood_pressure.ipynb
  blood_pressure_figures/
  _cache/                 <- git-ignored
```

**Retired work goes to `not_using/` at the repo root, never deleted.** A notebook for an
approach that has been decided against moves there keeping its filename; its figures and
cache entries are regenerable and git-ignored, so they are dropped. Deleting would lose
the record that the approach was tried and why it failed, which is what stops it being
proposed again. `not_using/` is not a task directory, so the notebooks / `*_figures/` /
`_cache/` rule does not apply to it.

**Retiring is not finished until the references are gone.** Other notebooks may compute or
compare against the retired metric -- `metric_comparison.ipynb` built DAOH itself rather
than reading it from a scorecard, so retiring that metric meant editing three of its cells.

Currently in `not_using/`:

| Notebook | Why |
|---|---|
| `days_alive_out_of_hospital.ipynb` | Composite needs death records the CDR does not usefully carry, which would silently score deaths as perfect recoveries |
| `discharge_disposition.ipynb` | A discharge destination is recorded for roughly 5% of the cohort |

Shared code lives in **`srp_common.py` at the repo root** -- CDR resolution, the cohort
definition, the Parquet-cached BigQuery reader, small-cell suppression and the shared
plotters -- imported by each notebook rather than copy-pasted. Notebooks find it by walking
up from `cwd`, so they run from either the task directory or the root.

**Verily Workbench does not publish the CDR through the environment** -- there is no
`WORKSPACE_CDR` and no `GOOGLE_CLOUD_PROJECT`. So the dataset is **pinned in
`cdr_config.py`** at the repo root, which is committed. Pinning is deliberate:
auto-selecting the newest CDR would shift results underneath a finished analysis when
All of Us publishes a release. Bump it as an explicit commit and re-run affected
notebooks -- cache keys include the CDR, so they miss.

**The CDR is the only dataset a notebook needs.** The cohort is derived from it by
`srp_common.cohort_ctes()`, which builds the concept set from `concept_ancestor`, takes
"has Fitbit" as presence in `activity_summary`, and requires at least one qualifying
`procedure_occurrence` row. There is no separate EHR test because procedure rows *are*
EHR data.

This is deliberate and worth not undoing. The Workbench cohort builder emits a per-build
index dataset (`<CDR dataset>_index_<build stamp>`, holding `T_ENT_*` / `T_HAD_*` /
`T_ESA_*`), and the notebooks used to select through it. That put the cohort definition in
UI state rather than in code: its breadth was unverifiable, a rebuild changed results
silently, and the dataset had to be hand-pinned because listing it needs
`bigquery.datasets.get`, which All of Us does not grant on the CDR's project. Deriving the
cohort from the CDR removes all of that. **Do not reintroduce a dependency on the index
tables** -- if a cohort needs changing, change `cohort_ctes`.

Resolution order is explicit argument -> environment variable -> `cdr_config.PINNED_CDR`.
One-off override without a commit:

```python
cfg = srp_common.init("<stem>", cdr="<project>.<dataset>")
```

```python
cfg = srp_common.init("<notebook stem>")
CDR = cfg.CDR
FIGURES, CACHE_DIR = cfg.FIGURES, cfg.CACHE_DIR
cached_gbq = cfg.cached_gbq

CTES = cfg.cohort_ctes(index_rule="first")   # cohort + one index date per patient
```

The **index rule** -- which of a patient's surgery-days becomes the index date -- is a
parameter, not a constant, because `index_surgery/surgery_day_distribution.ipynb` has not
settled it. `"first"` until it does.

**The CDR's column schema is pinned too**, in `cdr_schema.py` at the repo root -- 133
tables, 1335 columns, generated from `INFORMATION_SCHEMA.COLUMNS`. Structure only, so it
is committable: table names, column names and types, never row counts or values.

`cohort_ctes` validates its own tables on every call, and each notebook declares what else
it reads:

```python
cfg.require_columns({"sleep_daily_summary": ["person_id", "sleep_date", "is_main_sleep"]})
```

A wrong or renamed name then fails at `init()` with near-miss suggestions, instead of as a
BigQuery 400 partway through a run. `srp_common.column_type(table, column)` answers the
question that check cannot -- `sleep_daily_summary.is_main_sleep` is a **STRING**, not a
BOOL, so it needs `LOWER(CAST(... AS STRING)) IN ('true', '1')`. Assume nothing about a
type; look it up.

**Regenerate `cdr_schema.py` whenever `PINNED_CDR` is bumped.** A schema generated against
a different CDR is worse than none, so `require_columns` detects the mismatch, warns and
skips rather than validating against the wrong release.

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
