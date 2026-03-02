"""Retry logic with exponential backoff for transient failures."""

import asyncio
import random
import time
from typing import Callable, TypeVar

from ._logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

# Retryable HTTP status codes
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def _sleep_time(attempt: int, base: float = 0.5, max_delay: float = 8.0) -> float:
    """Calculate sleep time with exponential backoff + jitter."""
    delay = min(base * (2**attempt), max_delay)
    return delay + random.uniform(0, delay * 0.1)


def should_retry(status_code: int) -> bool:
    """Check if a request should be retried based on status code."""
    return status_code in RETRYABLE_STATUS_CODES


async def async_retry(
    func: Callable,
    max_retries: int = 2,
    retryable_exceptions: tuple = (),
) -> T:
    """Execute an async function with retry logic.

    Args:
        func: Async callable to execute.
        max_retries: Maximum number of retries.
        retryable_exceptions: Tuple of exception types that trigger retry.

    Returns:
        The return value of func.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except retryable_exceptions as e:
            last_exception = e
            if attempt < max_retries:
                sleep_time = _sleep_time(attempt)
                logger.debug(
                    "Retrying (attempt %d/%d) after %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    sleep_time,
                    e,
                )
                await asyncio.sleep(sleep_time)
            else:
                raise

    raise last_exception  # type: ignore[misc]


def sync_retry(
    func: Callable,
    max_retries: int = 2,
    retryable_exceptions: tuple = (),
) -> T:
    """Execute a sync function with retry logic.

    Args:
        func: Callable to execute.
        max_retries: Maximum number of retries.
        retryable_exceptions: Tuple of exception types that trigger retry.

    Returns:
        The return value of func.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exception = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except retryable_exceptions as e:
            last_exception = e
            if attempt < max_retries:
                sleep_time = _sleep_time(attempt)
                logger.debug(
                    "Retrying (attempt %d/%d) after %.1fs: %s",
                    attempt + 1,
                    max_retries,
                    sleep_time,
                    e,
                )
                time.sleep(sleep_time)
            else:
                raise

    raise last_exception  # type: ignore[misc]


__all__ = ["async_retry", "sync_retry", "should_retry", "RETRYABLE_STATUS_CODES"]
