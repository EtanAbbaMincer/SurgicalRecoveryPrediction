---
name: allofus-data-governance
description: All of Us / Verily Workbench data governance for this repo — what may be committed under the DUA, the deny-by-default .gitignore, mandatory notebook-output stripping, small-cell suppression, and the OMOP/BigQuery access rules. Use before committing anything, before writing a query or notebook that touches participant data, before changing .gitignore, and whenever deciding whether an output may leave the workspace.
---

## Governing rule — the DUA allows plain code only

**Per the All of Us Data Use Agreement, the only thing that may be committed to this repo is
plain code. No data at all.**

This is a compliance obligation, not a preference. It **outranks every layout convention,
including the `pipeline-manager` skill**, and it outranks convenience without exception. Data
committed to git persists in history — a violation is not a recoverable mistake.

### What that means concretely

`.gitignore` is **deny-by-default**: `*` ignores everything, `!*/` allows git to descend into
directories, then a whitelist re-admits only

- Claude files (`CLAUDE.md`, `.claude/**`) and markdown
- notebooks (`*.ipynb`)
- plain code (`*.py`, `*.sql`, `*.R`)
- environment declarations (`requirements.txt`, `environment.yml`, `pyproject.toml`)

and then **data-bearing paths are re-ignored after the whitelist** (last match wins):
`_cache/`, `.cache/`, and every data extension (`*.csv`, `*.parquet`, `*.json`,
`*.pkl`, `*.h5`, …). Data types are denied twice — by the default `*` and again explicitly — so
widening a whitelist later cannot silently expose them. The `.claude/` re-admission must stay the
**last block** in the file, since `*.json` would otherwise swallow it.

**Never weaken this by adding a broad whitelist.** Add narrow, extension-specific rules only.

### Forced consequence — strip notebook outputs before committing

Notebooks are on the allowed list, but **saved cell outputs embed rendered tables and figures,
which is data.** "Notebooks allowed" and "no data at all" are only consistent if a committed
notebook carries code and markdown with **outputs cleared**.

- Figures live in the git-ignored `<stem>_figures/` folder, so stripping loses nothing durable.
- A staged `.ipynb` whose diff shows populated `"outputs": [...]` is a **violation** — stop and
  clear it.
### Enforcement — INSTALLED, two independent layers

Manual discipline is not the control. Two layers are in place, deliberately redundant:

**Layer 1 — `nbstripout` clean filter.** `.gitattributes` (committed) declares
`*.ipynb filter=nbstripout`, so staging a notebook strips outputs and `execution_count` from the
**index** while leaving your working copy untouched — you keep your inline figures locally, and
the commit gets code and markdown only.

**Layer 2 — `.githooks/pre-commit` (committed, stdlib-only).** Independently inspects the staged
blob of every `.ipynb` and rejects any with outputs or a set `execution_count`. It also rejects
staged files with data-bearing extensions, which catches `git add -f` of a `.csv`/`.parquet`.

**Why both.** `.git/config` is not part of a clone, so on a fresh clone — a new Verily Workbench
workspace, a collaborator — the filter is **silently absent**: `git add` succeeds and stages the
outputs with no warning. Verified empirically. The hook is versioned in the repo, so it travels
and turns that silent failure into a blocked commit.

### Suppressed figures MAY be committed

Aggregate results meeting the All of Us small-cell threshold are permitted to leave the
workspace -- that is what the Data and Statistics Dissemination Policy is for -- so a
figure whose every cell clears it is not "data" in the sense the DUA prohibits.

**Only `*_suppressed.{png,svg,pdf}` is committable.** That name is written solely by the
`suppress=True` plotting path, which aggregates the sparse tail into a bucket meeting
the threshold; the filename is derived from the flag rather than a caller-supplied path,
so the suffix cannot lie. `.gitignore` admits exactly those patterns and the pre-commit
hook rejects any other image. Both key on the **filename only, never a directory path**,
so they survive a layout change and work at any depth.

Unsuppressed working figures stay ignored: they can carry a cell under the threshold,
and a PNG cannot be reviewed in a diff or removed from history after a push.

**The threshold is not the whole policy.** Dissemination rules also cover small
subgroups and combinations of variables, and repeated releases permit complementary
disclosure -- a cumulative curve drawn over unmasked data leaks the masked cell heights
through its step sizes, which is why the tail is aggregated rather than masked. Confirm
current policy before publishing.

### Per-clone setup — REQUIRED, two commands

Neither layer can self-install; git requires local config. In every new clone or workspace:

```
pip install -r requirements.txt && nbstripout --install --attributes .gitattributes
git config core.hooksPath .githooks
```

Until `core.hooksPath` is set, **Layer 2 is inactive.** Verify with
`git config --get core.hooksPath` (expect `.githooks`) and
`git config --get filter.nbstripout.clean` (expect a path).

**Never bypass with `git commit --no-verify`.** It is a compliance control, not a style check.

---

## Data handling rules

- **Row-level data never leaves the workspace.** No cell may download participant-level data
  locally, write it into the repo, or send it to an external service.
- **Small-cell suppression.** Aggregate results are subject to the All of Us Data and Statistics
  Dissemination Policy. Do not report counts below the allowed threshold (historically <20
  participants); **confirm the current threshold** before publishing any table. Display
  aggregates, never record listings — no `df.head()` of participant rows in anything shared.
- **Analysis runs in the cloud workspace**, not on this laptop. Assume no local data files.
- **Caches may hold row-level data**, so `_cache/` stays inside the Verily Workbench workspace
  and stays git-ignored. Caching is a performance decision and does not relax any rule here.

## Data access

- Access is **BigQuery over the OMOP CDM**: `person`, `condition_occurrence`,
  `procedure_occurrence`, `measurement`, `observation`, `drug_exposure`, `visit_occurrence`,
  `death`, plus the concept/vocabulary tables.
- Reference the **workspace-provided dataset variable**, never a hard-coded project ID. (The
  All of Us Researcher Workbench exposes this as `WORKSPACE_CDR`; confirm the equivalent that
  Verily Workbench injects before relying on the name.)
- **Pin the CDR / data-release version** in analysis output — results are not reproducible across
  releases otherwise, and cache keys must include it so a new release misses rather than serving
  stale data.
- Build phenotypes from **OMOP standard concept IDs / concept sets**, not raw code strings, so
  they survive vocabulary updates.
- **Cohort-builder index datasets are a separate, un-versioned reference.** Workbench cohort
  builder emits SQL against two datasets: the CDR (resolvable from the environment) and a
  per-build index dataset suffixed with a build date, e.g. `<CDR>_index_061026`, holding the
  `T_ENT_*` / `T_HAD_*` / `T_ESA_*` cohort tables. That suffix has no standard environment
  variable and changes when the **cohort is rebuilt** — a different trigger from a CDR release.
  Keep it in one named constant per notebook (`COHORT_INDEX_SUFFIX`), never inline in the SQL,
  and include both dataset names in any cache key so a rebuild invalidates cached extracts.
- **Generated cohort-builder code ends each query with `.head(5)`.** That is a participant-level
  record listing. Replace it with an aggregate summary; inspecting rows interactively is fine,
  but a listing must never reach saved output.

---

## Relationship to `pipeline-manager`

`pipeline-manager` (user-global) owns **portable layout**: task-scoped directories at the repo
root, one notebook per variable, no filename prefixes, `<stem>_figures/` folders, `<task>/_cache/`,
and the caching strategy. It is deliberately domain-neutral.

This skill owns everything **specific to All of Us / Verily Workbench**. Where the two conflict,
**this skill wins.**
