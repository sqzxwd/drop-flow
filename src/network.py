"""Network utilities for DropFlow: IP detection, port discovery, and QR generation."""

from __future__ import annotations

import io
import ipaddress
import socket
from pathlib import Path
from typing import List, Optional

import qrcode
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def _is_valid_ipv4(ip: str) -> bool:
    try:
        obj = ipaddress.ip_address(ip)
        return isinstance(obj, ipaddress.IPv4Address) and not obj.is_loopback and not obj.is_link_local
    except ValueError:
        return False


def get_all_local_ips() -> List[str]:
    """Retrieve all candidate IPv4 addresses for this machine."""
    ips = set()

    # Strategy 1: Socket connect probe to public DNS (no actual packet sent)
    for target in [("8.8.8.8", 80), ("1.1.1.1", 80)]:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.5)
            s.connect(target)
            ip = s.getsockname()[0]
            s.close()
            if _is_valid_ipv4(ip):
                ips.add(ip)
                break
        except Exception:
            pass

    # Strategy 2: Hostname resolution
    try:
        hostname = socket.gethostname()
        for addr_info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = addr_info[4][0]
            if _is_valid_ipv4(ip):
                ips.add(ip)
    except Exception:
        pass

    return list(ips)


def get_local_ip() -> str:
    """
    Get the best local IPv4 address for Wi-Fi / LAN sharing.
    Prioritizes 192.168.x.x, then 10.x.x.x, 172.16-31.x.x, then other valid LAN IPs,
    defaulting to 127.0.0.1 if offline.
    """
    ips = get_all_local_ips()
    if not ips:
        return "127.0.0.1"

    def ip_priority(ip: str) -> int:
        if ip.startswith("192.168."):
            return 1
        if ip.startswith("10."):
            return 2
        try:
            obj = ipaddress.ip_address(ip)
            if obj.is_private:
                return 3
        except ValueError:
            pass
        return 4

    sorted_ips = sorted(ips, key=ip_priority)
    return sorted_ips[0]


def is_port_available(port: int, host: str = "0.0.0.0") -> bool:
    """Check whether a given TCP port is available on the specified host."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def find_free_port(preferred_port: int = 8000, max_attempts: int = 100, host: str = "0.0.0.0") -> int:
    """
    Find a free port starting from preferred_port.
    If preferred_port is 0, let the OS allocate an ephemeral port.
    """
    if preferred_port == 0:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind((host, 0))
        port = sock.getsockname()[1]
        sock.close()
        return port

    for port in range(preferred_port, preferred_port + max_attempts):
        if is_port_available(port, host):
            return port

    # Fallback to ephemeral port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def generate_qr_ascii(url: str, invert: bool = True) -> str:
    """Generate ASCII representation of QR code for terminal printing."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1,
    )
    qr.add_data(url)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=invert)
    return buf.getvalue().rstrip()


def display_startup_banner(
    url: str,
    local_url: str,
    ip: str,
    port: int,
    downloads_dir: Path,
    room: str = "general",
    show_qr: bool = True,
    console: Optional[Console] = None,
) -> None:
    """Display rich terminal banner with network info and QR code."""
    c = console or Console()

    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold cyan")
    table.add_column("Value", style="bold white")

    table.add_row("Wi-Fi URL", f"[link={url}][bold green]{url}[/bold green][/link]")
    table.add_row("Local URL", f"[link={local_url}]{local_url}[/link]")
    table.add_row("Device IP", f"[yellow]{ip}[/yellow]")
    table.add_row("Active Port", f"[cyan]{port}[/cyan]")
    table.add_row("Room", f"[magenta]{room}[/magenta]")
    table.add_row("Downloads", f"[dim]{downloads_dir.resolve()}[/dim]")
    table.add_row("Status", "[bold green]● Active & Listening[/bold green]")

    content = []
    if show_qr:
        qr_ascii = generate_qr_ascii(url)
        content.append(Text(qr_ascii, style="bold white", justify="center"))
        content.append(Text("\nScan QR code from smartphone or tablet\n", style="dim italic", justify="center"))

    content.append(table)
    content.append(Text("\nPress Ctrl+C to shut down cleanly.", style="dim italic", justify="center"))

    panel = Panel(
        Text("\n").join(content),
        title="[bold yellow]⚡ DropFlow[/bold yellow] | [bold blue]Cross-Platform File & Clipboard Sync[/bold blue]",
        subtitle="[dim]Zero Config • Instant LAN Sharing • Stream-Ready[/dim]",
        border_style="bright_blue",
        padding=(1, 2),
    )
    c.print(panel)
