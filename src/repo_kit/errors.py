"""The one exception type rk shows to people.

Raise `RkError` (or a subclass) for a refusal or a failure the user can act on. `cli.main` prints
its message to stderr and exits 1; anything else is a bug and keeps its traceback.
"""

from __future__ import annotations


class RkError(Exception):
    """A refusal or failure with a message meant for the user."""
