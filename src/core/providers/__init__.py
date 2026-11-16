"""The provider layer: accounts, catalogues, model building and sign-in.

The UI talks to ``ModelService`` and nothing else. Inside, the dependency
direction is::

    service  →  domain  ←  store / catalog / factory / login

``domain`` is plain Python: no ``pydantic_ai``, no ``httpx``, no ``os.environ``.
That keeps the rules (aliases, selections, validation) testable without a
network and without a framework, and it keeps the provider SDKs confined to the
adapters that need them.
"""
