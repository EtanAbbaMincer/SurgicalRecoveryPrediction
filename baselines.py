"""The four individual wearable baselines, and the parameters each one was settled at.

A baseline here is **a patient's own normal**, against which a day near their surgery is read.
Every curve in `outcomes/` that is not read against one is cross-sectional: it moves whenever the
set of people wearing a device that day moves, so a peri-operative dip can be the arrival of
patients who sleep badly rather than anyone sleeping worse. `outcomes/fitbit_sleep.ipynb` already
answers that with one baseline -- a single pre-operative reference median per patient. This module
is the same idea carried to four shapes, so which shape is used stops being an accident of
whichever notebook was written first.

Every function here takes a **person-day grid**: one row per `rel_day`, one column per person,
holding the measure on the days its rule admits and nothing anywhere else. `eligible_value_grid`
builds one. A grid row is a calendar day rather than an observed day, so a trailing window spans
the days it claims rather than the last N days that happened to carry data.

**The settled values below were settled somewhere else.** They come from a wearable sweep against
a cancer-diagnosis index, not against this cohort and not against surgery, so they are a starting
point with provenance rather than a result of this project. What carries over is the SHAPE of each
baseline and the fact that each parameter is a knob rather than a constant; what does not carry
over is any claim that 180, 30, 14, 30, 0.5, 14, 60 or 10.4 are right for a surgical cohort. A
notebook that sweeps them against this cohort is what would settle them here, and until one exists
every number in this file should be read as inherited.

`rel_day` 0 is the day of surgery and belongs to neither side, matching
`srp_common.DEFAULT_WINDOWS`. Note that the static lookback reaches to day -193 and the long
comparative window to day -75, both far outside that 30-day baseline window: an analysis window is
the span a result is reported over, while a lookback is the history a baseline is built from, and
they are deliberately not the same span.

Nothing here resolves a path, reads a global or touches BigQuery, so a change reaches a running
kernel only after a `git pull` and an `importlib.reload`. Grids hold participant-level values, so
they stay inside the workspace: only aggregates over them may be printed or plotted, which is what
`daily_summary`, `coverage_by_baseline` and `baseline_agreement` are for.
"""

import math

import numpy as np
import pandas as pd

# --- the four baselines, at the values inherited from the sweeps --------------------------------
# Static lookback: one frozen value per person, from a window of STATIC_LOOKBACK_DAYS days that
# ends STATIC_DAYS_PRIOR days before the surgery and needs STATIC_MIN_ELIGIBLE_DAYS eligible days
# inside it. Written (n, m, x).
STATIC_LOOKBACK_DAYS = 180        # n, days of history the window spans
STATIC_MIN_ELIGIBLE_DAYS = 30     # m, eligible days required inside them
STATIC_DAYS_PRIOR = 14            # x, days before the surgery the window ends

# Rolling window: a baseline per person per day, from one trailing window. Written (w, g, k).
ROLLING_WINDOW_DAYS = 30          # w, days of history the trailing window spans
ROLLING_GAP_DAYS = 1              # g, days between the window's end and the day it scores
ROLLING_MAX_MISSING = 0.50        # k, share of w allowed to be MISSING, not the share required

# Comparative: two trailing windows at once. The long window is the baseline and the short one is
# held against it, as (C_S - C_L) / D_L. g and k carry over from the rolling window and apply to
# both sides.
SHORT_SPAN_DAYS = 14              # S, the recent stretch
LONG_SPAN_DAYS = 60               # L, the established normal
SPAN_COUPLING = "uncoupled"       # the long window sits a further S days back, so S is not in L
BASELINE_CENTRE = "median"        # both centres are medians, and D_L is the long window's IQR

