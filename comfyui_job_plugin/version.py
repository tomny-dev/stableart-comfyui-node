"""Single source of the plugin version.

Keep in sync with ``pyproject.toml`` ``[project].version``. Lives in its own
module (no heavy imports) so it can be imported anywhere — including the broker
client and the package ``__init__`` — without circular-import risk.
"""

from __future__ import annotations

__version__ = "0.2.2"
