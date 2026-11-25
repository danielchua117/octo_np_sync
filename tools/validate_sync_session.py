"""Validate periodic NI/OneBox barcodes and photodiode stimulus timing.

This intentionally ignores camera strobes and PsychoPy dropped-frame counts.

Usage:
    python tools/validate_sync_session.py "data\\bench_..." ^
        "D:\\recording.imec0.ap.bin" ^
        "D:\\recording.imec0.ap.meta"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config as C
from barcode_tools import decode, edges_from_analog
from analysis.align_session import photodiode_onsets


def load_ni(session: Path):
    meta = json.loads((session / "ni_ai.json").read_text(encoding="utf-8"))
    channels = meta["channels"]
    raw = np.fromfile(session / "ni_ai.bin", dtype="<i2")
    if not channels or raw.size % len(channels):
        raise ValueError("ni_ai.bin size does not match ni_ai.json")
    values = raw.reshape(-1, len(channels)).T * float(meta["v_per_count"])
    return {name: values[i] for i, name in enumerate(channels)}, float(meta["rate_hz"])


def decode_ni(ai, fs):
    if "barcode_loop" not in ai:
        raise ValueError("NI recording has no barcode_loop channel")
    edges_t, edges_v = edges_from_analog(ai["barcode_loop"], fs)
    return decode(edges_t, edges_v, t_end=len(ai["barcode_loop"]) / fs)


def decode_onebox(bin_path: Path, meta_path: Path):
    meta = {}
    for line in meta_path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            meta[key] = value
    nch = int(meta["nSavedChans"])
    fs = float(meta["imSampRate"])
    if nch != 385 or meta.get("snsSaveChanSubset", "") != "0:384":
        raise ValueError(
            "Expected OneBox SY0 layout nSavedChans=385 and "
            "snsSaveChanSubset=0:384"
        )
    words = np.memmap(bin_path, dtype="<i2", mode="r")
    if words.size % nch:
        raise ValueError("OneBox binary size is not a whole number of samples")
    nsamp = words.size // nch
    sy = words[384::nch].view(np.uint16)
    bit6 = (sy >> 6) & 1
    edge_idx = np.flatnonzero(bit6[1:] != bit6[:-1]) + 1
    edge_t = edge_idx / fs
    edge_v = bit6[edge_idx].astype(int)
    return (*decode(edge_t, edge_v, t_end=nsamp / fs), nsamp / fs)


def fit_clock(source, target):
    if len(source) < 2:
        raise ValueError("At least two matching barcodes are required")
    slope, intercept = np.polyfit(np.asarray(source), np.asarray(target), 1)
    residuals = np.asarray(target) - (slope * np.asarray(source) + intercept)
    return slope, intercept, residuals


def barcode_report(schedule, ni_t, ni_c, ob_t, ob_c):
    fired = schedule[schedule["status"].eq("fired")].copy()
    fired["barcode"] = fired["barcode"].astype("int64")
    ni = pd.DataFrame({"barcode": ni_c.astype("int64"), "ni_time_s": ni_t})
    ob = pd.DataFrame({"barcode": ob_c.astype("int64"), "onebox_time_s": ob_t})
    expected = set(fired["barcode"])
    ni_codes = set(ni["barcode"])
    ob_codes = set(ob["barcode"])
    print(
        f"barcodes: scheduled {len(fired)}, NI {len(ni)}, "
        f"OneBox {len(ob)}"
    )
    print(f"  missing from NI: {sorted(expected - ni_codes) or 'none'}")
    print(f"  missing from OneBox: {sorted(expected - ob_codes) or 'none'}")
    print(f"  unexpected NI: {sorted(ni_codes - expected) or 'none'}")
    print(f"  unexpected OneBox: {sorted(ob_codes - expected) or 'none'}")

    merged = (
        fired[["slot", "barcode", "target_elapsed_s", "actual_elapsed_s"]]
        .merge(ni, on="barcode", how="outer")
        .merge(ob, on="barcode", how="outer")
        .sort_values("barcode")
    )
    for source, target in (
        ("ni_time_s", "ni_interval_s"),
        ("onebox_time_s", "onebox_interval_s"),
    ):
        if source in merged:
            merged[target] = merged[source].diff()
    matched = merged.dropna(subset=["ni_time_s", "onebox_time_s"])
    if len(matched) >= 2:
        slope, intercept, residuals = fit_clock(
            matched["ni_time_s"], matched["onebox_time_s"]
        )
        print(
            f"NI/OneBox clock: drift {(slope - 1) * 1e6:+.2f} ppm; "
            f"max residual {np.abs(residuals).max() * 1000:.3f} ms"
        )
    else:
        slope = intercept = None
        print("NI/OneBox clock: unavailable; fewer than two matched barcodes")
    return merged, (slope, intercept)


def stimulus_report(session, ai, fs, schedule, ni_t, ni_c, onebox_fit, window_s):
    path = session / "psychopy_trials.csv"
    if not path.exists():
        print("stimuli: psychopy_trials.csv not found")
        return None
    trials = pd.read_csv(path)
    if "flip_time_s" not in trials:
        raise ValueError("psychopy_trials.csv must contain flip_time_s")

    fired = schedule[schedule["status"].eq("fired")].copy()
    fired["barcode"] = fired["barcode"].astype("int64")
    matched = fired.merge(
        pd.DataFrame({"barcode": ni_c.astype("int64"), "ni_barcode_s": ni_t}),
        on="barcode",
        how="inner",
    )
    if len(matched) < 2:
        raise ValueError("Cannot map stimulus trials without two matched NI barcodes")

    slope, intercept, _ = fit_clock(
        matched["actual_elapsed_s"], matched["ni_barcode_s"]
    )
    if (
        "flip_perf_s" in trials.columns
        and "schedule_epoch_perf_s" in schedule.columns
        and schedule["schedule_epoch_perf_s"].notna().any()
    ):
        epochs = schedule["schedule_epoch_perf_s"].dropna().to_numpy(float)
        if not np.allclose(epochs, epochs[0], rtol=0, atol=1e-6):
            raise ValueError("barcode_events.csv contains inconsistent schedule epochs")
        elapsed = trials["flip_perf_s"].to_numpy(float) - epochs[0]
    else:
        elapsed = (
            trials["flip_time_s"].to_numpy(float)
            - float(trials["flip_time_s"].iloc[0])
        )
    expected_ni = intercept + slope * elapsed
    detected = photodiode_onsets(
        ai["photodiode"], fs, expected_ni, window_s=window_s
    )
    out = trials.copy()
    out["expected_ni_time_s"] = expected_ni
    out["photodiode_onset_s"] = detected
    out["photodiode_detected"] = np.isfinite(detected)
    out["photodiode_latency_ms"] = (detected - expected_ni) * 1000
    if onebox_fit[0] is not None:
        out["expected_onebox_time_s"] = (
            onebox_fit[0] * expected_ni + onebox_fit[1]
        )
    n_ok = int(out["photodiode_detected"].sum())
    print(f"stimuli: photodiode detected {n_ok}/{len(out)} trial onsets")
    lat = out.loc[out["photodiode_detected"], "photodiode_latency_ms"]
    if len(lat):
        print(
            f"  latency median {lat.median():.3f} ms; "
            f"range {lat.min():.3f} to {lat.max():.3f} ms"
        )
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("onebox_bin", type=Path)
    parser.add_argument("onebox_meta", type=Path)
    parser.add_argument(
        "--photodiode-window",
        type=float,
        default=0.2,
        help="maximum time after expected onset to search (seconds)",
    )
    args = parser.parse_args()
    if args.photodiode_window <= 0:
        parser.error("--photodiode-window must be positive")

    ai, fs = load_ni(args.session)
    ni_t, ni_c = decode_ni(ai, fs)
    ob_t, ob_c, ob_duration = decode_onebox(args.onebox_bin, args.onebox_meta)
    schedule_path = args.session / "barcode_events.csv"
    if not schedule_path.exists():
        raise SystemExit(f"Missing {schedule_path}")
    schedule = pd.read_csv(schedule_path)
    required = {"barcode", "actual_elapsed_s", "status"}
    if not required.issubset(schedule.columns):
        raise SystemExit(f"{schedule_path} must contain {sorted(required)}")

    barcode_df, onebox_fit = barcode_report(schedule, ni_t, ni_c, ob_t, ob_c)
    stimulus_df = stimulus_report(
        args.session, ai, fs, schedule, ni_t, ni_c, onebox_fit,
        args.photodiode_window,
    )

    barcode_out = args.session / "sync_barcode_validation.csv"
    barcode_df.to_csv(barcode_out, index=False)
    print(f"wrote {barcode_out}")
    if stimulus_df is not None:
        stimulus_out = args.session / "sync_stimulus_validation.csv"
        stimulus_df.to_csv(stimulus_out, index=False)
        print(f"wrote {stimulus_out}")

    expected = set(schedule.loc[schedule.status.eq("fired"), "barcode"].astype(int))
    complete = expected == set(ni_c.astype(int)) == set(ob_c.astype(int))
    stimulus_ok = stimulus_df is None or bool(stimulus_df.photodiode_detected.all())
    if not complete or not stimulus_ok:
        raise SystemExit(1)
    print("PASS: scheduled, NI, and OneBox barcodes match; all stimulus onsets detected")


if __name__ == "__main__":
    main()
