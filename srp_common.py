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
    CDR, CDR_INDEX = cfg.CDR, cfg.CDR_INDEX
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
    "resolve_cdr",
    "Session",
    "discover_cohort_index",
    "init",
    "list_cdr_datasets",
    "resolve_cdr",
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
    """List datasets in the workspace project that look like a CDR or cohort index.

    Use this to find the value for cdr_config.PINNED_CDR. The project is taken from
    Application Default Credentials when not given, since Verily Workbench does not
    set GOOGLE_CLOUD_PROJECT.
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


def discover_cohort_index(cdr: str, verbose: bool = True) -> str:
    """Return the newest cohort-builder index dataset for `cdr`.

    The Workbench cohort builder writes a per-build dataset '<CDR>_index_<suffix>'
    holding the T_ENT_* / T_HAD_* / T_ESA_* cohort tables. It has no environment
    variable and changes when the COHORT is rebuilt -- a different trigger from a
    CDR release -- so it is discovered rather than hard-coded, and a rebuild
    therefore changes the cache key and forces a re-query.

    Ordered by creation_time, which is chronologically correct regardless of how
    the name suffix happens to be formatted.
    """
    import pandas_gbq

    project, _, dataset = cdr.partition(".")
    sql = f"""
        SELECT schema_name, creation_time
        FROM `{project}`.INFORMATION_SCHEMA.SCHEMATA
        WHERE STARTS_WITH(schema_name, '{dataset}_index_')
        ORDER BY creation_time DESC
    """
    candidates = pandas_gbq.read_gbq(sql, dialect="standard", progress_bar_type=None)

    if candidates.empty:
        raise RuntimeError(
            f"No cohort index dataset found starting with '{dataset}_index_' in "
            f"project '{project}'. Build the cohort in the Workbench cohort builder, "
            f"or set WORKSPACE_CDR_INDEX explicitly."
        )

    if verbose and len(candidates) > 1:
        print(f"Cohort index: {len(candidates)} builds found, using the newest:")
        for _, row in candidates.iterrows():
            print(f"    {row['schema_name']:42s} created {row['creation_time']}")

    return f"{project}.{candidates.iloc[0]['schema_name']}"


@dataclass
class Session:
    """Resolved dataset names and paths for one notebook, plus its cached reader."""

    notebook: str
    CDR: str
    CDR_INDEX: str
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
        """Hash the query together with both dataset names.

        Including CDR and CDR_INDEX is what makes a CDR release or a cohort
        rebuild miss the cache instead of serving a stale extract.
        """
        return hashlib.sha256(
            "|".join([sql, self.CDR, self.CDR_INDEX]).encode()
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
         cdr: str | None = None, cdr_index: str | None = None) -> Session:
    """Resolve datasets, create the notebook's git-ignored output dirs, and report.

    `notebook` is the notebook's filename stem; its figure folder is named
    '<notebook>_figures/' per the repo layout convention. `reader` overrides the
    BigQuery reader, for tests. `cdr` / `cdr_index` override resolution and
    discovery respectively -- escape hatches for a workspace whose variable names
    are not yet known.
    """
    cdr_source, cdr = resolve_cdr(cdr)
    cdr_index = (
        cdr_index
        or os.environ.get("WORKSPACE_CDR_INDEX")
        or _pinned("PINNED_COHORT_INDEX")
        or discover_cohort_index(cdr, verbose=verbose)
    )

    figures = Path(f"{notebook}_figures")
    cache_dir = Path("_cache")
    figures.mkdir(exist_ok=True)
    cache_dir.mkdir(exist_ok=True)

    session = Session(
        notebook=notebook, CDR=cdr, CDR_INDEX=cdr_index, CDR_SOURCE=cdr_source,
        FIGURES=figures, CACHE_DIR=cache_dir, _reader=reader,
    )

    if verbose:
        print(f"CDR         : {cdr}   (from {cdr_source})")
        print(f"Cohort index: {cdr_index}")
        print(f"Figures     : {figures}/")
        print(f"Cache       : {cache_dir}/")

    return session
