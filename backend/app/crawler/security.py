import ipaddress
import socket
import urllib.parse


_PROXY_EXCEPTION_NETWORKS = (
    # 本地代理工具（Surge/Clash 等）可能把域名解析到 198.18.0.0/15。
    ipaddress.ip_network("198.18.0.0/15"),
)


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    if any(ip in network for network in _PROXY_EXCEPTION_NETWORKS):
        return False
    return not ip.is_global


def _ip_from_hostname(hostname: str) -> ipaddress._BaseAddress | None:
    try:
        ip = ipaddress.ip_address(hostname)
        return getattr(ip, "ipv4_mapped", None) or ip
    except ValueError:
        return None


def is_private_url(url: str) -> bool:
    """Check if a URL points to a private/internal network address."""
    try:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return True

        hostname = parsed.hostname
        if not hostname:
            return True

        if hostname in ("localhost", "localhost.localdomain"):
            return True

        direct_ip = _ip_from_hostname(hostname)
        if direct_ip is not None:
            return not direct_ip.is_global

        addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in addrinfo:
            ip = ipaddress.ip_address(sockaddr[0])
            if _is_blocked_ip(ip):
                return True
    except (socket.gaierror, ValueError, OSError):
        return True

    return False
