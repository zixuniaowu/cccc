from __future__ import annotations

import errno
import socket
import sys


def _format_bind_target(host: str, port: int) -> str:
    raw_host = str(host or "").strip() or "0.0.0.0"
    if ":" in raw_host and not (raw_host.startswith("[") and raw_host.endswith("]")):
        raw_host = f"[{raw_host}]"
    return f"{raw_host}:{int(port)}"


def _is_windows_access_denied(exc: OSError) -> bool:
    if not sys.platform.startswith("win"):
        return False
    if getattr(exc, "winerror", None) == 10013:
        return True
    return int(getattr(exc, "errno", 0) or 0) in {errno.EACCES, errno.EPERM}


def _is_addr_in_use(exc: OSError) -> bool:
    if int(getattr(exc, "errno", 0) or 0) == errno.EADDRINUSE:
        return True
    return int(getattr(exc, "winerror", 0) or 0) == 10048


def describe_bind_error(*, host: str, port: int, exc: OSError) -> str:
    target = _format_bind_target(host, port)
    base = f"Web port {int(port)} is unavailable on {target}."
    if _is_addr_in_use(exc):
        return (
            f"{base} Another process is already using that port. "
            "Stop the existing process, or choose a different port with --port <port> "
            "or CCCC_WEB_PORT."
        )
    if _is_windows_access_denied(exc):
        return (
            f"{base} Windows denied the bind. On Windows this commonly means the port falls "
            "inside an excluded TCP port range reserved by Hyper-V, WSL, WinNAT, or HNS, "
            "even when no process is listening. Check with "
            "`netsh interface ipv4 show excludedportrange protocol=tcp`, then restart CCCC "
            "with a different port via --port <port> or CCCC_WEB_PORT."
        )
    detail = str(exc).strip()
    if detail:
        return f"{base} Bind failed: {detail}"
    return f"{base} Bind failed."


def ensure_tcp_port_bindable(*, host: str, port: int) -> None:
    host_s = str(host or "").strip() or "0.0.0.0"
    port_n = int(port)
    flags = socket.AI_PASSIVE if host_s in {"0.0.0.0", "::", ""} else 0
    infos = socket.getaddrinfo(host_s, port_n, type=socket.SOCK_STREAM, flags=flags)
    last_error: OSError | None = None
    seen: set[tuple[int, int, int, tuple[object, ...]]] = set()
    for family, socktype, proto, _canonname, sockaddr in infos:
        addr_key = (int(family), int(socktype), int(proto), tuple(sockaddr))
        if addr_key in seen:
            continue
        seen.add(addr_key)
        sock = socket.socket(family, socktype, proto)
        try:
            if sys.platform.startswith("win") and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                except OSError:
                    pass
            sock.bind(sockaddr)
            return
        except OSError as exc:
            last_error = exc
        finally:
            try:
                sock.close()
            except Exception:
                pass
    if last_error is not None:
        raise RuntimeError(describe_bind_error(host=host_s, port=port_n, exc=last_error)) from last_error
    raise RuntimeError(f"Web port {port_n} is unavailable on {_format_bind_target(host_s, port_n)}.")
