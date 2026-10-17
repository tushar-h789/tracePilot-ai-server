"""Client network metadata (IP, user agent) with explicit proxy trust."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from fastapi import Request

MAX_USER_AGENT_LENGTH = 256
MAX_IP_LENGTH = 45


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip: str | None
    user_agent: str | None


def _is_trusted(peer: str, trusted: list[str]) -> bool:
    try:
        addr = ip_address(peer)
    except ValueError:
        return False
    return any(addr in ip_network(net, strict=False) for net in trusted)


def client_ip(request: Request, trusted_proxies: list[str]) -> str | None:
    """Peer IP, or the right-most X-Forwarded-For hop when the peer is a trusted proxy."""
    peer = request.client.host if request.client else None
    if peer and trusted_proxies and _is_trusted(peer, trusted_proxies):
        forwarded = request.headers.get("x-forwarded-for", "")
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            candidate = hops[-1]
            try:
                ip_address(candidate)
            except ValueError:
                return peer[:MAX_IP_LENGTH]
            return candidate[:MAX_IP_LENGTH]
    return peer[:MAX_IP_LENGTH] if peer else None


def client_info(request: Request, trusted_proxies: list[str]) -> ClientInfo:
    ua = request.headers.get("user-agent")
    return ClientInfo(
        ip=client_ip(request, trusted_proxies),
        user_agent=ua[:MAX_USER_AGENT_LENGTH] if ua else None,
    )
