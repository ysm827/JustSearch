import ipaddress
import socket
import urllib.parse


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def is_private_url(url: str) -> bool:
    """Check if a URL points to a private/internal network address."""
    try:
        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True

        if hostname in ("localhost", "localhost.localdomain"):
            return True

        if hostname.startswith("::ffff:"):
            mapped_ipv4 = hostname[7:]
            try:
                ip = ipaddress.ip_address(mapped_ipv4)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return True
            except ValueError:
                pass

        addrinfo = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for _family, _type, _proto, _canonname, sockaddr in addrinfo:
            ip = ipaddress.ip_address(sockaddr[0])
            # 本地代理工具（Surge/Clash 等）可能把域名解析到 198.18.0.0/15。
            if ip in ipaddress.ip_network("198.18.0.0/15"):
                continue
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, ValueError, OSError):
        return True

    return False
