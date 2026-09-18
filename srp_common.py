"""Shared helpers for SurgicalRecoveryPrediction notebooks.

One copy of the things every task notebook needs: resolving the CDR from the
workspace environment, discovering the cohort-builder index dataset, and a
Parquet-cached BigQuery wrapper. Notebooks import this instead of carrying their
own copy, so a change here reaches every notebook at once.

Usage from a notebook inside a task directory:

    import sys, pathlib
    _root = pathlib.Path.cwd().resolve()
    while not (_root / "srp_common.py").exists():
        if _root.parent == _root:
            raise RuntimeError("repo root not found: no srp_common.py above cwd")
        _root = _root.parent
    sys.path.insert(0, str(_root))

    import srp_common
    cfg = srp_common.init("surgery_day_distribution")
    CDR = cfg.CDR
    FIGURES, CACHE_DIR = cfg.FIGURES, cfg.CACHE_DIR
    cached_gbq = cfg.cached_gbq

GOVERNANCE. Caches written by this module hold row-level All of Us data. They live
in the task directory's git-ignored _cache/ and must stay inside the Verily
Workbench workspace -- never committed, never downloaded. See the
allofus-data-governance skill.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__all__ = [
    "CDR_ENV_CANDIDATES",
    "Session",
    "init",
    "list_cdr_datasets",
    "resolve_cdr",
    # cohort SQL
    "SURGERY_ANCESTOR_CONCEPT_ID",
    "SURGERY_ANCESTOR_SNOMED_CODE",
    "FITBIT_PRESENCE_TABLE",
    "INDEX_RULES",
    "DEFAULT_WINDOWS",
    "cohort_ctes",
    "window_case_sql",
    "require_columns",
    "column_type",
    "COHORT_TABLES",
    # figures and suppression
    "SUPPRESS_THRESHOLD",
    "SURFACE",
    "INK",
    "INK_MUTED",
    "GRID",
    "CATEGORICAL",
    "SERIES",
    "STATUS",
    "verdict_label",
    "collapse_small_cells",
    "min_cell",
    "style_axes",
    # index hospitalisation
    "INPATIENT_VISIT_CONCEPT_IDS",
    "index_visit_cte",
    # scorecard hand-off
    "write_scorecard",
    "read_scorecards",
    # plotters
    "plot_rel_day_curve",
    "plot_distribution",
]

CDR_ENV_CANDIDATES = (
    # All of Us Researcher Workbench
    "WORKSPACE_CDR",
    "CDR_DATASET",
    "WORKSPACE_CDR_DATASET",
    # Verily Workbench / generic
    "WORKBENCH_CDR",
    "SRP_CDR",
)

# Any env key matching this, whose value looks like "project.dataset", is treated as
# a candidate CDR. Cheaper than maintaining a list of every platform's variable name.
_CDR_KEY_HINT = ("CDR", "CURATED", "DATASET")


def _looks_like_dataset(value: str) -> bool:
    """True for a 'project.dataset' reference: one dot, no whitespace, no slashes."""
    value = (value or "").strip()
    return (
        value.count(".") == 1
        and not any(c.isspace() for c in value)
        and "/" not in value
        and all(part for part in value.split("."))
    )


def _env_candidates() -> dict[str, str]:
    """Env keys that hint at a CDR and hold something shaped like a dataset ref."""
    return {
        k: v for k, v in os.environ.items()
        if any(h in k.upper() for h in _CDR_KEY_HINT) and _looks_like_dataset(v)
    }


def resolve_cdr(cdr: str | None = None) -> tuple[str, str]:
    """Return (source, CDR dataset).

    Resolution order: an explicit `cdr` argument, then the known variable names,
    then any environment key that hints at a CDR and holds a 'project.dataset'
    value. Never falls back to a literal project ID -- a wrong-but-plausible
    dataset fails silently, so this raises with diagnostics instead.
    """
    if cdr:
        if not _looks_like_dataset(cdr):
            raise ValueError(
                f"cdr={cdr!r} does not look like 'project.dataset'."
            )
        return "explicit argument", cdr

    for var in CDR_ENV_CANDIDATES:
        value = os.environ.get(var)
        if value:
            return f"${var}", value

    discovered = _env_candidates()
    if len(discovered) == 1:
        var, value = next(iter(discovered.items()))
        print(f"Note: resolved the CDR from ${var} by pattern match, not a known name.")
        return f"${var} (discovered)", value
    if len(discovered) > 1:
        raise RuntimeError(
            "Several environment variables look like a CDR dataset; pass the right "
            "one explicitly as srp_common.init(..., cdr=...):\n"
            + "\n".join(f"    {k} = {v}" for k, v in sorted(discovered.items()))
        )

    pinned = _pinned("PINNED_CDR")
    if pinned:
        if not _looks_like_dataset(pinned):
            raise ValueError(
                f"cdr_config.PINNED_CDR={pinned!r} does not look like 'project.dataset'."
            )
        return "cdr_config.PINNED_CDR", pinned

    # Nothing found anywhere. Report what IS in the environment so this is diagnosable.
    nearby = {
        k: v for k, v in sorted(os.environ.items())
        if any(h in k.upper() for h in
               ("CDR", "DATASET", "WORKSPACE", "WORKBENCH", "PROJECT", "BIGQUERY", "BQ", "GOOGLE"))
    }
    listing = ("\n".join(f"    {k} = {v}" for k, v in nearby.items())
               if nearby else "    (no plausibly related variables found)")
    raise RuntimeError(
        "Could not resolve the CDR dataset. Tried: an explicit cdr= argument, the "
        "known variable names (" + ", ".join(CDR_ENV_CANDIDATES) + "), a pattern "
        "match over every environment key, and cdr_config.PINNED_CDR.\n\n"
        "Environment variables that might have carried it:\n" + listing + "\n\n"
        "Verily Workbench does not publish the CDR through the environment, so the "
        "normal fix is to pin it:\n\n"
        "  1. Set PINNED_CDR in cdr_config.py at the repo root. Commit it -- a dataset\n"
        "     identifier is configuration, not participant data, and pinning is what\n"
        "     makes the analysis reproducible across CDR releases.\n"
        "  2. Not sure of the name? List what the workspace can see:\n"
        "         import srp_common; srp_common.list_cdr_datasets()\n"
        "  3. One-off override, no commit:\n"
        "         cfg = srp_common.init(\"<stem>\", cdr=\"<project>.<dataset>\")\n\n"
        "Do not hard-code the project ID into the SQL."
    )


def _pinned(name: str):
    """Read `name` from cdr_config.py if that module is importable, else None.

    Optional by design: a checkout without the file still works via an explicit
    argument or an environment variable.
    """
    try:
        import cdr_config
    except ImportError:
        return None
    return getattr(cdr_config, name, None)


def list_cdr_datasets(project: str | None = None):
    """List datasets in `project` that look like a CDR or cohort index.

    CAVEAT: with no argument this uses the project from Application Default
    Credentials, which in Verily Workbench is the project queries BILL from -- not the
    All of Us-managed project that HOLDS the CDR. Listing the data project usually
    fails with 403, because read access to the curated tables does not include
    'bigquery.datasets.get'. So this is a convenience for workspaces where metadata
    access exists; where it does not, read the dataset names out of the cohort-builder
    SQL and pin them in cdr_config.py.
    """
    import pandas_gbq

    if project is None:
        try:
            import google.auth

            _, project = google.auth.default()
        except Exception as exc:
            raise RuntimeError(
                "Could not determine the BigQuery project from Application Default "
                f"Credentials ({exc}). Pass it: list_cdr_datasets(project='...')."
            ) from exc
    if not project:
        raise RuntimeError(
            "Application Default Credentials carry no project. Pass it explicitly: "
            "list_cdr_datasets(project='...')."
        )

    sql = f"""
        SELECT schema_name, creation_time
        FROM `{project}`.INFORMATION_SCHEMA.SCHEMATA
        ORDER BY creation_time DESC
    """
    datasets = pandas_gbq.read_gbq(sql, dialect="standard", progress_bar_type=None)
    print(f"project: {project}   ({len(datasets)} dataset(s))")
    print("Candidates for cdr_config.PINNED_CDR are the ones WITHOUT an '_index_' "
          "suffix:\n")
    for _, row in datasets.iterrows():
        kind = "cohort index" if "_index_" in row["schema_name"] else "CDR candidate"
        print(f"    {project}.{row['schema_name']:38s} {kind:14s} "
              f"created {row['creation_time']}")
    return datasets


@dataclass
class Session:
    """Resolved dataset names and paths for one notebook, plus its cached reader."""

    notebook: str
    CDR: str
    CDR_SOURCE: str
    FIGURES: Path
    CACHE_DIR: Path
    _reader: Callable | None = field(default=None, repr=False)

    @property
    def CDR_PROJECT(self) -> str:
        return self.CDR.partition(".")[0]

    @property
    def CDR_DATASET(self) -> str:
        return self.CDR.partition(".")[2]

    def cache_key(self, sql: str) -> str:
        """Hash the query together with the dataset it runs against.

        Including the CDR is what makes a release bump miss the cache instead of
        serving the previous release's extract under the new release's name.
        """
        return hashlib.sha256(
            "|".join([sql, self.CDR]).encode()
        ).hexdigest()[:16]

    def cached_gbq(self, sql: str, name: str, force_refresh: bool = False):
        """Run `sql`, caching the result as Parquet in the task's _cache/.

        force_refresh re-queries and overwrites even on a hit.
        """
        import pandas

        path = self.CACHE_DIR / f"{name}_{self.cache_key(sql)}.parquet"

        if path.exists() and not force_refresh:
            df = pandas.read_parquet(path)
            print(f"[cache hit ] {name}: {len(df):,} rows from {path.name}")
            return df

        reader = self._reader or _default_reader
        print(f"[cache miss] {name}: querying BigQuery ...")
        df = reader(sql)
        df.to_parquet(path, index=False)
        print(f"[cached    ] {name}: {len(df):,} rows -> {path.name}")
        return df


    def cohort_ctes(self, index_rule: str = "first", extra: str = "") -> str:
        """Cohort + index-surgery CTE prefix for this session's datasets."""
        return cohort_ctes(self, index_rule=index_rule, extra=extra)

    def require_columns(self, requirements: dict) -> None:
        """Assert the pinned schema has `{table: [columns]}`, against this CDR."""
        require_columns(requirements, cdr=self.CDR)

    def dry_run_bytes(self, sql: str) -> int:
        """Bytes BigQuery would scan for `sql`, without running or billing it.

        Cheap insurance against an accidental full scan of a minute-level Fitbit
        table. Returns -1 rather than raising if the dry run itself fails, since a
        missing estimate should not stop an otherwise valid notebook.
        """
        try:
            from google.cloud import bigquery

            client = bigquery.Client()
            job = client.query(
                sql,
                job_config=bigquery.QueryJobConfig(dry_run=True, use_query_cache=False),
            )
            processed = int(job.total_bytes_processed)
            print(f"dry run: {processed / 2**30:,.2f} GiB would be scanned")
            return processed
        except Exception as exc:
            print(f"dry run unavailable ({type(exc).__name__}: {exc})")
            return -1

    def figure_path(self, name: str, suppress: bool) -> Path:
        """Figure filename, with the '_suppressed' suffix derived from the flag.

        The suffix is what .gitignore and the pre-commit hook key on, so it must be
        written by the suppressing code path and never by a caller-supplied string --
        otherwise an unsuppressed figure can claim a suppression that did not happen.
        """
        return self.FIGURES / f"{name}{'_suppressed' if suppress else ''}.png"

    def save_figure(self, fig, name: str, suppress: bool, note: str = "") -> Path:
        """Stamp the provenance footer onto `fig`, save it, and say what it may be.

        The footer carries the CDR so a figure is never separated from the release it
        came from, and states in the image itself whether suppression was applied.
        """
        status = (f"suppressed at the {SUPPRESS_THRESHOLD}-participant threshold"
                  if suppress else
                  f"NOT suppressed -- may contain cells below {SUPPRESS_THRESHOLD}; "
                  "do not share this version")
        footer = f"All of Us  |  CDR {self.CDR}  |  {status}"
        if note:
            footer += f"  |  {note}"
        fig.text(0.01, 0.005, footer, fontsize=8, color=INK_MUTED)

        out = self.figure_path(name, suppress)
        fig.savefig(out, dpi=150, bbox_inches="tight", facecolor=SURFACE)
        print(f"saved -> {out}"
              + ("   (committable)" if suppress else "   (local only, git-ignored)"))
        return out


