"""Ajaa kaikki tests/test_*.py -testit ilman pytestiä:  python tests/run_all.py [suodatin]
(pytestillä sama: python -m pytest -q tests)"""
import importlib
import logging
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(1, str(HERE.parent))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.WARNING, format="    %(levelname)s %(name)s: %(message)s")
    flt = sys.argv[1] if len(sys.argv) > 1 else ""
    passed, failed = 0, []
    t_all = time.monotonic()
    for f in sorted(HERE.glob("test_*.py")):
        mod = importlib.import_module(f.stem)
        for name in sorted(n for n in dir(mod) if n.startswith("test_")):
            if flt and flt not in f"{f.stem}.{name}":
                continue
            t0 = time.monotonic()
            try:
                getattr(mod, name)()
                passed += 1
                print(f"PASS {f.stem}.{name} ({time.monotonic() - t0:.1f} s)", flush=True)
            except Exception:  # noqa: BLE001
                failed.append(f"{f.stem}.{name}")
                print(f"FAIL {f.stem}.{name} ({time.monotonic() - t0:.1f} s)", flush=True)
                traceback.print_exc()
    print(f"\n{passed} passed, {len(failed)} failed in {time.monotonic() - t_all:.0f} s")
    for n in failed:
        print(f"  FAILED {n}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
