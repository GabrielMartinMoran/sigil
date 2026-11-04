"""Hermetic test environment.

The suite must not depend on the developer's shell, their ``.env``, or their
``~/.sigil``. Two independent reasons, both now enforced by construction rather
than by import order:

- **Settings are read by an explicit loader.** ``load_settings()`` takes the
  environment as an argument, so a test builds the configuration it wants and
  nothing else in the process can freeze a value by being imported first.
- **Provider credentials never come from the environment at all.** They live in
  ``~/.sigil/providers/credentials.json`` and are configured from the UI, so
  there is nothing to scrub.

What this module still does: point ``SIGIL_HOME`` at a per-run temporary
directory, so any code that resolves a path (sessions, roadmaps, providers) writes
inside the sandbox instead of the developer's home. Test modules that need their
own directory use ``tmp_path`` and pass it explicitly.
"""

import atexit
import os
import shutil
import tempfile

import dotenv

# 1. Disable the `.env` loader: `src/sigil.py` calls it, and a local file must not
#    reach the process during tests.
dotenv.load_dotenv = lambda *args, **kwargs: False


# 2. Sandbox every path Sigil owns.
SANDBOX = tempfile.mkdtemp(prefix='sigil-tests-')
os.environ['SIGIL_HOME'] = SANDBOX
atexit.register(shutil.rmtree, SANDBOX, True)


# 3. Drop the tuning overrides the shell may carry, so a value set for daily use
#    does not change an assertion. Every test that needs a non-default value
#    passes it to `load_settings` explicitly.
for _name in [name for name in os.environ if name.startswith('SIGIL_') and name != 'SIGIL_HOME']:
    del os.environ[_name]
