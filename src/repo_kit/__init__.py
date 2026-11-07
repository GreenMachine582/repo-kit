"""repo-kit: shared repo tooling, agent sessions and PR flow. The CLI is `rk`."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("repo-kit")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0"
