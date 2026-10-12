"""Classify model transport failures without exposing private request data."""

import json
import socket
import ssl

import httpx
import pytest
from anthropic import APIConnectionError, APITimeoutError

from research_agent.model_errors import _model_connection_error

PRIVATE_VALUE = "synthetic-private-value-must-not-be-exported"


def connection_error(cause=None, *, timeout=False):
    request = httpx.Request(
        "POST",
        f"https://example.invalid/{PRIVATE_VALUE}?key={PRIVATE_VALUE}",
        headers={"authorization": f"Bearer {PRIVATE_VALUE}"},
        json={"messages": PRIVATE_VALUE},
    )
    error = (
        APITimeoutError(request=request)
        if timeout
        else APIConnectionError(request=request, message=PRIVATE_VALUE)
    )
    error.__cause__ = cause
    return error


@pytest.mark.parametrize(
    "cause,reason",
    [
        (ssl.SSLCertVerificationError(PRIVATE_VALUE), "tls_certificate"),
        (ssl.SSLError(PRIVATE_VALUE), "tls"),
        (socket.gaierror(PRIVATE_VALUE), "dns"),
        (httpx.ProxyError(PRIVATE_VALUE), "proxy"),
        (httpx.ConnectTimeout(PRIVATE_VALUE), "connect_timeout"),
        (httpx.ReadTimeout(PRIVATE_VALUE), "read_timeout"),
        (httpx.WriteTimeout(PRIVATE_VALUE), "write_timeout"),
        (httpx.PoolTimeout(PRIVATE_VALUE), "pool_timeout"),
        (httpx.ConnectError(PRIVATE_VALUE), "connect"),
        (httpx.ReadError(PRIVATE_VALUE), "read"),
        (httpx.WriteError(PRIVATE_VALUE), "write"),
        (httpx.RemoteProtocolError(PRIVATE_VALUE), "remote_protocol"),
        (httpx.LocalProtocolError(PRIVATE_VALUE), "local_protocol"),
        (httpx.UnsupportedProtocol(PRIVATE_VALUE), "unsupported_protocol"),
    ],
)
def test_typed_transport_causes_have_safe_reason_codes(cause, reason):
    timeout = isinstance(cause, httpx.TimeoutException)
    diagnostic = _model_connection_error(connection_error(cause, timeout=timeout))

    assert diagnostic["code"] == (
        "model_timeout" if timeout else "model_connection_error"
    )
    assert diagnostic["details"]["reason"] == reason
    assert diagnostic["details"]["exception_chain"] == [
        "APITimeoutError" if timeout else "APIConnectionError",
        type(cause).__name__,
    ]
    assert PRIVATE_VALUE not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "cause,reason",
    [
        (ssl.SSLCertVerificationError(PRIVATE_VALUE), "tls_certificate"),
        (socket.gaierror(PRIVATE_VALUE), "dns"),
    ],
)
def test_specific_inner_cause_wins_over_outer_connect_error(cause, reason):
    transport = httpx.ConnectError(PRIVATE_VALUE)
    transport.__cause__ = cause

    diagnostic = _model_connection_error(connection_error(transport))

    assert diagnostic["details"]["reason"] == reason
    assert diagnostic["details"]["exception_chain"] == [
        "APIConnectionError",
        "ConnectError",
        type(cause).__name__,
    ]


@pytest.mark.parametrize("timeout,reason", [(False, "unknown"), (True, "timeout")])
def test_missing_cause_preserves_uncertainty(timeout, reason):
    diagnostic = _model_connection_error(connection_error(timeout=timeout))

    assert diagnostic["details"]["reason"] == reason
    assert len(diagnostic["details"]["exception_chain"]) == 1


def test_unknown_exception_name_and_message_are_not_exported():
    private_exception_type = type(PRIVATE_VALUE, (Exception,), {})
    diagnostic = _model_connection_error(
        connection_error(private_exception_type(PRIVATE_VALUE))
    )

    assert diagnostic["details"] == {
        "reason": "unknown",
        "exception_chain": ["APIConnectionError", "OtherError"],
    }
    assert PRIVATE_VALUE not in json.dumps(diagnostic)


@pytest.mark.parametrize("suppressed", [False, True])
def test_only_unsuppressed_context_is_traversed(suppressed):
    error = connection_error()
    error.__context__ = socket.gaierror(PRIVATE_VALUE)
    error.__suppress_context__ = suppressed

    diagnostic = _model_connection_error(error)

    assert diagnostic["details"]["reason"] == ("unknown" if suppressed else "dns")
    assert len(diagnostic["details"]["exception_chain"]) == (1 if suppressed else 2)


def test_explicit_cause_takes_precedence_over_context():
    error = connection_error(httpx.ProxyError(PRIVATE_VALUE))
    error.__context__ = socket.gaierror(PRIVATE_VALUE)
    error.__suppress_context__ = False

    diagnostic = _model_connection_error(error)

    assert diagnostic["details"]["reason"] == "proxy"
    assert diagnostic["details"]["exception_chain"] == [
        "APIConnectionError",
        "ProxyError",
    ]


def test_cyclic_exception_chain_stops_without_repeating_nodes():
    cause = httpx.ConnectError(PRIVATE_VALUE)
    error = connection_error(cause)
    cause.__cause__ = error

    diagnostic = _model_connection_error(error)

    assert diagnostic["details"]["reason"] == "connect"
    assert diagnostic["details"]["exception_chain"] == [
        "APIConnectionError",
        "ConnectError",
    ]


def test_exception_chain_length_is_bounded():
    cause = socket.gaierror(PRIVATE_VALUE)
    for _index in range(10):
        wrapper = RuntimeError(PRIVATE_VALUE)
        wrapper.__cause__ = cause
        cause = wrapper

    diagnostic = _model_connection_error(connection_error(cause))

    assert diagnostic["details"]["reason"] == "unknown"
    assert len(diagnostic["details"]["exception_chain"]) == 8
