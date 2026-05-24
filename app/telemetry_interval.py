"""Shared math for the tracked-repeater telemetry scheduler.

The app enforces a ceiling of 24 repeater status checks per 24 hours across
all tracked repeaters. With N repeaters tracked, the shortest legal interval
is ``24 // floor(24 / N)`` hours. Longer intervals (``12`` or ``24``) are
always legal at any N and are offered as user choices on top of the derived
shortest-legal value.

The user picks an interval via settings. The scheduler uses
``clamp_telemetry_interval`` to push that pick up to the shortest legal
interval if the user has added repeaters that invalidated their choice.
The stored preference is *not* mutated on clamp — users get their pick back
if they later drop repeaters.
"""

from datetime import UTC, datetime

# Daily check budget: total number of repeater status checks we allow
# across all tracked repeaters per 24-hour window.
DAILY_CHECK_CEILING = 24

# Menu of interval values shown to users. The derivation-based options
# (1..8) are filtered per current repeater count via
# ``legal_interval_options``; 12 and 24 are always legal.
TELEMETRY_INTERVAL_OPTIONS_HOURS: tuple[int, ...] = (1, 2, 3, 4, 6, 8, 12, 24)

DEFAULT_TELEMETRY_INTERVAL_HOURS = 8


def shortest_legal_interval_hours(n_tracked: int) -> int:
    """Return the shortest interval (hours) that keeps under the daily ceiling.

    With ``N`` repeaters, each full cycle costs ``N`` checks. We're capped at
    ``DAILY_CHECK_CEILING`` checks/day, so the maximum cycles/day is
    ``floor(24 / N)`` and the resulting interval is ``24 // cycles_per_day``.
    For ``N == 0`` we return the default so the math still terminates, though
    the scheduler skips empty-tracked cycles regardless.
    """
    if n_tracked <= 0:
        return DEFAULT_TELEMETRY_INTERVAL_HOURS
    cycles_per_day = DAILY_CHECK_CEILING // n_tracked
    if cycles_per_day <= 0:
        # Would exceed ceiling even at 24h cadence; fall back to 24h.
        return 24
    return 24 // cycles_per_day


def clamp_telemetry_interval(preferred_hours: int, n_tracked: int) -> int:
    """Return the effective interval: max of user preference and shortest legal.

    Unrecognized values fall back to the default.
    """
    if preferred_hours not in TELEMETRY_INTERVAL_OPTIONS_HOURS:
        preferred_hours = DEFAULT_TELEMETRY_INTERVAL_HOURS
    shortest = shortest_legal_interval_hours(n_tracked)
    return max(preferred_hours, shortest)


def legal_interval_options(n_tracked: int) -> list[int]:
    """Return the subset of the interval menu that is legal for a given N."""
    shortest = shortest_legal_interval_hours(n_tracked)
    return [h for h in TELEMETRY_INTERVAL_OPTIONS_HOURS if h >= shortest]


def next_run_timestamp_utc(effective_hours: int, now: datetime | None = None) -> int:
    """Return Unix timestamp for the next UTC top-of-hour where
    ``hour % effective_hours == 0``.

    Returns the next matching hour strictly in the future (never ``now``
    itself, even if ``now`` lies exactly on a matching boundary).
    """
    if effective_hours <= 0:
        effective_hours = DEFAULT_TELEMETRY_INTERVAL_HOURS
    if now is None:
        now = datetime.now(UTC)
    else:
        now = now.astimezone(UTC)

    # Round up to the next top-of-hour, then skip forward until the modulo matches.
    candidate = now.replace(minute=0, second=0, microsecond=0)
    # Always move at least one hour forward so "now" never matches.
    candidate = candidate.replace(hour=candidate.hour)
    from datetime import timedelta

    candidate = candidate + timedelta(hours=1)
    while candidate.hour % effective_hours != 0:
        candidate = candidate + timedelta(hours=1)
    return int(candidate.timestamp())
