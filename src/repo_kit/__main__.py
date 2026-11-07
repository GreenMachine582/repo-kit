"""Allow `python -m repo_kit`, the same as `rk`."""

from repo_kit.cli import main

raise SystemExit(main())