def _default_reader(sql: str):
    """Read via pandas_gbq, preferring the BigQuery Storage API.

    Falls back to the REST API when Storage is not enabled or permissions are
    missing, which happens outside the Workbench.
    """
    import pandas_gbq

    try:
        return pandas_gbq.read_gbq(
            sql, dialect="standard", use_bqstorage_api=True,
            progress_bar_type="tqdm_notebook",
        )
    except Exception:
        return pandas_gbq.read_gbq(
            sql, dialect="standard", use_bqstorage_api=False,
            progress_bar_type="tqdm_notebook",
        )


def init(notebook: str, verbose: bool = True, reader: Callable | None = None,
         cdr: str | None = None) -> Session:
    """Resolve the CDR, create the notebook's git-ignored output dirs, and report.

    `notebook` is the notebook's filename stem; its figure folder is named
    '<notebook>_figures/' per the repo layout convention. `reader` overrides the
    BigQuery reader, for tests. `cdr` overrides resolution.

    The CDR is the only dataset a notebook needs: cohorts are derived from it by
    cohort_ctes rather than read from a Workbench cohort-builder index.
    """
    cdr_source, cdr = resolve_cdr(cdr)

    figures = Path(f"{notebook}_figures")
    cache_dir = Path("_cache")
    figures.mkdir(exist_ok=True)
    cache_dir.mkdir(exist_ok=True)

    session = Session(
        notebook=notebook, CDR=cdr, CDR_SOURCE=cdr_source,
        FIGURES=figures, CACHE_DIR=cache_dir, _reader=reader,
    )

    if verbose:
        print(f"CDR         : {cdr}   (from {cdr_source})")
        print(f"Figures     : {figures}/")
        print(f"Cache       : {cache_dir}/")

    return session


