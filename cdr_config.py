"""Pinned dataset configuration for SurgicalRecoveryPrediction.

Verily Workbench does not expose the CDR through the environment -- there is no
WORKSPACE_CDR and no GOOGLE_CLOUD_PROJECT -- so the dataset is pinned here instead.

Pinning is deliberate, not a workaround. Auto-selecting the newest CDR would change
results underneath a finished analysis when All of Us publishes a release. Bump these
values as an explicit, reviewable commit when you intend to move to a new release, and
re-run affected notebooks (the query cache keys include the CDR, so they will miss).

A dataset identifier is configuration, not participant data, so it is safe to commit
under the DUA -- and committing it is what makes an analysis reproducible.

Run srp_common.list_cdr_datasets() to see what is available in the workspace project.
"""

# The curated data repository: "<project>.<dataset>".
# Taken from the Workbench cohort-builder SQL for cohort CardiacSurgxFitbit
# (dataset AllOfUsControlledTierDatasetv9, built 2026-09-09).
PINNED_CDR = "wb-silky-artichoke-2408.C2025Q4R6"

# The cohort-builder index dataset holding the T_ENT_* / T_HAD_* / T_ESA_* tables.
# None means "discover the most recently created one", which is usually what you want:
# it changes on cohort REBUILD, not on CDR release. Pin it to freeze a specific build.
PINNED_COHORT_INDEX = None
