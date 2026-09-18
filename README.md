# Surgical Recovery Prediction

Can recovery after surgery be predicted from *All of Us* EHR and wearable data — and, first,
can it even be **measured** here? This repo holds the cohort definition, the per-metric audits
that decide which recovery outcome is definable at all, and the cross-metric comparison that
picks one. The modelling comes after that decision, not before it.

Runs in the **Verily Workbench** cloud workspace against CDR **`wb-silky-artichoke-2408.C2025Q4R6`,
Controlled Tier**. Verily Workbench does not publish the CDR through the environment — there is no
`WORKSPACE_CDR` and no `GOOGLE_CLOUD_PROJECT` — so the dataset is pinned in `cdr_config.py` and
committed. Pinning is deliberate: auto-selecting the newest release would shift results underneath
a finished analysis. Bump it as an explicit commit and re-run the affected notebooks; cache keys
include the CDR, so they miss.

**Nothing here is run locally.** The laptop has no CDR access and no BigQuery credentials. Every
notebook is executed by hand in the Workbench; the repo carries code only, `_cache/` lives on the
Workbench disk, and **no notebook in this repo has a committed output** — see *Data handling*.

## Layout

```
SurgicalRecoveryPrediction/
├── CLAUDE.md                      # the working rules; defers to the two skills below
├── .claude/skills/                # allofus-data-governance — DUA, suppression, what may be committed
├── srp_common.py                  # every shared definition: cohort, cache, suppression, plotters
├── cdr_config.py                  # the pinned CDR. Committed on purpose
├── cdr_schema.py                  # 133 tables, 1335 columns. Structure only, no values
├── cohort/
│   └── surgery_fitbit.ipynb       # what the cohort is and what it carries
├── index_surgery/
│   └── surgery_day_distribution.ipynb   # which surgery-day becomes the index date
├── outcomes/                      # one notebook per candidate recovery metric, plus the comparison
├── not_using/                     # retired approaches, kept as a record. Not a reference
└── <task>/_cache/                 # gitignored parquet, keyed on SQL + CDR
```

Task directories sit at the repo root and are named for the task, never for a file type or a
pipeline stage. One notebook per variable, no ordering prefixes. Shared logic lives in
`srp_common.py` at the root and is imported, never copied — notebooks find it by walking up from
`cwd`, so they run from either the task directory or the root.

## Run order

Every notebook queries BigQuery; none of them reads another notebook's cache except
`metric_comparison.ipynb`, which reads the scorecard rows the others write.

| notebook | what it settles |
|---|---|
| `cohort/surgery_fitbit.ipynb` | the cohort's breadth and size, and the shape of the person and procedure extracts |
| `index_surgery/surgery_day_distribution.ipynb` | how many patients have more than one surgery-day, and therefore whether the index rule matters. **Not yet settled** |
| `outcomes/fitbit_steps.ipynb` | whether a steps-based outcome is definable: coverage, the paired count, and time to return |
| `outcomes/fitbit_sleep.ipynb` | the same for sleep duration and efficiency, plus whether any peri-operative decline is just non-wear |
| `outcomes/fitbit_heart_rate.ipynb` | coverage from the daily summary, then what deriving a resting value from minute-level data would cost |
| `outcomes/length_of_stay.ipynb` | whether the index surgery sits in an identifiable stay, and whether the lengths spread |
| `outcomes/readmission.ipynb` | coverage, the censoring problem, and the event rate at 30 and 90 days |
| `outcomes/survey_physical_health.ipynb` | whether any patient has a survey answer on **both** sides of their surgery |
| `outcomes/metric_comparison.ipynb` | reads every scorecard row; picks the primary outcome and the pre-declared secondaries |

Run the per-metric notebooks before `metric_comparison.ipynb`. Each writes one row into the shared
task cache via `srp_common.write_scorecard`, keyed by notebook stem, so re-running replaces a row
rather than duplicating it. The rows refuse to combine across different CDR releases.

## The cohort

Derived from the CDR alone by `srp_common.cohort_ctes()`, so the definition lives in
version-controlled code and cannot be narrowed by Workbench UI state.

