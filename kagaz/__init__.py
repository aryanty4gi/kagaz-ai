"""Kagaz AI package."""
import asyncio
import os

# The python.org installer on macOS ships without root certificates, so HTTPS calls
# to Agentverse fail with CERTIFICATE_VERIFY_FAILED. Point OpenSSL at certifi's bundle.
try:
    import certifi

    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    # aiohttp (used by uAgents) builds its SSL context once at import time,
    # possibly before this runs, so add the bundle to that context too.
    try:
        from aiohttp import connector as _aiohttp_connector

        _aiohttp_connector._SSL_CONTEXT_VERIFIED.load_verify_locations(certifi.where())
    except (ImportError, AttributeError):
        pass
except ImportError:
    pass

# Python 3.14+ no longer creates an event loop on demand, but uAgents expects one
# when an Agent or Bureau is built. Make sure the main thread has a loop.
try:
    asyncio.get_event_loop_policy().get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())
