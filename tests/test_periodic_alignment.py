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
    expected_trial_ni = np.array([1.98, 9.98, 17.98, 25.98])
    for t in expected_trial_ni:
        ai[0, int(t * fs):int((t + 0.5) * fs)] = 3.0

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
            "schedule_epoch_perf_s": 1000.0,
        }
    ).to_csv(os.path.join(sd, "barcode_events.csv"), index=False)
    pd.DataFrame(
        {
            "trial": range(4),
            "condition": "stim_A",
            "flip_time_s": [10.0, 18.0, 26.0, 34.0],
            "flip_perf_s": [1000.0, 1008.0, 1016.0, 1024.0],
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
    assert np.allclose(aligned.ni_trial_time_s, expected_trial_ni, atol=1e-4)
    assert aligned.photodiode_onset_s.notna().all()

    onebox_fs, onebox_duration = 1000, 50
    onebox_bin = os.path.join(sd, "test.imec0.ap.bin")
    onebox_meta = os.path.join(sd, "test.imec0.ap.meta")
    onebox = np.memmap(
        onebox_bin,
        dtype="<i2",
        mode="w+",
        shape=(onebox_fs * onebox_duration, 385),
    )
    onebox[:] = 0
    for t, code in zip(10.0 + 5.0 * np.arange(len(codes)), codes):
        waveform = np.repeat(
            event_waveform(int(code)), onebox_fs // C.DO_RATE_HZ
        )
        i0 = int(t * onebox_fs)
        onebox[i0:i0 + len(waveform), 384] = (
            ((waveform >> C.BARCODE_BIT) & 1).astype("<i2") * 64
        )
    onebox.flush()
    del onebox
    with open(onebox_meta, "w", encoding="utf-8") as fh:
        fh.write(
            "nSavedChans=385\n"
            "snsSaveChanSubset=0:384\n"
            f"imSampRate={onebox_fs}\n"
        )
    validation = subprocess.run(
        [
            sys.executable,
            os.path.join(ROOT, "tools", "validate_sync_session.py"),
            sd,
            onebox_bin,
            onebox_meta,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "PASS: scheduled, NI, and OneBox barcodes match" in validation.stdout
    assert "schedule/NI clock: drift" in validation.stdout
    assert "NI/OneBox clock: drift" in validation.stdout
    assert "OneBox intervals: mean" in validation.stdout
    assert "photodiode detected 4/4 trial onsets" in validation.stdout

print("PASS [periodic alignment]: timestamped trials aligned without trial barcodes")
