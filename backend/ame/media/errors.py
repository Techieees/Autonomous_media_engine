class RetryableMediaError(RuntimeError):
    """Job-layer failure that should be retried (missing ffmpeg, renderer crash)."""
