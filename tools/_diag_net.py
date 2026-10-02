# -*- coding: utf-8 -*-
"""Network/TLS diagnostics for GitHub API reachability."""
import os
import ssl
import sys

import requests

url = "https://api.github.com/rate_limit"
print("python", sys.version.split()[0])
print("openssl", ssl.OPENSSL_VERSION)
print("HTTP_PROXY", os.environ.get("HTTP_PROXY"))
print("HTTPS_PROXY", os.environ.get("HTTPS_PROXY"))
print("NO_PROXY", os.environ.get("NO_PROXY"))
import certifi  # noqa: E402
print("certifi", certifi.where())

# 1) default verify
try:
    r = requests.get(url, timeout=30)
    print("A default-verify:", r.status_code)
except Exception as e:  # noqa: BLE001
    print("A default-verify ERR:", type(e).__name__, repr(e)[:400])

# 2) verify=False
try:
    r = requests.get(url, timeout=30, verify=False)
    print("B verify-false:", r.status_code)
except Exception as e:  # noqa: BLE001
    print("B verify-false ERR:", type(e).__name__, repr(e)[:400])

# 3) truststore (use OS trust store) if available
try:
    import truststore
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    r = requests.get(url, timeout=30, verify=ctx)
    print("C truststore:", r.status_code)
except Exception as e:  # noqa: BLE001
    print("C truststore ERR:", type(e).__name__, repr(e)[:400])

# 4) raw ssl connect to check revocation-related failures
try:
    import socket
    ctx = ssl.create_default_context()
    with socket.create_connection(("api.github.com", 443), timeout=20) as sock:
        with ctx.wrap_socket(sock, server_hostname="api.github.com") as ss:
            print("D raw-tls OK proto", ss.version())
except Exception as e:  # noqa: BLE001
    print("D raw-tls ERR:", type(e).__name__, repr(e)[:400])
