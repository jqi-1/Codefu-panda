"""Allow `python -m local_agent` as a convenience entry point."""

from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
