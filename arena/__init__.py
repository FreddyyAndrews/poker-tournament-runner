"""Poker arena: seats, tables, match runner, event logs and verification,
built on the engine (engine/) and the bot sandbox (sandbox/)."""

import os

__version__ = "0.1.0"

# Local bot processes get a short allowlisted environment (pokercore/sandbox/
# guard.py). The jev bots also need their model setting and the proxy's URL.
os.environ.setdefault("BOT_ENV_ALLOW", "JEV_*,POKER_PROXY_URL")
