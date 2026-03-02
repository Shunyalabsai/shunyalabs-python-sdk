import logging

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger that stays silent by default.

    Uses Python's standard logging module with NullHandler by default.
    Users can configure logging levels and handlers as needed.

    Args:
        name: Logger name, typically __name__ from the calling module.

    Returns:
        Configured logger instance.

    Examples:
        Enable debug logging in user code::

            import logging
            logging.basicConfig(level=logging.DEBUG)

        For specific components::

            logging.getLogger('shunyalabs.asr').setLevel(logging.DEBUG)
    """
    module_logger = logging.getLogger(name)
    module_logger.addHandler(logging.NullHandler())
    return module_logger


__all__ = ["get_logger"]