# ===========================================================================
# Cohort and index-surgery SQL
#
# Every notebook that measures something "relative to the surgery" needs the same
# three things: the surgical concept set, the built cohort, and one index date per
# patient. Duplicating that across notebooks is how two notebooks end up silently
# analysing different cohorts -- the SQL still executes, it just answers a different
# question -- so it is defined once here and imported.
# ===========================================================================

# SNOMED 387713003, "Surgical procedure". Its descendants define "a surgery" for this
# project. Referenced by standard concept ID rather than code string so the concept set
# survives a vocabulary update; the code is recorded alongside it for human review.
SURGERY_ANCESTOR_CONCEPT_ID = 4301351
SURGERY_ANCESTOR_SNOMED_CODE = "387713003"

# "Has Fitbit data" is taken as having at least one row in this table. All of Us
# publishes a has_fitbit flag in its cohort-builder helper tables, but reading that
# would put the cohort definition back outside version control; presence in a Fitbit
# table is the same statement, derived from the CDR and auditable here.
FITBIT_PRESENCE_TABLE = "activity_summary"

# How to collapse a patient's several surgery-days into one index date. Which rule is
# correct is still open -- that is what index_surgery/surgery_day_distribution.ipynb is
# for -- so it is a parameter, not a constant, and every notebook records the rule it
# used. "first" is the provisional default because it is the only rule that cannot be
# influenced by post-operative events.
INDEX_RULES = {
    "first": "MIN",
    "last": "MAX",
}

