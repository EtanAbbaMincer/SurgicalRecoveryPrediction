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
# Taken from the Workbench cohort-builder SQL for the surgery x Fitbit cohort
# (dataset AllOfUsControlledTierDatasetv9, built 2026-09-09).
PINNED_CDR = "wb-silky-artichoke-2408.C2025Q4R6"

# The cohort-builder index dataset holding the T_ENT_* / T_HAD_* / T_ESA_* tables.
#
# This is PINNED rather than discovered because discovery is not permitted here: the CDR
# lives in an All of Us-managed project (wb-silky-artichoke-2408) that grants read access
# to the curated tables but not 'bigquery.datasets.get', so querying its
# INFORMATION_SCHEMA.SCHEMATA returns 403. Queries bill from a different project
# (wb-fulgent-grape-9642), which does not help -- the metadata lives with the data.
#
# UPDATE THIS when you REBUILD the cohort in the Workbench cohort builder. The trigger is
# a cohort rebuild, not a CDR release. Forgetting means the notebooks keep analysing the
# previous build; the suffix is the build stamp, so a mismatch is visible here.
#
# Set to None to attempt discovery instead, if permissions ever allow it.
PINNED_COHORT_INDEX = "wb-silky-artichoke-2408.C2025Q4R6_index_061026"
