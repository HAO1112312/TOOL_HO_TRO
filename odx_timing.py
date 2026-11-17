"""Shared display formatting; recording deadlines keep their original floats."""
import math


def _seconds(value):
    """Keep rendering safe for unavailable/invalid observations."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def format_elapsed(seconds):
    """Render completed tenths as MM:SS.s, without adding a display second.

    The tiny tolerance only removes binary floating point noise at a tenth.
    Splitting the integer part avoids overflow for otherwise finite inputs.
    Invalid, nonfinite and negative values render as zero.
    """
    value = _seconds(seconds)
    whole = math.floor(value)
    tenths = whole * 10 + math.floor((value - whole) * 10 + 1e-9)
    minutes, remainder = divmod(tenths, 600)
    seconds_whole, fraction = divmod(remainder, 10)
    return f"{minutes:02d}:{seconds_whole:02d}.{fraction}"


def format_recording_clock(elapsed, target):
    """Use identical precision for HUD and Host/Player clocks."""
    return f"{format_elapsed(elapsed)} / {format_elapsed(target)}"


def remaining_seconds(elapsed, target):
    """A positive fraction of a second must not display as zero remaining."""
    return math.ceil(max(0.0, _seconds(target) - _seconds(elapsed)))