# Windows in days relative to the index date, both bounds inclusive. Shared so that
# "coverage in the baseline window" means the same thing in every outcome notebook and the
# metrics stay comparable.
#
# Deliberately only two windows: the 30 days before surgery and the 30 days after. An
# earlier version split the post-operative side into acute / early / recovery / late and
# held the last pre-operative week out as its own "periop" bucket. That granularity is
# what a settled metric needs; the question these notebooks actually answer is the coarser
# one of which candidate metrics have usable data at all, and the finer buckets made every
# notebook carry window bookkeeping that decided nothing. Re-split the post-operative side
# when a metric is being constructed rather than screened.
#
# Day 0, the day of surgery, is in neither window: it is the operative day rather than a
# pre- or post-operative one, so a record on it counts toward neither side.
#
# The baseline runs right up to the day before surgery, which makes it a mixed
# pre-operative level rather than a healthy-state one -- activity in the final week is
# already depressed by the condition being operated on. It therefore understates the drop
# from health, and "return to baseline" means return to that mixed level.
DEFAULT_WINDOWS = {
    "baseline": (-30, -1),
    "postop": (1, 30),
}


def cohort_ctes(cfg: "Session", index_rule: str = "first",
                extra: str = "") -> str:
    """Return a 'WITH ...' prefix defining the cohort and one index date per patient.

    Reads ONLY the CDR. Defines five CTEs, each usable by the query that follows:

        surgical_concept  concept_id      -- the ancestor and every descendant
        fitbit_person     person_id       -- anyone with wearable data
        cohort            person_id       -- Fitbit + >=1 qualifying procedure
        surgical_events   person_id, surgery_date
        index_surgery     person_id, index_date, n_surgery_days

    The cohort is derived here rather than read from the Workbench cohort-builder
    index tables, so the definition lives in version-controlled code and cannot be
    silently narrowed by UI state -- which is what the repo's conventions require of
    a cohort definition, and what makes the concept set auditable by counting
    surgical_concept.

    A separate has_ehr_data test is not needed: procedure_occurrence rows ARE EHR
    data, so requiring a qualifying procedure already implies it.

    concept_ancestor includes the self-row at zero levels of separation, so the
    ancestor concept itself is in surgical_concept without a union. `extra` is
    appended as further comma-separated CTEs when a notebook needs its own.

    `index_rule` must be a key of INDEX_RULES; it is interpolated as an aggregate
    function name, never as user text, and an unknown value raises rather than
    producing SQL.
    """
    try:
        agg = INDEX_RULES[index_rule]
    except KeyError:
        raise ValueError(
            f"index_rule={index_rule!r} is not one of {sorted(INDEX_RULES)}."
        ) from None

    require_columns(
        {**COHORT_TABLES, FITBIT_PRESENCE_TABLE: ["person_id"]}, cdr=cfg.CDR
    )

    ctes = f"""
    surgical_concept AS (
        SELECT DISTINCT ca.descendant_concept_id AS concept_id
        FROM `{cfg.CDR}`.concept_ancestor AS ca
        WHERE ca.ancestor_concept_id = {SURGERY_ANCESTOR_CONCEPT_ID}
    ),
    fitbit_person AS (
        SELECT DISTINCT person_id
        FROM `{cfg.CDR}`.{FITBIT_PRESENCE_TABLE}
    ),
    cohort AS (
        SELECT DISTINCT po.person_id
        FROM `{cfg.CDR}`.procedure_occurrence AS po
        JOIN fitbit_person AS f ON f.person_id = po.person_id
        WHERE po.procedure_concept_id IN (SELECT concept_id FROM surgical_concept)
    ),
    surgical_events AS (
        SELECT
            po.person_id,
            DATE(po.procedure_datetime) AS surgery_date
        FROM `{cfg.CDR}`.procedure_occurrence AS po
        WHERE po.person_id IN (SELECT person_id FROM cohort)
          AND po.procedure_concept_id IN (SELECT concept_id FROM surgical_concept)
          AND po.procedure_datetime IS NOT NULL
    ),
    index_surgery AS (
        SELECT
            person_id,
            {agg}(surgery_date) AS index_date,
            COUNT(DISTINCT surgery_date) AS n_surgery_days
        FROM surgical_events
        GROUP BY person_id
    )"""

    if extra:
        ctes = ctes + ",\n" + extra.strip().rstrip(",")
    return "WITH" + ctes + "\n"