# Exponential window: the same trailing idea as the rolling window with the flat cutoff removed.
# Weight decays geometrically with age instead of being 1 inside w days and 0 outside it, so no
# day ever leaves the window and steps the baseline on its way out.
#
# EWMA_HALFLIFE_DAYS is set to match the flat window rather than chosen freely: at 10.4 days the
# exponential weights have the same centre of mass as ROLLING_WINDOW_DAYS=30 after the g=1 shift
# (mean lag 15.5 days) and the same Kish effective sample size (30.0). That makes the two a
# like-for-like pair, which is the only comparison that says anything about the CUTOFF rather
# than about how far back each one looks.
EWMA_HALFLIFE_DAYS = 10.4         # h, days for a day's weight to halve
# k carries over from the rolling window and is applied to the exponential window's CAPACITY
# rather than to a day count; see ewma_min_effective_days, which is where the floor comes from.


def days_required(window_days: int, max_missing: float, floor: int = 1) -> int:
    """How many of a window's days must be eligible, given the share allowed to be missing.

    `floor` is 1 for a centre and 2 for a spread, because a median of no days is not a number and
    an IQR of one day is not a spread.
    """
    return max(floor, math.ceil((1 - max_missing) * window_days))


# The settled floors, named so a caller can print what a default actually resolved to.
ROLLING_MIN_DAYS = days_required(ROLLING_WINDOW_DAYS, ROLLING_MAX_MISSING)


def grid_first_day(first_reported_day: int, days_prior: int = STATIC_DAYS_PRIOR,
                   lookback_days: int = STATIC_LOOKBACK_DAYS,
                   long_days: int = LONG_SPAN_DAYS, short_days: int = SHORT_SPAN_DAYS,
                   gap_days: int = ROLLING_GAP_DAYS, window_days: int = ROLLING_WINDOW_DAYS,
                   coupling: str = SPAN_COUPLING) -> int:
    """The earliest `rel_day` a grid must carry for all four baselines to exist on
    `first_reported_day`.

    **Two different reaches are at work and they do not move together.** The static window is
    anchored to the surgery, so it sits at a fixed place on the rel_day axis however wide the
    reported span is; the trailing windows are anchored to the day they score, so their reach is
    measured back from the first day reported and moves with it. Taking the minimum of the two is
    what covers both.

    A notebook that pulls a shorter extract does not fail -- it silently gets an all-null static
    baseline and a truncated comparative one -- so it asks for this rather than hard-coding a
    number that stops matching when a parameter moves.
    """
    static_start = -(days_prior + lookback_days - 1)
    long_shift = gap_days if coupling == "coupled" else gap_days + short_days
    trailing_reach = max(long_shift + long_days - 1, gap_days + window_days - 1)
    return min(static_start, first_reported_day - trailing_reach)


# --- building the grid --------------------------------------------------------------------------
def eligible_value_grid(days: pd.DataFrame, value_column: str, eligibility_column: str,
                        first_day: int, last_day: int) -> pd.DataFrame:
    """One row per rel_day in `[first_day, last_day]`, one column per person, holding the measure
    on that domain's eligible days and nothing anywhere else.

    Every day in the range is a row, including days a person carries no data for, so a rolling
    window below spans calendar days rather than observed days. A person with no eligible day at
    all has no column, which keeps them out of the numerator and the denominator alike.

    `first_day` reaches further back than the window being reported, because a baseline at the
    reported window's far edge needs its own lookback behind it -- `required_lookback_days` says
    how much.
    """
    eligible = days.loc[days[eligibility_column], ["person_id", "rel_day", value_column]]
    grid = eligible.pivot(index="rel_day", columns="person_id", values=value_column)
    return grid.reindex(range(first_day, last_day + 1))


def flatten(grid: pd.DataFrame) -> pd.Series:
    """Every value in a person-day grid as one series, with the absent person-days dropped."""
    return pd.Series(grid.to_numpy().ravel()).dropna()