| population | rule | n |
|---|---|---|
| surgical concept set | every descendant of OMOP `4301351` (SNOMED 387713003, "Surgical procedure") in `concept_ancestor` | *not yet run* |
| has Fitbit | ≥1 row in `activity_summary` | *not yet run* |
| **cohort** | has Fitbit **and** ≥1 `procedure_occurrence` row with a qualifying `procedure_concept_id` | *not yet run* |
| with a dated surgery | that, and ≥1 qualifying row carrying a non-null `procedure_datetime` | *not yet run* |

**The `n` column is empty because no notebook in this repo has been run against the CDR from this
machine, and outputs are stripped before commit.** Fill it in from a Workbench run; do not guess.

**Do not reintroduce a dependency on the cohort-builder index tables.** The Workbench cohort
builder emits a per-build index dataset (`<CDR>_index_<stamp>`, holding `T_ENT_*` / `T_HAD_*` /
`T_ESA_*`) and the notebooks used to select through it. That put the cohort definition in UI state:
its breadth was unverifiable, a rebuild changed results silently, and the dataset had to be
hand-pinned because listing it needs `bigquery.datasets.get`, which All of Us does not grant on the
CDR's project. If a cohort needs changing, change `cohort_ctes`.

A separate EHR test is not needed: `procedure_occurrence` rows **are** EHR data, so requiring a
qualifying procedure already implies it.

## Cache

One `_cache/` per task directory, gitignored, holding parquet. The cache key is
`sha256(normalised SQL + CDR)[:16]`, so a CDR bump misses rather than silently serving the previous
release under the new release's name, and an edited query misses rather than returning the old
answer.

* A hit returns the parquet without an API call.
* Every query aggregates **inside** BigQuery and returns counts, quantiles or correlations. No
  query in this repo returns participant-level rows, so the cache holds aggregates only — but it
  stays gitignored regardless, because that is a property of the queries and not a guarantee of the
  directory.
* `cfg.dry_run_bytes(sql)` prices a query before running it. `fitbit_heart_rate.ipynb` is the only
  notebook that uses it as a gate: `MINUTE_LEVEL_BUDGET_GIB = 200` refuses the minute-level
  derivation above that, so reaching for `heart_rate_intraday` has to be a decision rather than an
  accident.

## Shared code

| file | what it is the authority for |
|---|---|
| `srp_common.py` | CDR resolution, `cohort_ctes`, the parquet-cached reader, `DEFAULT_WINDOWS`, `SUPPRESS_THRESHOLD`, `collapse_small_cells`, `write_scorecard`/`read_scorecards`, and every plotter |
| `cdr_config.py` | `PINNED_CDR`. Resolution order is explicit argument → environment variable → this |
| `cdr_schema.py` | the CDR's column schema, generated from `INFORMATION_SCHEMA.COLUMNS`. **Regenerate it whenever `PINNED_CDR` is bumped** — a schema from a different release is worse than none, so `require_columns` detects the mismatch and skips rather than validating against the wrong one |

Each notebook declares what it reads with `cfg.require_columns({...})`, so a wrong or renamed
column fails at `init()` with near-miss suggestions instead of as a BigQuery 400 partway through a
run. `require_columns` cannot answer what a column's *type* is —
`srp_common.column_type(table, column)` does. Assume nothing: `sleep_daily_summary.is_main_sleep`
is a **STRING**, not a BOOL, and needs `LOWER(CAST(... AS STRING)) IN ('true', '1')`.

## What is settled

**The analysis windows are 30 days either side.** `DEFAULT_WINDOWS` is `baseline = (-30, -1)`,
`postop = (1, 30)`. Day 0 is in neither. The baseline is the month before surgery and is therefore
already depressed by the condition being operated on — it understates the drop from health, and
"return to baseline" means return to that mixed level, not to health.

**The index rule is `"first"`, provisionally.** Which of a patient's surgery-days becomes the index
date is a parameter, not a constant, because `index_surgery/surgery_day_distribution.ipynb` has not
settled it. `"first"` is used until it does, because it is the only rule no post-operative event can
influence, and every result is stamped with the rule so a later change is visible.

