"""Align NI barcodes, photodiode onsets, camera exposure strobes, and OneBox time.

The aligned table is written as trials_aligned.bin, a compressed NumPy NPZ
container with a .bin extension, plus trials_aligned.schema.json. Use
load_aligned_bin() to read it safely without pickle.
"""
import argparse, json, os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as C
from barcode_tools import edges_from_analog, decode


BINARY_FORMAT = "octo_np_sync_table_npz_v1"


def save_aligned_bin(df, path):
    """Save a DataFrame as a compressed binary table and JSON schema sidecar.

    This is an NPZ container (ZIP + NumPy arrays), not a headerless raw binary
    array. Numeric columns retain their NumPy types. Text columns are Unicode;
    their missing values are represented by per-column boolean null masks.
    """
    arrays = {}
    fields = []
    for i, name in enumerate(df.columns):
        key = f"col_{i}"
        series = df[name]
        if pd.api.types.is_numeric_dtype(series.dtype) or pd.api.types.is_bool_dtype(series.dtype):
            arrays[key] = series.to_numpy()
            fields.append({"name": str(name), "key": key, "kind": "numeric", "dtype": str(series.dtype)})
        else:
            text = series.astype("string")
            null_key = f"null_{i}"
            arrays[key] = text.fillna("").to_numpy(dtype=str)
            arrays[null_key] = text.isna().to_numpy(dtype=np.bool_)
            fields.append({"name": str(name), "key": key, "kind": "text", "null_key": null_key})

    with open(path, "wb") as fh:
        np.savez_compressed(fh, **arrays)

    schema_path = os.path.splitext(path)[0] + ".schema.json"
    schema = {"format": BINARY_FORMAT, "rows": len(df), "fields": fields}
    with open(schema_path, "w", encoding="utf-8") as fh:
        json.dump(schema, fh, indent=2)
    return schema_path


