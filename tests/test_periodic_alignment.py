"""Verify periodic barcodes align timestamped PsychoPy trials."""
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import config as C
from barcode_tools import event_waveform
from analysis.align_session import load_aligned_bin


with tempfile.TemporaryDirectory(prefix="octo_periodic_") as sd:
    fs, duration = C.AI_RATE_HZ, 45
    ai = np.zeros((3, fs * duration))
    ai[0] = 0.1
    ai[2] = 5.0
    codes = np.arange(5001, 5009)
    ni_times = 2.0 + 5.0 * np.arange(len(codes))
    for t, code in zip(ni_times, codes):
        waveform = np.repeat(event_waveform(int(code)), fs // C.DO_RATE_HZ)
        i0 = int(t * fs)
        ai[1, i0:i0 + len(waveform)] = 5.0 * ((waveform >> C.BARCODE_BIT) & 1)

    np.asarray(ai.T / C.AI_STORE_V_PER_COUNT, dtype="<i2").tofile(
        os.path.join(sd, "ni_ai.bin")
    )
    with open(os.path.join(sd, "ni_ai.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "rate_hz": fs,
                "channels": list(C.AI_CHANNELS),
                "v_per_count": C.AI_STORE_V_PER_COUNT,
            },
            fh,
        )
    pd.DataFrame(
        {
            "slot": range(len(codes)),
            "barcode": codes,
            "target_elapsed_s": 5.0 * np.arange(len(codes)),
            "actual_elapsed_s": 5.0 * np.arange(len(codes)) + 0.02,
            "lateness_ms": 20.0,
            "status": "fired",
            "error": "",
        }
    ).to_csv(os.path.join(sd, "barcode_events.csv"), index=False)
    pd.DataFrame(
        {
            "trial": range(4),
            "condition": "stim_A",
            "flip_time_s": [10.0, 18.0, 26.0, 34.0],
            "stim_frames": 160,
            "refresh_hz": 319.9,
            "dropped_frames_so_far": [0, 1, 1, 2],
        }
    ).to_csv(os.path.join(sd, "psychopy_trials.csv"), index=False)

    subprocess.run(
        [sys.executable, os.path.join(ROOT, "analysis", "align_session.py"), sd],
        check=True,
    )
    aligned = load_aligned_bin(os.path.join(sd, "trials_aligned.bin"))
    assert len(aligned) == 4
    assert "ni_trial_time_s" in aligned
    assert "barcode_before" in aligned
    assert list(aligned.trial) == [0, 1, 2, 3]

print("PASS [periodic alignment]: timestamped trials aligned without trial barcodes")