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
    "Session",
    "discover_cohort_index",
    "init",
    "resolve_cdr",
]

CDR_ENV_CANDIDATES = ("WORKSPACE_CDR", "CDR_DATASET", "WORKSPACE_CDR_DATASET")


def resolve_cdr() -> tuple[str, str]:
    """Return (env var name, CDR dataset) from the workspace environment.

    Never falls back to a literal project ID: a wrong-but-plausible dataset would
    fail silently, so this raises with instructions instead.
    """
    for var in CDR_ENV_CANDIDATES:
        value = os.environ.get(var)
        if value:
            return var, value
    raise RuntimeError(
        "Could not resolve the CDR dataset from the environment. Tried: "
        + ", ".join(CDR_ENV_CANDIDATES)
        + ". Set the variable this workspace uses (check os.environ for a key "
          "containing 'CDR'), or export it before starting the kernel. "
          "Do not hard-code the project ID."
    )


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
    CDR_ENV_VAR: str
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


def init(notebook: str, verbose: bool = True, reader: Callable | None = None) -> Session:
    """Resolve datasets, create the notebook's git-ignored output dirs, and report.

    `notebook` is the notebook's filename stem; its figure folder is named
    '<notebook>_figures/' per the repo layout convention. `reader` overrides the
    BigQuery reader, for tests.
    """
    cdr_env_var, cdr = resolve_cdr()
    cdr_index = os.environ.get("WORKSPACE_CDR_INDEX") or discover_cohort_index(
        cdr, verbose=verbose
    )

    figures = Path(f"{notebook}_figures")
    cache_dir = Path("_cache")
    figures.mkdir(exist_ok=True)
    cache_dir.mkdir(exist_ok=True)

    session = Session(
        notebook=notebook, CDR=cdr, CDR_INDEX=cdr_index, CDR_ENV_VAR=cdr_env_var,
        FIGURES=figures, CACHE_DIR=cache_dir, _reader=reader,
    )

    if verbose:
        print(f"CDR         : {cdr}   (from ${cdr_env_var})")
        print(f"Cohort index: {cdr_index}")
        print(f"Figures     : {figures}/")
        print(f"Cache       : {cache_dir}/")

    return session
