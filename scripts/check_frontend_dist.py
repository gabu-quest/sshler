"""
Fail fast when the bundled Vue dist is missing.

Intended for release/packaging pipelines to ensure `pnpm build` ran before
`python -m build` so the wheel/sdist contains `sshler/static/dist`.
"""

from pathlib import Path
import sys


def main() -> int:
    dist_dir = Path(__file__).resolve().parent.parent / "sshler" / "static" / "dist"
    index_html = dist_dir / "index.html"
    if not index_html.exists():
        sys.stderr.write(
            "frontend dist missing; run `pnpm install` + `pnpm run build` in frontend/ "
            "before packaging\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