def window_case_sql(column: str = "rel_day", windows: dict | None = None) -> str:
    """Return a CASE expression labelling `column` with its DEFAULT_WINDOWS bucket.

    Days that fall in no window -- the gaps between buckets and anything outside the
    outermost bounds -- evaluate to NULL, so a downstream GROUP BY silently drops them
    rather than inventing an "other" category that would be read as a real window.
    """
    windows = windows or DEFAULT_WINDOWS
    arms = "\n".join(
        f"            WHEN {column} BETWEEN {lo} AND {hi} THEN '{name}'"
        for name, (lo, hi) in windows.items()
    )
    return "CASE\n" + arms + "\n        END"


# ===========================================================================
# Figures, palette and small-cell suppression
#
# One implementation of suppression, shared by every notebook. The point is not
# convenience: a second copy is a second chance to get the threshold wrong, and the
# rule that a "_suppressed" filename can only be written by the suppressing code path
# only holds if there is exactly one such path.
# ===========================================================================

# All of Us Data and Statistics Dissemination Policy, historical threshold. CONFIRM
# against current policy before anything is published -- the threshold is also not the
# whole policy, which additionally covers small subgroups and variable combinations.
SUPPRESS_THRESHOLD = 20

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"
GRID = "#e3e2df"

# Categorical hues in FIXED assignment order -- a series keeps its hue when another is
# filtered out, and the order is never cycled. Validated on the #fcfcfb surface for the
# lightness band, chroma floor, all-pairs CVD separation, the normal-vision floor and
# 3:1 contrast. Adding a fifth hue means re-running that validation, not appending a
# colour that looks different.
CATEGORICAL = ["#2a78d6", "#c2620a", "#00915f", "#b5309a"]
SERIES = CATEGORICAL[0]

# Status colours are reserved and never reused as "series 5". Red/amber/green is not
# separable under deuteranopia by construction, so anything wearing one of these MUST
# also carry its verdict as text -- see verdict_label().
STATUS = {"good": "#1f7a3f", "warning": "#a86a00", "bad": "#b4261e"}


def verdict_label(status: str) -> str:
    """Return the text label that must accompany a STATUS colour, never colour alone."""
    return {"good": "PASS", "warning": "MARGINAL", "bad": "FAIL"}[status]


def collapse_small_cells(df, count_col: str, label_col: str,
                         threshold: int = SUPPRESS_THRESHOLD,
                         other_label: str = "other (aggregated)"):
    """Aggregate every row under `threshold` into one row, and report what moved.

    Returns (kept_df, n_absorbed, absorbed_count). Rows at or above the threshold are
    returned unchanged and in their original order; the rest are summed into a single
    appended row. If that aggregate is itself still under the threshold it is dropped
    entirely rather than shown, because a visible "other" bar of 11 patients discloses
    just as much as the cells it replaced.

    Aggregating rather than masking is deliberate: a masked cell whose neighbours and
    total are both published is recoverable by subtraction, and a cumulative curve drawn
    over masked data leaks the hidden heights through its step sizes.
    """
    small = df[count_col] < threshold
    kept = df.loc[~small].copy()
    absorbed = df.loc[small]

    if absorbed.empty:
        return kept, 0, 0

    absorbed_count = int(absorbed[count_col].sum())
    if absorbed_count < threshold:
        return kept, len(absorbed), 0

    row = {c: None for c in df.columns}
    row[label_col] = other_label
    row[count_col] = absorbed_count
    import pandas

    kept = pandas.concat([kept, pandas.DataFrame([row])], ignore_index=True)
    return kept, len(absorbed), absorbed_count


