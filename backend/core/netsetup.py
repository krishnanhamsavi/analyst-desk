"""Make HTTPS work on machines where antivirus/corporate software intercepts TLS.

The problem this solves
-----------------------
Security products (AVG, Avast, Kaspersky, Zscaler, corporate MITM proxies) decrypt
HTTPS to scan it. They re-sign every certificate with their own root CA, which they
install into the *operating system* trust store. Browsers work fine. Python does
not, because Python ships its own CA list (`certifi`) that has never heard of that
root. Every request then dies with "unable to get local issuer certificate".

We use two different fixes because we have two different TLS stacks:

  * stdlib `ssl` / httpx / requests -> `truststore`, which delegates verification to
    the OS. This is the most robust option: whatever the OS trusts, we trust.

  * yfinance -> uses `curl_cffi` (libcurl + BoringSSL), which ignores Python's ssl
    module entirely and reads the `CURL_CA_BUNDLE` env var. So we write a combined
    PEM (certifi + any interception roots found in the OS store) and point it there.

Note we keep verification ON in both cases. We are not disabling security; we are
teaching Python about a root the machine's owner already trusts.

On a machine with no interception this module is a cheap no-op.
"""

from __future__ import annotations

import logging
import os
import ssl
import subprocess
import sys
from pathlib import Path

from core.config import settings

log = logging.getLogger(__name__)

_applied = False

# Subject fragments that identify a TLS-interception root worth importing.
_INTERCEPTOR_HINTS = (
    "web/mail shield",   # AVG / Avast
    "antivirus",
    "kaspersky",
    "eset",
    "bitdefender",
    "zscaler",
    "netskope",
    "fortinet",
    "sophos",
    "mcafee",
    "proxy",
    "firewall",
)


def _windows_extra_roots() -> list[str]:
    """Export interception roots from the Windows cert store as PEM strings.

    Uses PowerShell because Python has no portable API for reading the Windows
    store. Failures here are non-fatal -- we just return nothing.
    """
    if sys.platform != "win32":
        return []

    hints = "|".join(_INTERCEPTOR_HINTS)
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
$seen = @{{}}
foreach ($store in @('Cert:\\LocalMachine\\Root','Cert:\\CurrentUser\\Root')) {{
  foreach ($c in (Get-ChildItem $store)) {{
    if ($c.Subject -match '(?i){hints}') {{
      if (-not $seen.ContainsKey($c.Thumbprint)) {{
        $seen[$c.Thumbprint] = $true
        $b64 = [Convert]::ToBase64String($c.RawData, 'InsertLineBreaks')
        "-----BEGIN CERTIFICATE-----"
        $b64
        "-----END CERTIFICATE-----"
      }}
    }}
  }}
}}
"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if out.returncode != 0 or "BEGIN CERTIFICATE" not in out.stdout:
        return []

    # Split the concatenated output back into individual PEM blocks.
    blocks, current = [], []
    for line in out.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        current.append(line)
        if line == "-----END CERTIFICATE-----":
            blocks.append("\n".join(current) + "\n")
            current = []
    return blocks


def _build_ca_bundle() -> Path | None:
    """Write certifi + interception roots to one PEM and return its path."""
    try:
        import certifi
    except ImportError:
        return None

    extra = _windows_extra_roots()
    if not extra:
        return None

    bundle = settings.cache_dir / "ca_bundle.pem"
    try:
        base = Path(certifi.where()).read_text(encoding="utf-8")
        bundle.write_text(base + "\n" + "\n".join(extra), encoding="utf-8")
    except OSError:
        return None

    log.info("Built CA bundle with %d local root(s): %s", len(extra), bundle)
    return bundle


def _verification_works() -> bool:
    """Cheap probe: can we complete a TLS handshake to a public host?"""
    import socket

    try:
        ctx = ssl.create_default_context()
        with socket.create_connection(("www.sec.gov", 443), timeout=10) as sock:
            with ctx.wrap_socket(sock, server_hostname="www.sec.gov"):
                return True
    except Exception:
        return False


def apply() -> None:
    """Idempotently install both TLS fixes. Safe to call from anywhere."""
    global _applied
    if _applied:
        return
    _applied = True

    if _verification_works():
        log.debug("TLS verification already works; no interception fix needed.")
        return

    log.warning(
        "TLS verification failed against a public host -- this machine appears to "
        "intercept HTTPS. Applying local trust fixes."
    )

    # Fix 1: stdlib ssl (httpx, requests) -> verify via the OS trust store.
    try:
        import truststore

        truststore.inject_into_ssl()
        log.info("truststore installed: Python now verifies via the OS trust store.")
    except ImportError:
        log.error("truststore is not installed; `pip install truststore` to fix HTTPS.")

    # Fix 2: curl_cffi (yfinance) -> point libcurl at a combined PEM bundle.
    bundle = _build_ca_bundle()
    if bundle is not None:
        os.environ.setdefault("CURL_CA_BUNDLE", str(bundle))
        # requests honours this one; harmless elsewhere.
        os.environ.setdefault("REQUESTS_CA_BUNDLE", str(bundle))
