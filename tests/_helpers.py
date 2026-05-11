import logging


def make_record(**extras) -> logging.LogRecord:
    """Build a LogRecord with the given attributes set."""
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    for k, v in extras.items():
        setattr(record, k, v)
    return record