def min_cell(df, count_col: str) -> int:
    """Smallest non-zero count in `count_col` -- what decides if a table may be shared."""
    values = [int(v) for v in df[count_col] if v and v > 0]
    return min(values) if values else 0


def style_axes(*axes, grid_axis: str = "y") -> None:
    """Apply the recessive grid, hidden top/right spines and muted ticks to each axis."""
    for ax in axes:
        ax.set_facecolor(SURFACE)
        if grid_axis:
            ax.grid(axis=grid_axis, color=GRID, linewidth=1, zorder=0)
            ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.tick_params(colors=INK_MUTED, labelsize=9)



# ===========================================================================
# Schema validation against the pinned CDR
#
# A wrong table or column name is the cheapest bug to prevent and one of the more
# expensive to hit: BigQuery rejects it only after the query is composed, hundreds of
# lines into a run, with a message that names a line number in generated SQL. The CDR's
# schema is pinned in cdr_schema.py, so the same mistake can be caught at init() for
# free.
# ===========================================================================


def _schema():
    """Return (SCHEMA, CDR) from cdr_schema.py, or (None, None) if absent.

    Optional by design: a fresh clone without the generated file still runs, it just
    does not get the check. Missing validation must never be a hard failure -- that
    would make the safety net a liability.
    """
    try:
        import cdr_schema
    except ImportError:
        return None, None
    return getattr(cdr_schema, "SCHEMA", None), getattr(cdr_schema, "CDR", None)


def require_columns(requirements: dict, cdr: str | None = None) -> None:
    """Raise if the pinned schema lacks any of `{table: [columns]}`.

    Reports every missing name at once rather than the first, because fixing them one
    BigQuery round-trip at a time is the thing this exists to avoid. For a missing
    column it suggests near-misses from the same table, which is usually enough to spot
    a rename.

    A schema generated against a different CDR than the one being queried is worse than
    no schema, so that mismatch warns loudly and skips the check rather than validating
    against the wrong release.
    """
    schema, schema_cdr = _schema()
    if schema is None:
        print("Note: cdr_schema.py not found, so column names are unvalidated. "
              "Regenerate it to get this check.")
        return
    if cdr and schema_cdr and cdr != schema_cdr:
        print(f"WARNING: cdr_schema.py was generated against {schema_cdr}, but this "
              f"notebook queries {cdr}. Skipping validation -- regenerate the schema.")
        return

    problems = []
    for table, columns in sorted(requirements.items()):
        if table not in schema:
            near = [t for t in schema if table.split("_")[0] in t][:6]
            problems.append(f"  table not in the CDR: {table}"
                            + (f"\n      similar tables: {', '.join(near)}" if near else ""))
            continue
        for column in columns:
            if column not in schema[table]:
                stem = column.split("_")[0]
                near = [c for c in schema[table] if stem in c or c in column][:6]
                problems.append(f"  column not in {table}: {column}"
                                + (f"\n      similar columns: {', '.join(near)}" if near else ""))

    if problems:
        raise RuntimeError(
            "Schema check failed against the pinned CDR "
            f"({schema_cdr}):\n" + "\n".join(problems) +
            "\n\nEither the name is wrong, or the CDR moved and cdr_schema.py is stale. "
            "Regenerate cdr_schema.py before assuming the name is wrong."
        )


def column_type(table: str, column: str) -> str | None:
    """Data type of `table.column` in the pinned schema, or None if unknown.

    Useful where a type decides the SQL -- a STRING flag needs CAST before comparison,
    a BOOL does not.
    """
    schema, _ = _schema()
    if not schema:
        return None
    return (schema.get(table, {}).get(column) or (None,))[0]


# Tables cohort_ctes itself reads. Validated on every call, so a notebook gets the
# check without having to remember to ask for it.
COHORT_TABLES = {
    "concept_ancestor": ["ancestor_concept_id", "descendant_concept_id"],
    "procedure_occurrence": ["person_id", "procedure_concept_id", "procedure_datetime"],
}

# ===========================================================================
# The index hospitalisation
#
# Four of the EHR outcome metrics -- length of stay, readmission, days alive and out
# of hospital, discharge disposition -- all hang off the same object: the inpatient
# stay the index surgery happened during. Defining it four times is four chances for
# them to disagree about which stay that was, which would make the metrics
# incomparable in exactly the notebook meant to compare them.
# ===========================================================================

# OMOP standard visit concepts: 9201 "Inpatient Visit", 262 "Emergency Room and
# Inpatient Visit". Day-surgery cases appear as outpatient and are deliberately not
# included -- they have no stay to measure, and their absence is itself a coverage
# finding rather than something to paper over.
INPATIENT_VISIT_CONCEPT_IDS = (9201, 262)


