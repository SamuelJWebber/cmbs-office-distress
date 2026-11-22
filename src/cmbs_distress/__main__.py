"""Allow ``python -m cmbs_distress`` invocation."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
