"""Classify model connection errors without exposing requests or credentials."""

import socket
import ssl

import httpx
from anthropic import APIConnectionError, APITimeoutError


def _model_connection_error(error: APIConnectionError) -> dict:
    categories = (
        (ssl.SSLCertVerificationError, "tls_certificate"),
        (ssl.SSLError, "tls"),
        (socket.gaierror, "dns"),
        (httpx.ProxyError, "proxy"),
        (httpx.ConnectTimeout, "connect_timeout"),
        (httpx.ReadTimeout, "read_timeout"),
        (httpx.WriteTimeout, "write_timeout"),
        (httpx.PoolTimeout, "pool_timeout"),
        (httpx.ConnectError, "connect"),
        (httpx.ReadError, "read"),
        (httpx.WriteError, "write"),
        (httpx.RemoteProtocolError, "remote_protocol"),
        (httpx.LocalProtocolError, "local_protocol"),
        (httpx.UnsupportedProtocol, "unsupported_protocol"),
        (APITimeoutError, "timeout"),
        (APIConnectionError, "unknown"),
    )
    chain = []
    seen = set()
    reason = "unknown"
    cause = error
    while cause is not None and id(cause) not in seen and len(chain) < 8:
        seen.add(id(cause))
        matched = next(
            (
                (kind, category)
                for kind, category in categories
                if isinstance(cause, kind)
            ),
            None,
        )
        chain.append(matched[0].__name__ if matched else "OtherError")
        if matched:
            reason = matched[1]
        cause = cause.__cause__ or (
            cause.__context__ if not cause.__suppress_context__ else None
        )
    timed_out = isinstance(error, APITimeoutError)
    details = {"reason": reason, "exception_chain": chain}
    context = getattr(error, "research_context", None)
    if isinstance(context, dict):
        if isinstance(context.get("role"), str) and context["role"] in {
            "orchestrator",
            "financial",
            "web",
        }:
            details["role"] = context["role"]
        for field in ("timeout_seconds", "elapsed_seconds"):
            if type(context.get(field)) in {int, float} and 0 <= context[field] < float(
                "inf"
            ):
                details[field] = context[field]
    return {
        "code": "model_timeout" if timed_out else "model_connection_error",
        "message": (
            "模型 API 调用超时，未取得可用模型响应。"
            if timed_out
            else "模型 API 调用发生连接或传输错误，未取得可用模型响应。"
        )
        + "请检查服务地址、代理及网络连接；该错误不能证明 API Key 无效，也不能确认请求是否已送达。",
        "details": details,
    }