def index_visit_cte(cfg: "Session") -> str:
    """CTE 'index_visit': the inpatient stay containing each patient's index date.

    Pass this to cohort_ctes(extra=...). Patients may have several qualifying stays
    -- overlapping records are common in claims-derived EHR data -- so `rn` ranks them
    longest-first and every downstream query filters `rn = 1`. Ties break on the
    earlier start date, so the choice is deterministic across runs and the query cache
    stays valid.

    A patient whose index surgery falls in no inpatient stay simply does not appear
    here, which is what makes the row count of this CTE the coverage denominator for
    every stay-based metric.
    """
    ids = ", ".join(str(i) for i in INPATIENT_VISIT_CONCEPT_IDS)
    return f"""
    index_visit AS (
        SELECT
            i.person_id,
            i.index_date,
            vo.visit_occurrence_id,
            vo.visit_start_date,
            vo.visit_end_date,
            DATE_DIFF(vo.visit_end_date, vo.visit_start_date, DAY) AS los_days,
            DATE_DIFF(vo.visit_end_date, i.index_date, DAY) AS postop_los_days,
            ROW_NUMBER() OVER (
                PARTITION BY i.person_id
                ORDER BY DATE_DIFF(vo.visit_end_date, vo.visit_start_date, DAY) DESC,
                         vo.visit_start_date
            ) AS rn
        FROM index_surgery AS i
        JOIN `{cfg.CDR}`.visit_occurrence AS vo
          ON vo.person_id = i.person_id
         AND i.index_date BETWEEN vo.visit_start_date
                              AND COALESCE(vo.visit_end_date, vo.visit_start_date)
        WHERE vo.visit_concept_id IN ({ids})
          AND vo.visit_end_date IS NOT NULL
    )"""


# ===========================================================================
# Scorecard hand-off between notebooks
#
# The point of an audit is the comparison, and the comparison needs every metric's
# verdict in one place. Each notebook writes its own one-row summary into the shared
# task cache; the comparison notebook reads whatever is there. Aggregates only -- a
# scorecard row is counts and fractions, never participant-level values.
# ===========================================================================

SCORECARD_PREFIX = "scorecard_"


def write_scorecard(cfg: "Session", **fields):
    """Write this notebook's one-row audit summary into the task cache.

    Keyed by notebook stem, so re-running a notebook replaces its own row and cannot
    duplicate it. The CDR and the index rule are stamped in automatically, because a
    scorecard compared across different values of those would be comparing different
    analyses.
    """
    import pandas

    row = {
        "metric": cfg.notebook,
        "CDR": cfg.CDR,
        **fields,
    }
    path = cfg.CACHE_DIR / f"{SCORECARD_PREFIX}{cfg.notebook}.parquet"
    frame = pandas.DataFrame([row])
    frame.to_parquet(path, index=False)
    print(f"scorecard -> {path.name}")
    return frame


def read_scorecards(cfg: "Session"):
    """Read every scorecard in the task cache, newest write order irrelevant.

    Raises if the rows disagree about the CDR or the cohort index, since a comparison
    across two data releases is not a comparison -- it is a bug that would otherwise
    produce a plausible-looking table.
    """
    import pandas

    paths = sorted(cfg.CACHE_DIR.glob(f"{SCORECARD_PREFIX}*.parquet"))
    if not paths:
        raise RuntimeError(
            f"No scorecards in {cfg.CACHE_DIR}/. Run the per-metric notebooks first; "
            "each writes its row at the end of its verdict section."
        )
    frame = pandas.concat([pandas.read_parquet(p) for p in paths], ignore_index=True)

    for column in ("CDR",):
        distinct = sorted(frame[column].dropna().unique())
        if len(distinct) > 1:
            raise RuntimeError(
                f"Scorecards disagree on {column}: {distinct}. Re-run the stale "
                "notebooks against the current pinned datasets before comparing."
            )

    return frame.sort_values("metric").reset_index(drop=True)


# ===========================================================================
# Shared plotters
#
# Two shapes cover almost every question in the outcome audit: "how many patients
# have data on day N relative to surgery" and "how many patients fall in each
# bucket". Both are defined here so that suppression happens in one place and a
# figure's committability is decided by the same code that drew it.
# ===========================================================================


def _new_figure(height=4.6):
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(10, height), facecolor=SURFACE)
    style_axes(ax)
    return fig, ax


