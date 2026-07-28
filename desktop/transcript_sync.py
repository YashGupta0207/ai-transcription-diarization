"""Small, UI-independent helpers for timestamped transcript playback."""
from bisect import bisect_right


def active_word_index(words: list[dict], seconds: float) -> int:
    """Return the latest word that has started at ``seconds``.

    The list is supplied by the API in timestamp order.  Keeping the lookup
    here avoids a linear scan on every media-player position update.
    """
    if not words:
        return -1
    starts = [float(word.get("start", 0.0)) for word in words]
    return bisect_right(starts, seconds) - 1


def format_timestamp(seconds: float) -> str:
    # Convert once to integer centiseconds so binary floating-point values
    # such as 53.12 do not display as 53.11.
    centiseconds = round(max(0.0, seconds or 0.0) * 100)
    minutes, centiseconds = divmod(centiseconds, 6000)
    seconds, centiseconds = divmod(centiseconds, 100)
    return f"{minutes:02d}:{seconds:02d}.{centiseconds:02d}"
