"""Bounded HTTP retrieval for untrusted opportunity links."""

import ipaddress
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3


class PublicSourceError(RuntimeError):
    pass


def _public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast and not ip.is_reserved and not getattr(ip, "is_site_local", False)


def public_url(value: str) -> bool:
    if not value or any(character.isspace() for character in value) or "\\" in value:
        return False
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname or ""
        if (url.scheme not in {"http", "https"} or url.username is not None
                or url.password is not None or port not in {None, 443 if url.scheme == "https" else 80}):
            return False
        try:
            return _public_ip(host)
        except ValueError:
            return "." in host.rstrip(".") and not host.rstrip(".").endswith((".local", ".localhost", ".internal"))
    except ValueError:
        return False


def fetch_public_html(url: str, headers: dict[str, str], timeout: int = 20) -> tuple[str, str]:
    """Resolve and pin the connection at every redirect, keeping TLS hostname checks."""
    started = time.monotonic()
    for _ in range(5):
        if not public_url(url):
            raise PublicSourceError("Unsupported or non-public source URL.")
        parsed = urlsplit(url)
        secure = parsed.scheme == "https"
        port = 443 if secure else 80
        try:
            host = parsed.hostname.encode("idna").decode("ascii")
            addresses = list(dict.fromkeys(
                answer[4][0] for answer in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            ))
        except (OSError, UnicodeError) as error:
            raise PublicSourceError("Could not resolve the source host.") from error
        if not addresses or any(not _public_ip(address) for address in addresses):
            raise PublicSourceError("Source resolves to a non-public address.")
        address = next((value for value in addresses if ":" not in value), addresses[0])
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise PublicSourceError("Source exceeded its retrieval time limit.")
        options = {"timeout": urllib3.Timeout(total=remaining, connect=min(6, remaining)), "retries": False}
        pool = (urllib3.HTTPSConnectionPool(address, port, server_hostname=host, assert_hostname=host,
                                            ssl_context=ssl.create_default_context(cafile=certifi.where()), **options)
                if secure else urllib3.HTTPConnectionPool(address, port, **options))
        response = None
        try:
            target = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
            response = pool.urlopen("GET", target, headers={**headers, "Host": parsed.netloc, "Accept-Encoding": "identity"},
                                    redirect=False, preload_content=False)
            if response.status in {301, 302, 303, 307, 308} and response.headers.get("Location"):
                url = urljoin(url, response.headers["Location"])
                continue
            if response.status != 200:
                raise PublicSourceError(f"Source {host} returned HTTP {response.status}.")
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise PublicSourceError(f"Source {host} did not return HTML.")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise PublicSourceError("Source returned unexpected compression.")
            content = response.read(2_000_001, decode_content=False)
            if len(content) > 2_000_000:
                raise PublicSourceError("Source exceeded the HTML size limit.")
            content_type = response.headers.get("Content-Type", "")
            encoding = content_type.lower().split("charset=", 1)[-1].split(";", 1)[0].strip(' "\'') if "charset=" in content_type.lower() else "utf-8"
            try:
                return content.decode(encoding, errors="replace"), url
            except LookupError:
                return content.decode("utf-8", errors="replace"), url
        except (urllib3.exceptions.HTTPError, OSError) as error:
            raise PublicSourceError(f"Could not retrieve {host} ({type(error).__name__}).") from error
        finally:
            if response is not None:
                response.close()
            pool.close()
    raise PublicSourceError("Source exceeded the redirect limit.")