def plot_rel_day_curve(cfg, df, value_col, count_col="n_patients", band=None,
                       title="", ylabel="", suppress=False, save_as=None,
                       show_windows=True, note="", zero_line=False):
    """Plot `value_col` against day relative to the index surgery.

    `band` is an optional (low_col, high_col) pair drawn as a shaded interquartile
    ribbon behind the line. `zero_line` draws a horizontal rule at y=0, which a curve
    of differences needs to be readable at all -- without it there is no way to see
    which side of no-change the line is on.

    Suppression drops any day whose `count_col` is under the threshold rather than
    aggregating it, which is correct here because no total is published alongside --
    there is nothing for a reader to recover the dropped days by subtraction from.

    Returns (fig, n_days_dropped) so the notebook can report how much of the curve
    the suppressed version is missing.
    """
    import matplotlib.pyplot as plt

    d = df.sort_values("rel_day").reset_index(drop=True)
    dropped = 0
    if suppress:
        keep = d[count_col] >= SUPPRESS_THRESHOLD
        dropped = int((~keep).sum())
        d = d.loc[keep].reset_index(drop=True)

    fig, ax = _new_figure()

    if show_windows:
        for i, (name, (lo, hi)) in enumerate(DEFAULT_WINDOWS.items()):
            ax.axvspan(lo, hi, color=GRID, alpha=0.45 if i % 2 == 0 else 0.22,
                       linewidth=0, zorder=0)
            ax.text((lo + hi) / 2, 1.005, name, transform=ax.get_xaxis_transform(),
                    ha="center", va="bottom", fontsize=8, color=INK_MUTED)

    ax.axvline(0, color=INK_MUTED, linewidth=1, linestyle="--", zorder=2)
    if zero_line:
        ax.axhline(0, color=INK_MUTED, linewidth=1, linestyle=":", zorder=2)

    if band:
        lo_col, hi_col = band
        ax.fill_between(d["rel_day"], d[lo_col], d[hi_col], color=SERIES,
                        alpha=0.18, linewidth=0, zorder=2)
    ax.plot(d["rel_day"], d[value_col], color=SERIES, linewidth=2, zorder=3)

    ax.set_title(title, fontsize=13, fontweight="600", color=INK, loc="left", pad=18)
    ax.set_xlabel("days relative to index surgery", fontsize=10, color=INK_MUTED)
    ax.set_ylabel(ylabel or value_col, fontsize=10, color=INK_MUTED)
    ax.set_xlim(d["rel_day"].min(), d["rel_day"].max())

    full_note = note
    if suppress and dropped:
        full_note = (f"{dropped} day(s) below {SUPPRESS_THRESHOLD} patients omitted"
                     + (f"  |  {note}" if note else ""))
    if save_as:
        cfg.save_figure(fig, save_as, suppress, full_note)
    else:
        fig.text(0.01, 0.005, full_note, fontsize=8, color=INK_MUTED)
    return fig, dropped


def plot_distribution(cfg, df, label_col, count_col="n_patients", title="",
                      xlabel="", suppress=False, save_as=None, logy=False, note=""):
    """Bar chart of patient counts per bucket, with the sparse tail aggregated.

    When `suppress` is set the under-threshold buckets are summed into one visible
    "other" bar rather than hidden, so the displayed total still reconciles and no
    individual small cell is recoverable. Returns (fig, n_buckets_absorbed).
    """
    import matplotlib.pyplot as plt

    d = df.reset_index(drop=True)
    absorbed = 0
    if suppress:
        d, absorbed, absorbed_count = collapse_small_cells(d, count_col, label_col)
        d = d.reset_index(drop=True)

    labels = [str(v) for v in d[label_col]]
    heights = [int(v) for v in d[count_col]]

    if not labels:
        # Every bucket fell under the threshold and was dropped. Drawing an empty axis
        # would read as "no patients" rather than "nothing may be shown", so say so.
        raise ValueError(
            f"Nothing left to plot: every bucket is below the {SUPPRESS_THRESHOLD}-"
            "participant threshold. Widen the buckets before plotting a suppressed figure."
        )
    longest_label = max(len(label) for label in labels)

    fig, ax = _new_figure(height=max(4.6, 0.32 * len(labels) + 2.4)
                          if len(labels) > 14 else 4.6)
    xs = range(len(labels))
    bars = ax.bar(xs, heights, width=0.78, color=SERIES, linewidth=0, zorder=3)
    if logy:
        ax.set_yscale("log")

    ax.set_title(title, fontsize=13, fontweight="600", color=INK, loc="left", pad=10)
    ax.set_xlabel(xlabel or label_col, fontsize=10, color=INK_MUTED)
    ax.set_ylabel("patients", fontsize=10, color=INK_MUTED)
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels, rotation=45 if longest_label > 6 else 0,
                       ha="right" if longest_label > 6 else "center")
    for rect, height in zip(bars, heights):
        ax.text(rect.get_x() + rect.get_width() / 2, rect.get_height(), f"{height:,}",
                ha="center", va="bottom", fontsize=8.5, color=INK_MUTED)

    full_note = note
    if suppress and absorbed:
        full_note = (f"{absorbed} bucket(s) aggregated into 'other'"
                     + (f"  |  {note}" if note else ""))
    if save_as:
        cfg.save_figure(fig, save_as, suppress, full_note)
    else:
        fig.text(0.01, 0.005, full_note, fontsize=8, color=INK_MUTED)
    return fig, absorbed