def flatten_pair(left: pd.DataFrame, right: pd.DataFrame) -> tuple:
    """Two person-day grids as two aligned series, keeping only the cells both of them hold.

    `flatten` drops each grid's own absences, which would leave two series of different lengths
    in a different order, so anything comparing two grids cell by cell has to come through here.
    """
    both = left.notna() & right.notna()
    return (pd.Series(left.where(both).to_numpy().ravel()).dropna(),
            pd.Series(right.where(both).to_numpy().ravel()).dropna())


def daily_summary(grid: pd.DataFrame) -> pd.DataFrame:
    """A person-day grid reduced to one row per rel_day: median, quartiles and contributor count.

    This is the boundary between participant-level and reportable -- every figure and every printed
    number a notebook produces comes through here, so nothing leaving the grid is one participant's
    own value. `n_patients` is the column the small-cell threshold is applied to, and it counts the
    people who actually contributed to that day's median rather than the cohort.
    """
    return pd.DataFrame({
        "rel_day": grid.index,
        "n_patients": grid.notna().sum(axis=1).to_numpy(),
        "p25": grid.quantile(0.25, axis=1).to_numpy(),
        "p50": grid.median(axis=1).to_numpy(),
        "p75": grid.quantile(0.75, axis=1).to_numpy(),
    })


# --- baseline 1, the static pre-operative lookback -----------------------------------------------
def static_baseline(value_grid: pd.DataFrame, lookback_days: int = STATIC_LOOKBACK_DAYS,
                    min_days: int = STATIC_MIN_ELIGIBLE_DAYS,
                    days_prior: int = STATIC_DAYS_PRIOR,
                    statistic: str = BASELINE_CENTRE) -> pd.Series:
    """One frozen value per person, taken from the fixed pre-operative window.

    The window is `rel_day` `-(days_prior + lookback_days - 1)` through `-days_prior`, so it ends
    `days_prior` days before the surgery and never sees the operative day itself. A person holding
    fewer than `min_days` eligible days in it gets no baseline rather than one built from whatever
    few days they had, which is what keeps this comparable across people.

    `days_prior` carries more weight at a surgical index than at a diagnosis one: the fortnight
    before surgery is pre-operative preparation, anxiety and a condition already bad enough to
    operate on, so a window running up to day -1 measures that rather than a healthy normal.

    One entry per person in the grid, so a person who missed the bar is NaN here rather than
    absent, and a later comparison keeps the same set of columns throughout.
    """
    window_end = -days_prior
    window_start = window_end - lookback_days + 1
    in_window = value_grid.loc[window_start:window_end]
    centre = in_window.mean() if statistic == "mean" else in_window.median()
    return centre.where(in_window.notna().sum() >= min_days)


def static_deviation(value_grid: pd.DataFrame, baseline: pd.Series = None,
                     **kwargs) -> pd.DataFrame:
    """Each eligible person-day minus that person's frozen baseline, in the measure's own units.

    A person with no baseline yields an all-null column rather than being dropped, so the grid
    keeps its shape and every count below reads against the same set of people.
    """
    if baseline is None:
        baseline = static_baseline(value_grid, **kwargs)
    return value_grid.sub(baseline.reindex(value_grid.columns), axis="columns")


# --- baseline 2, the rolling trailing window ----------------------------------------------------
def rolling_baseline(value_grid: pd.DataFrame, window_days: int = ROLLING_WINDOW_DAYS,
                     gap_days: int = ROLLING_GAP_DAYS,
                     min_eligible_days: int = ROLLING_MIN_DAYS,
                     statistic: str = BASELINE_CENTRE) -> pd.DataFrame:
    """Each person-day's trailing baseline over the eligible days in the window that ends
    `gap_days` before it and reaches `window_days` further back.

    A window holding fewer than `min_eligible_days` eligible days returns nothing, so that day's
    comparison is dropped rather than filled from a neighbour or from the cohort. The two centres
    are both offered because a wear-day distribution is skewed: a marathon or a device glitch
    moves a mean and leaves a median where it was.

    Trailing through the operative period is the trade this baseline makes. From a few days after
    surgery its window is already partly post-operative, so it tracks the recovery and reports a
    smaller departure than the static one -- the right shape for "is today worse than the recent
    past" and the wrong shape for "how far is this patient from their old normal".
    """
    trailing = value_grid.rolling(window=window_days, min_periods=min_eligible_days)
    centre = trailing.mean() if statistic == "mean" else trailing.median()
    return centre.shift(gap_days)


