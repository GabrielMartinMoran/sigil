"""Hermetic test environment.

The app reads its configuration from the process environment, and
``ConfigProvider`` calls ``load_dotenv()`` in its class body. Two things followed
from that:

- Importing ``src.config_provider`` pulled the repo ``.env`` into ``os.environ``,
  so the suite inherited whatever the developer had there. A local
  ``LLM_PROVIDER=opencode-go`` was enough to break three instruction tests.
- Real credentials sat in ``os.environ`` for the whole run, one accidental call
  away from being used or printed by a failing assertion.

This module runs before any test module, so it settles both inputs first:

1. ``dotenv.load_dotenv`` becomes a no-op, so ``ConfigProvider`` cannot load any
   ``.env`` file, now or later in the session.
2. Credentials and ``SIGIL_*`` overrides are removed from the inherited
   environment, then the minimum the code requires is pinned to test values.

Tests that need a non-default value patch ``ConfigProvider`` itself with
``monkeypatch.setattr``, which is explicit and scoped to that test.
"""

import os

import dotenv

# 1. Disable the `.env` loader before `src.config_provider` imports the name.
#    `from dotenv import load_dotenv` binds whatever this attribute holds at
#    import time, so patching it here is enough.
dotenv.load_dotenv = lambda *args, **kwargs: False


# 2. Drop the inherited values that would otherwise leak into the run. `SIGIL_*`
#    covers every override the app reads (step limit, context strategy, MCP
#    config path, skills roots, the self-launch guard); the rest are the
#    credential names this project and its local tooling use.
SCRUBBED = (
    'OPENROUTER_API_KEY',
    '_OPENROUTER_API_KEY',
    'OPENCODE_GO_API_KEY',
    'OPENCODE_SERVER_PASSWORD',
)
for _name in [name for name in os.environ if name.startswith('SIGIL_') or name in SCRUBBED]:
    del os.environ[_name]


# 3. Pin the minimum the code requires, to values that never reach a network.
os.environ['LLM_PROVIDER'] = 'openrouter'
os.environ['LLM_MODEL'] = 'test/model'
os.environ['OPENROUTER_API_KEY'] = 'test-key'
os.environ['OPENCODE_GO_API_KEY'] = 'test-key'

# Every other setting keeps its documented default, so a test never depends on
# the machine it runs on.