**Readmission excludes transfers and episode continuations.** Counting them inflates the event rate
with administrative artefacts rather than clinical ones.

**Two metrics are retired**, in `not_using/`, and the reasons are the point — see *Not maintained*.

**A cross-sectional curve is not a within-patient one.** Every per-day median over a wearable
metric is taken over whoever recorded a value that day, and that set shrinks toward surgery. A
decline is therefore consistent with patients doing worse **or** with the patients who score
highest being the ones who stop wearing the device. `fitbit_sleep.ipynb` Part 3 is the worked
pattern for separating the two: a fixed panel anchored on a band that does not overlap the run-up,
each night differenced against that patient's own reference. Any new wearable metric needs the same
treatment before its curve is believed.

**Nights and days with no record are absent, not zero.** No wearable query zero-fills. This is
correct, and it is exactly why the denominator moves from day to day.

## Data handling

**Per the All of Us DUA, only plain code may be committed. No data at all.** This outranks every
convenience and every layout convention. `.gitignore` is deny-by-default: `*` ignores everything,
a whitelist re-admits code, notebooks, markdown and environment declarations, and data-bearing
extensions are then re-ignored after the whitelist, so widening a whitelist later cannot silently
expose them.

The non-obvious consequence: **notebook outputs must be stripped before committing**, because saved
cell outputs embed rendered tables and figures, and that is data. Two redundant layers enforce it,
and **both need one-time setup per clone**, because `.git/config` is not cloned:

```
pip install -r requirements.txt && nbstripout --install --attributes .gitattributes
git config core.hooksPath .githooks
```

* **`nbstripout` clean filter** (`.gitattributes`) strips outputs from the *index* on `git add`,
  leaving the working copy alone — inline figures stay local, the commit gets code and markdown.
* **`.githooks/pre-commit`** (versioned, stdlib only) independently inspects every staged `.ipynb`
  blob and rejects any with outputs or a set `execution_count`, and rejects staged data-bearing
  extensions. It travels with the repo, which is what turns a fresh workspace's silently-absent
  filter into a blocked commit.

**Never `git commit --no-verify` here.** It is a compliance control, not a style check.

**Suppressed figures are the exception.** Aggregates meeting the All of Us small-cell threshold may
leave the workspace, so `*_suppressed.{png,svg,pdf}` is committable and every other image is
rejected. The suffix is written only by the `suppress=True` plotting path, so it cannot lie. Both
the ignore rule and the hook key on the **filename**, never a directory path.
`SUPPRESS_THRESHOLD = 20`, and **the threshold is not the whole policy** — subgroup and
variable-combination rules also apply, and a cumulative curve drawn over unmasked data leaks masked
cell heights through its step sizes. Confirm current policy before publishing.

## Notebook format

One notebook per variable, carrying that variable's analysis end to end. Inside, sections are
`## Part N — Title Case`, the first being `## Shared Imports and Methods`, and each cell is a whole
analysis unit rather than a single small question — its own constants at the top with an inline
comment each, its derivation, and every figure and table that answer one thing together. A
2-to-4-sentence markdown cell sits directly above each one, with the load-bearing claim in bold.
The conventions live in the `pipeline-manager` skill; the reference implementation is
[`AndrewTaehonKim/All_of_Us-Cancer_Wearables`](https://github.com/AndrewTaehonKim/All_of_Us-Cancer_Wearables).

## Not maintained

`not_using/` holds approaches decided against. They are kept, never deleted, because deleting loses
the record of what was tried and why it failed — which is what stops it being proposed again.

| notebook | why |
|---|---|
| `days_alive_out_of_hospital.ipynb` | the composite needs death records this CDR does not usefully carry, which would silently score deaths as perfect recoveries |
| `discharge_disposition.ipynb` | a discharge destination is recorded for roughly 5% of the cohort |

Retiring is not finished until the references are gone: `metric_comparison.ipynb` built DAOH itself
rather than reading it from a scorecard, so retiring that metric meant editing three of its cells.