def rolling_eligible_days(value_grid: pd.DataFrame, window_days: int = ROLLING_WINDOW_DAYS,
                          gap_days: int = ROLLING_GAP_DAYS) -> pd.DataFrame:
    """How many eligible days sit in each person-day's trailing window, on the same alignment."""
    in_window = value_grid.notna().rolling(window=window_days, min_periods=1).sum()
    return in_window.shift(gap_days)


def rolling_deviation(value_grid: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Each eligible person-day minus its own trailing baseline, in the measure's own units."""
    return value_grid - rolling_baseline(value_grid, **kwargs)


# --- baseline 3, the exponential trailing window -------------------------------------------------
def ewma_weight_sums(value_grid: pd.DataFrame, halflife_days: float = EWMA_HALFLIFE_DAYS,
                     gap_days: int = ROLLING_GAP_DAYS) -> tuple:
    """The sum of the exponential weights, and the sum of their squares, per person-day.

    Together these give the effective sample size behind an EWMA, which is the exponential
    window's answer to "how many of the window's days were actually observed". Both are plain
    linear recursions over the grid's rows, so a day that is absent contributes nothing while the
    decay still advances -- which is what makes the weighting run in CALENDAR time rather than in
    observed-day time.

    Returned on the same alignment as `ewma_baseline`, so a floor applied to one applies to the
    other. Only the day's own row is excluded by `gap_days`; everything before it still counts.
    """
    decay = 0.5 ** (1.0 / halflife_days)
    observed = value_grid.notna().to_numpy(dtype="float64")
    first = np.zeros_like(observed)
    second = np.zeros_like(observed)
    running_first = np.zeros(observed.shape[1])
    running_second = np.zeros(observed.shape[1])
    for row in range(observed.shape[0]):
        running_first = decay * running_first + observed[row]
        running_second = decay ** 2 * running_second + observed[row]
        first[row] = running_first
        second[row] = running_second
    as_frame = lambda values: pd.DataFrame(values, index=value_grid.index,
                                           columns=value_grid.columns).shift(gap_days)
    return as_frame(first), as_frame(second)


def ewma_effective_days(value_grid: pd.DataFrame, halflife_days: float = EWMA_HALFLIFE_DAYS,
                        gap_days: int = ROLLING_GAP_DAYS) -> pd.DataFrame:
    """Kish effective sample size behind each person-day's EWMA, `(sum w)^2 / sum w^2`.

    The exponential window has no day count to threshold on, because every past day contributes
    something, so this is what `min_eligible_days` becomes. A fully observed record sits at
    `(1 + decay) / (1 - decay)` effective days, which at the settled halflife is 30.0 -- the
    same number of days the flat window holds when nothing is missing.

    pandas' own `min_periods` on `ewm` counts observations EVER SEEN and never expires, so it
    would hand back a baseline built from two days a year apart. It is not a substitute for this.
    """
    first, second = ewma_weight_sums(value_grid, halflife_days, gap_days)
    return (first ** 2) / second.where(second > 0)


def ewma_full_effective_days(halflife_days: float = EWMA_HALFLIFE_DAYS) -> float:
    """The effective sample size an EWMA reaches on a fully observed record, `(1 + d) / (1 - d)`.

    This is the exponential window's answer to `w`: how many days it is worth when nothing is
    missing. It SCALES WITH THE HALFLIFE -- 7.0 at h = 2.4, 30.0 at h = 10.4, 90.0 at h = 31.2 --
    which is why a fixed n_eff floor cannot serve a halflife sweep. A floor of 15 sits above the
    entire capacity of any halflife under about 5 days and would admit nothing at all there.
    """
    decay = 0.5 ** (1.0 / halflife_days)
    return (1 + decay) / (1 - decay)


def ewma_min_effective_days(halflife_days: float = EWMA_HALFLIFE_DAYS,
                            max_missing: float = ROLLING_MAX_MISSING) -> float:
    """The n_eff a person-day must clear, as the same share of capacity the flat window demands.

    The exact analogue of `days_required(w, k)`: `k` is the share allowed to be MISSING, so this
    asks for `(1 - k)` of what the window is worth when full. At the settled halflife it is 15.0,
    which is `ROLLING_MIN_DAYS` -- the two rules demand the same thing, which is what makes the
    pair comparable.
    """
    return (1 - max_missing) * ewma_full_effective_days(halflife_days)


# Named so a caller can print what the settled default resolved to, exactly like ROLLING_MIN_DAYS.
EWMA_MIN_EFFECTIVE_DAYS = ewma_min_effective_days()


def ewma_baseline(value_grid: pd.DataFrame, halflife_days: float = EWMA_HALFLIFE_DAYS,
                  gap_days: int = ROLLING_GAP_DAYS,
                  min_effective_days: float = None,
                  statistic: str = "mean") -> pd.DataFrame:
    """Each person-day's exponentially weighted trailing baseline.

    The flat window's one real defect is its edge: a day counts fully at lag `w` and not at all at
    lag `w + 1`, so an outlier LEAVING the window steps the baseline without anything changing in
    the person's behaviour. Here weight decays with age and nothing ever leaves, so that step is
    gone -- measured at 0.06 against the flat window's 3.33 on a single-outlier series.

    **This does not make it uniformly smoother, and it is not a free upgrade.** The newest day
    carries the most weight, so on a noisy record the exponential baseline moves MORE from day to
    day than a flat mean over the same reach; on i.i.d. synthetic data its typical one-day move
    was 2.98% of level against the flat window's 1.77%. It trades reactivity to a day leaving for
    reactivity to a day arriving. Which trade wins depends on how autocorrelated and how
    outlier-prone the measure is, which is a property of the measure and of this cohort rather
    than something the shape settles.

    **Mean only.** There is no exponentially weighted median, so `statistic` exists to refuse a
    request for one rather than to silently serve a mean in its place. That matters because a
    trailing median is what the other three fall back to for a zero-inflated measure, and this
    baseline cannot -- daily steps are zero-inflated, sleep efficiency is not.
    """
    if statistic != "mean":
        raise ValueError(
            f"ewma_baseline has no {statistic!r} because there is no exponentially weighted "
            f"median; pandas' ewm offers mean, var and std only. Use the rolling baseline for a "
            f"median, or set this notebook's BASELINE_STATISTIC to 'mean'.")
    # Derived from the halflife unless a caller names one, because a floor that does not scale
    # with h is a floor that silently empties the whole grid at a short halflife.
    if min_effective_days is None:
        min_effective_days = ewma_min_effective_days(halflife_days)
    centre = value_grid.ewm(halflife=halflife_days, ignore_na=False, adjust=True).mean()
    effective = ewma_effective_days(value_grid, halflife_days, gap_days)
    return centre.shift(gap_days).where(effective >= min_effective_days)


def ewma_deviation(value_grid: pd.DataFrame, **kwargs) -> pd.DataFrame:
    """Each eligible person-day minus its own exponential baseline, in the measure's own units."""
    return value_grid - ewma_baseline(value_grid, **kwargs)


# --- baseline 4, a short window read against a long one ------------------------------------------
def rolling_centre_and_spread(value_grid: pd.DataFrame, window_days: int,
                              min_eligible_days: int, statistic: str = BASELINE_CENTRE) -> tuple:
    """A trailing window's centre and its own spread, unshifted, for one statistic.

    `median` carries the IQR and `mean` the standard deviation, so each centre is paired with the
    spread that belongs to it. The result is not shifted, because the same rolling pass serves
    both the coupled long window and the uncoupled one, which differ only by how far back they sit.
    """
    trailing = value_grid.rolling(window=window_days, min_periods=min_eligible_days)
    if statistic == "mean":
        return trailing.mean(), trailing.std()
    return trailing.median(), trailing.quantile(0.75) - trailing.quantile(0.25)


def comparative_baseline(value_grid: pd.DataFrame, short_days: int = SHORT_SPAN_DAYS,
                         long_days: int = LONG_SPAN_DAYS, gap_days: int = ROLLING_GAP_DAYS,
                         max_missing: float = ROLLING_MAX_MISSING,
                         statistic: str = BASELINE_CENTRE,
                         coupling: str = SPAN_COUPLING) -> pd.DataFrame:
    """The long window's centre alone, which is the level the comparative index reads against.

    `comparative_index` divides by the long window's spread as well, so the index is unitless.
    This returns only `C_L`, in the measure's own unit, which is what a figure asking how the
    baseline itself moves needs to draw.
    """
    long_centre = rolling_centre_and_spread(
        value_grid, long_days, days_required(long_days, max_missing, floor=2), statistic)[0]
    long_shift = gap_days if coupling == "coupled" else gap_days + short_days
    return long_centre.shift(long_shift)


def comparative_index(value_grid: pd.DataFrame, short_days: int = SHORT_SPAN_DAYS,
                      long_days: int = LONG_SPAN_DAYS, gap_days: int = ROLLING_GAP_DAYS,
                      max_missing: float = ROLLING_MAX_MISSING,
                      statistic: str = BASELINE_CENTRE,
                      coupling: str = SPAN_COUPLING) -> pd.DataFrame:
    """The comparative baseline: `(C_S - C_L) / D_L` per person-day, the recent stretch against
    the established normal, in units of one of that person's own long-window spreads.

    Called comparative because the person is compared against themselves at two timescales at
    once, rather than against one frozen or one trailing number.

    The long window IS the baseline, level and scale together, and the short window is what
    replaces the single scored day. That trades latency for noise, so nothing shorter than
    `short_days` is visible here -- a two-day admission cannot appear on this curve at all, which
    is the one thing to know before reading it near day 0. Uncoupled pushes the long window a
    further `short_days` back so the short window's own days are not also part of the normal it is
    read against.

    Both windows need two eligible days whatever `max_missing` allows, because the long window has
    to yield a spread. A person flat across the whole long window has no spread to be read
    against, so that day has no index rather than an infinite one.
    """
    short_centre = rolling_centre_and_spread(
        value_grid, short_days, days_required(short_days, max_missing, floor=2),
        statistic)[0].shift(gap_days)
    long_centre, long_spread = rolling_centre_and_spread(
        value_grid, long_days, days_required(long_days, max_missing, floor=2), statistic)
    long_shift = gap_days if coupling == "coupled" else gap_days + short_days
    spread = long_spread.shift(long_shift)
    return (short_centre - long_centre.shift(long_shift)) / spread.where(spread > 0)


# --- the four together ---------------------------------------------------------------------------
BASELINE_NAMES = ("static", "rolling", "ewma", "comparative")


def baseline_levels(value_grid: pd.DataFrame, statistic: str = BASELINE_CENTRE) -> dict:
    """The four baselines as levels rather than as departures, one person-day grid each.

    The mirror of `deviations_from_each_baseline`: same four names, same shapes, but holding what
    each baseline SAYS a person's normal is rather than how far that day sat from it. All four
    come back in the measure's own unit, comparative included, so unlike the departures they can
    share one axis.

    The static baseline is one frozen number per person, broadcast across the days that person
    actually has, so it draws as the flat line it is without inventing days the person never held.
    """
    frozen = static_baseline(value_grid, statistic=statistic)
    static = pd.DataFrame({person: frozen.get(person, float("nan"))
                           for person in value_grid.columns}, index=value_grid.index)
    return {"static": static.where(value_grid.notna()),
            "rolling": rolling_baseline(value_grid, statistic=statistic),
            "ewma": ewma_baseline(value_grid),
            "comparative": comparative_baseline(value_grid, statistic=statistic)}


def deviations_from_each_baseline(value_grid: pd.DataFrame,
                                  statistic: str = BASELINE_CENTRE) -> dict:
    """How far each person-day sat from its own normal, one person-day grid per baseline.

    **Three of these four share a unit and the fourth does not.** Static, rolling and exponential
    are the day's value minus a level, so they are in the measure's own unit; comparative is a
    difference of two centres divided by a spread, so it is in that patient's own long-window
    IQRs. Plotting all four on one axis would misstate the fourth, which is why a notebook draws
    it separately rather than adding it to the same figure.

    The comparative entry is also not "today minus normal" at all: its numerator is the last
    `SHORT_SPAN_DAYS` days rather than the day itself, so it answers a slower question than the
    other three and lags them by design.
    """
    frozen = static_baseline(value_grid, statistic=statistic)
    return {"static": static_deviation(value_grid, frozen),
            "rolling": rolling_deviation(value_grid, statistic=statistic),
            "ewma": ewma_deviation(value_grid),
            "comparative": comparative_index(value_grid, statistic=statistic)}


def coverage_by_baseline(value_grid: pd.DataFrame, deviations: dict = None,
                         statistic: str = BASELINE_CENTRE) -> pd.DataFrame:
    """What each baseline costs in person-days and in people, as one reportable table.

    A stricter baseline is not free: every eligibility rule above drops the person-days it cannot
    serve, and a rule that halves the analysable cohort has to earn that against whatever it buys.
    Every column is a cohort-level count or share rather than anyone's own value, so this table is
    reportable as it stands, subject to the small-cell threshold on the counts.
    """
    if deviations is None:
        deviations = deviations_from_each_baseline(value_grid, statistic=statistic)
    eligible_days = int(value_grid.notna().to_numpy().sum())
    eligible_people = int((value_grid.notna().sum() > 0).sum())
    rows = []
    for name in BASELINE_NAMES:
        scored = deviations[name].notna()
        rows.append({
            "baseline": name,
            "person_days": int(scored.to_numpy().sum()),
            "share_of_person_days": (float(scored.to_numpy().sum() / eligible_days)
                                     if eligible_days else float("nan")),
            "patients": int((scored.sum() > 0).sum()),
            "share_of_patients": (float((scored.sum() > 0).sum() / eligible_people)
                                  if eligible_people else float("nan")),
        })
    return pd.DataFrame(rows)


def baseline_agreement(deviations: dict, names: tuple = BASELINE_NAMES) -> pd.DataFrame:
    """Pairwise Spearman correlation between two baselines' departures, on the person-days both
    of them score.

    Spearman rather than Pearson because comparative is on a different scale entirely, so only the
    ORDERING of person-days is comparable across a pair. A pair correlating near 1 means the choice
    between them changes nothing downstream that only ranks days, and a pair correlating near 0
    means the choice is the analysis.

    Ranked first and then correlated linearly, which is the definition of Spearman, rather than
    `Series.corr(method="spearman")` -- that route imports scipy, and scipy is not among this
    repo's declared dependencies. The two agree exactly when there are no ties.
    """
    rows = []
    for i, left in enumerate(names):
        for right in names[i + 1:]:
            a, b = flatten_pair(deviations[left], deviations[right])
            rows.append({"pair": f"{left} vs {right}",
                         "person_days": int(len(a)),
                         "spearman": (float(a.rank().corr(b.rank())) if len(a) > 1
                                      else float("nan"))})
    return pd.DataFrame(rows)
