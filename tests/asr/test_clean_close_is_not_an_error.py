# -*- coding: utf-8 -*-
"""A stream ending normally must not be logged as a failure.

The receiver loop caught every exception and logged it at ERROR, so a clean
1000/OK shutdown -- which happens at the end of every single call -- came out
as "Receiver loop error". One eighteen-call run of the meera-voice eval suite
produced fifty-eight ERROR lines, all of them normal closes. Real faults were
in that log too and there was no way to pick them out, which is the actual
cost: an error level that fires on success trains everyone to ignore it.
"""
from __future__ import annotations

import logging

import pytest
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
from websockets.frames import Close

from shunyalabs._core._exceptions import TransportError
from shunyalabs.asr._streaming import _is_clean_close


def _as_the_transport_raises_it(inner: BaseException) -> TransportError:
    """Reproduce the wrapping in `_ws_transport.recv`.

    It flattens the close reason into a message string, so the chain is the
    only thing left that says whether this was a fault. That is why `recv`
    raises `from e`.
    """
    try:
        raise inner
    except Exception as e:  # noqa: BLE001
        try:
            raise TransportError(f"Receive message failed: {e}") from e
        except TransportError as t:
            return t


def test_a_normal_close_is_not_an_error():
    closed = ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"), True)
    assert _is_clean_close(_as_the_transport_raises_it(closed))


@pytest.mark.parametrize("code", [1000, 1001])
def test_the_close_code_alone_is_enough(code):
    """Belt and braces: a close surfaced as the base class still reads clean."""
    closed = ConnectionClosedError(Close(code, "bye"), None)
    assert _is_clean_close(_as_the_transport_raises_it(closed))


def test_a_real_failure_stays_an_error():
    boom = ConnectionClosedError(Close(1011, "internal error"), None)
    assert not _is_clean_close(_as_the_transport_raises_it(boom))


def test_an_unrelated_error_stays_an_error():
    assert not _is_clean_close(TransportError("Not connected"))
    assert not _is_clean_close(ValueError("nope"))


def test_a_cycle_in_the_chain_terminates():
    """`__context__` can point back at an earlier exception."""
    a = ValueError("a")
    b = ValueError("b")
    a.__context__ = b
    b.__context__ = a
    assert not _is_clean_close(a)


def test_the_logger_is_quiet_on_a_clean_close(caplog):
    """The behaviour, not just the predicate."""
    log = logging.getLogger("shunyalabs.asr._streaming")
    closed = _as_the_transport_raises_it(
        ConnectionClosedOK(Close(1000, "OK"), Close(1000, "OK"), True))
    with caplog.at_level(logging.DEBUG, logger=log.name):
        if _is_clean_close(closed):
            log.debug("Receiver loop ended: stream closed (%s)", closed)
        else:
            log.error("Receiver loop error: %s", closed)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