def load_aligned_bin(path):
    """Load a table written by save_aligned_bin(), without enabling pickle."""
    schema_path = os.path.splitext(path)[0] + ".schema.json"
    with open(schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    if schema.get("format") != BINARY_FORMAT:
        raise ValueError(f"Unsupported aligned binary format in {schema_path}")

    columns = {}
    with np.load(path, allow_pickle=False) as archive:
        for field in schema["fields"]:
            values = archive[field["key"]]
            if field["kind"] == "text":
                values = pd.array(values, dtype="string")
                mask = archive[field["null_key"]]
                values[mask] = pd.NA
            columns[field["name"]] = values
    return pd.DataFrame(columns)


def load_ai(sdir):
    with open(os.path.join(sdir, "ni_ai.json")) as f:
        meta = json.load(f)
    raw = np.fromfile(os.path.join(sdir, "ni_ai.bin"), dtype="<i2")
    nch = len(meta["channels"])
    if nch <= 0 or raw.size % nch:
        raise ValueError("NI binary data size does not match the channel metadata")
    x = raw.reshape(-1, nch).T * meta.get("v_per_count", 0.001)
    return {name: x[i] for i, name in enumerate(meta["channels"])}, meta["rate_hz"]


def photodiode_onsets(pd_v, fs, after, window_s=0.2):
    lo, hi = np.percentile(pd_v, [5, 99.5])
    if not np.isfinite(hi - lo) or hi - lo <= 0:
        return np.full(len(after), np.nan)
    on_th, off_th = lo + 0.6 * (hi - lo), lo + 0.4 * (hi - lo)
    state = pd.Series(np.where(pd_v > on_th, 1.0, np.where(pd_v < off_th, 0.0, np.nan))).ffill().fillna(0).to_numpy()
    rises = (np.flatnonzero(np.diff(state) > 0) + 1) / fs
    out = []
    for onset in after:
        k = np.searchsorted(rises, onset)
        out.append(rises[k] if k < len(rises) and rises[k] - onset < window_s else np.nan)
    return np.asarray(out)


def load_onebox_edges(path):
    ob = pd.read_csv(path)
    if not {"time_s", "value"}.issubset(ob.columns):
        raise ValueError("OneBox CSV must contain time_s,value columns")
    return ob.time_s.to_numpy(float), ob.value.to_numpy(int)


def fit_clock(source_times, target_times):
    if len(source_times) < 2:
        raise ValueError("At least two matching barcodes are required for clock fitting")
    slope, intercept = np.polyfit(source_times, target_times, 1)
    residuals = target_times - (slope * source_times + intercept)
    return slope, intercept, residuals


def match_barcode_streams(ni_t, ni_c, ob_t, ob_c):
    ni = pd.DataFrame({"barcode": ni_c, "ni_time_s": ni_t}).drop_duplicates("barcode")
    ob = pd.DataFrame({"barcode": ob_c, "onebox_time_s": ob_t}).drop_duplicates("barcode")
    return ni.merge(ob, on="barcode", how="inner")


def report_schedule(schedule, ni_t, ni_c):
    """Compare the intended periodic schedule with decoded NI barcodes."""
    fired = schedule[schedule.status == "fired"].copy()
    fired["barcode"] = fired["barcode"].astype("int64")
    decoded = pd.DataFrame({"barcode": ni_c.astype("int64"), "ni_time_s": ni_t})
    expected = set(fired.barcode)
    received = set(decoded.barcode)
    missing = sorted(expected - received)
    unexpected = sorted(received - expected)
    print(
        f"schedule: {len(fired)} fired; NI decoded {len(decoded)} complete "
        f"barcodes; missing {len(missing)}; unexpected {len(unexpected)}"
    )
    if missing:
        print("WARNING: NI-missing scheduled barcodes:", missing)
    if unexpected:
        print("WARNING: NI decoded unscheduled barcodes:", unexpected)
    if len(fired) > 1:
        intervals = np.diff(fired.actual_elapsed_s.to_numpy(float))
        print(
            f"schedule intervals: mean {np.mean(intervals):.6f} s; "
            f"range {np.min(intervals):.6f}-{np.max(intervals):.6f} s"
        )
    return fired, decoded


def align_periodic_trials(session, plog, ni_t, ni_c, onebox_match=None):
    """Align trial timestamps to NI using the periodic barcode event log."""
    schedule_path = os.path.join(session, "barcode_events.csv")
    if not os.path.exists(schedule_path):
        raise ValueError(
            "Periodic trial log has no barcode column and barcode_events.csv is missing"
        )
    schedule = pd.read_csv(schedule_path)
    required = {"barcode", "actual_elapsed_s", "status"}
    if not required.issubset(schedule.columns):
        raise ValueError(f"{schedule_path} must contain {sorted(required)}")
    schedule = schedule.dropna(subset=["barcode"]).copy()
    fired, decoded = report_schedule(schedule, ni_t, ni_c)
    matched = fired.merge(decoded, on="barcode", how="inner")
    if len(matched) < 2:
        raise ValueError("Need at least two fired barcodes decoded by NI for periodic alignment")

    slope, intercept, residuals = fit_clock(
        matched.actual_elapsed_s.to_numpy(float),
        matched.ni_time_s.to_numpy(float),
    )
    print(
        f"periodic clock: NI = {intercept:.6f} + {slope:.9f} * schedule_elapsed "
        f"(drift {(slope - 1) * 1e6:+.1f} ppm, "
        f"max residual {np.abs(residuals).max() * 1e3:.3f} ms)"
    )

    trials = plog.copy()
    if "flip_time_s" not in trials.columns:
        raise ValueError("psychopy_trials.csv must contain flip_time_s")
    has_perf_anchor = (
        "flip_perf_s" in trials.columns
        and "schedule_epoch_perf_s" in schedule.columns
        and schedule["schedule_epoch_perf_s"].notna().any()
    )
    if has_perf_anchor:
        epoch_values = schedule["schedule_epoch_perf_s"].dropna().to_numpy(float)
        if not np.allclose(epoch_values, epoch_values[0], rtol=0, atol=1e-6):
            raise ValueError("barcode_events.csv contains inconsistent schedule epochs")
        schedule_elapsed = trials.flip_perf_s.to_numpy(float) - epoch_values[0]
    else:
        first_flip = float(trials.flip_time_s.iloc[0])
        schedule_elapsed = trials.flip_time_s.to_numpy(float) - first_flip
    ni_trial = intercept + slope * schedule_elapsed
    out = trials.copy()
    out["schedule_elapsed_s"] = schedule_elapsed
    out["ni_trial_time_s"] = ni_trial
    fired_times = fired.actual_elapsed_s.to_numpy(float)
    fired_codes = fired.barcode.to_numpy("int64")
    out["barcode_before"] = pd.Series(
        [fired_codes[np.searchsorted(fired_times, t, side="right") - 1]
         if len(fired) and np.searchsorted(fired_times, t, side="right") else pd.NA
         for t in schedule_elapsed],
        dtype="Int64",
    )
    if onebox_match is not None:
        ob_slope, ob_intercept, _ = onebox_match
        out["onebox_trial_time_s"] = ob_intercept + ob_slope * ni_trial
    return out, (slope, intercept), matched


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--onebox", default=None)
    ap.add_argument("--onebox-end-s", type=float, default=None,
                    help="actual OneBox recording end time in the same time base as CSV time_s")
    a = ap.parse_args()
    ai, fs = load_ai(a.session)
    bt, bv = edges_from_analog(ai["barcode_loop"], fs)
    ni_t, ni_c = decode(bt, bv, t_end=len(ai["barcode_loop"]) / fs)
    pd_on = photodiode_onsets(ai["photodiode"], fs, ni_t)
    cam = ai["camera_exposure"]
    ct, cv = edges_from_analog(cam, fs)
    edge = C.CAMERA_EXPOSURE_EDGE
    if edge == "auto":
        edge = "falling" if np.mean(cam < 2.5) < 0.5 else "rising"
        print(f"camera: strobe detected as active-{'LOW' if edge == 'falling' else 'HIGH'}")
    strobes = ct[cv == (0 if edge == "falling" else 1)]
    if len(strobes) > 2:
        gaps = np.flatnonzero(np.diff(strobes) > 1.5 / C.CAMERA_FPS)
        print(f"camera: {len(strobes)} exposure strobes; mean {(len(strobes)-1)/(strobes[-1]-strobes[0]):.2f} Hz; {len(gaps)} long gaps")
    rows = []
    for t0, code, pdt in zip(ni_t, ni_c, pd_on, strict=True):
        reference = pdt if np.isfinite(pdt) else t0
        f = np.searchsorted(strobes, reference) if len(strobes) else -1
        rows.append(dict(barcode=code, ni_barcode_s=t0, photodiode_onset_s=pdt,
                         display_latency_ms=(pdt - t0) * 1000 if np.isfinite(pdt) else np.nan,
                         camera_frame=f if 0 <= f < len(strobes) else -1,
                         camera_strobe_index=f if 0 <= f < len(strobes) else -1,
                         camera_frame_time_s=strobes[f] if 0 <= f < len(strobes) else np.nan,
                         camera_frame_reference="photodiode" if np.isfinite(pdt) else "NI barcode"))
    df = pd.DataFrame(rows)
    onebox_fit = None
    if a.onebox:
        ob_t, ob_c = load_onebox_edges(a.onebox)
        ob_t, ob_c = decode(ob_t, ob_c, t_end=a.onebox_end_s)
        if a.onebox_end_s is None:
            print("WARNING: --onebox-end-s omitted; cannot verify that a final OneBox barcode is complete")
        matched = match_barcode_streams(ni_t, ni_c, ob_t, ob_c)
        if len(matched) < 2:
            raise ValueError(f"Need at least two unique matching barcodes for OneBox fit; got {len(matched)}")
        slope, intercept, residuals = fit_clock(
            matched.ni_time_s.to_numpy(), matched.onebox_time_s.to_numpy()
        )
        print(f"OneBox: matched {len(matched)} unique barcodes, drift {(slope-1)*1e6:+.1f} ppm, max residual {np.abs(residuals).max()*1e3:.3f} ms")
        onebox_fit = (slope, intercept, residuals)
        df["onebox_barcode_s"] = slope * df.ni_barcode_s + intercept
        df["onebox_photodiode_s"] = slope * df.photodiode_onset_s + intercept
    plog = os.path.join(a.session, "psychopy_trials.csv")
    if os.path.exists(plog):
        trials = pd.read_csv(plog)
        if "barcode" in trials.columns:
            df = trials.merge(df, on="barcode", how="outer")
            missing = df.ni_barcode_s.isna().sum()
            if missing:
                print(f"WARNING: {missing} PsychoPy trials have no NI barcode")
        else:
            df, clock_fit, _ = align_periodic_trials(
                a.session, trials, ni_t, ni_c, onebox_fit[:2] if onebox_fit else None
            )
            trial_ni = df.ni_trial_time_s.to_numpy(float)
            df["photodiode_onset_s"] = photodiode_onsets(
                ai["photodiode"], fs, trial_ni
            )
            df["display_latency_ms"] = (
                df.photodiode_onset_s - df.ni_trial_time_s
            ) * 1000
            df["camera_strobe_index"] = [
                (np.searchsorted(strobes, t) if len(strobes) else -1)
                for t in df.photodiode_onset_s.fillna(df.ni_trial_time_s)
            ]
            df["camera_strobe_index"] = df.camera_strobe_index.where(
                df.camera_strobe_index < len(strobes), -1
            )
            if onebox_fit:
                df["onebox_photodiode_s"] = (
                    onebox_fit[0] * df.photodiode_onset_s + onebox_fit[1]
                )
    out = os.path.join(a.session, "trials_aligned.bin")
    schema_path = save_aligned_bin(df, out)
    lat = df.display_latency_ms.to_numpy(dtype=float) if "display_latency_ms" in df else np.array([])
    print(f"{len(ni_c)} barcodes | median display latency {np.nanmedian(lat) if np.isfinite(lat).any() else float('nan'):.2f} ms | wrote {out}")
    print(f"Binary schema: {schema_path}")
    return df


if __name__ == "__main__":
    main()
