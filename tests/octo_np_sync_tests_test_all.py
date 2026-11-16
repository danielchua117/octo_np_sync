"""Run the hardware-free synchronization tests as one command.

Usage:
    python tests/test_all.py

Each existing test runs in a fresh Python process so the mocked nidaqmx module
and test-generated files cannot leak into the other tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TESTS = (
    ("decoder stress test", Path(__file__).with_name("test_decoder.py")),
    ("offline alignment test", Path(__file__).with_name("test_offline.py")),
    ("NI rig mock test", Path(__file__).with_name("test_ni_rig_mock.py")),
)


def main() -> int:
    failures: list[str] = []

    for label, script in TESTS:
        print(f"\n=== {label}: {script.name} ===", flush=True)
        result = subprocess.run(
            [sys.executable, str(script)],
            cwd=ROOT,
            env=os.environ.copy(),
        )
        if result.returncode == 0:
            print(f"PASS [{label}]", flush=True)
        else:
            print(
                f"FAIL [{label}] (exit code {result.returncode})",
                flush=True,
            )
            failures.append(label)

    if failures:
        print(
            "\nSOFTWARE SYNC TESTS FAILED: " + ", ".join(failures),
            flush=True,
        )
        return 1

    print("\nALL SOFTWARE SYNC TESTS PASSED", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())