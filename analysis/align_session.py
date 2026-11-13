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
    if a.onebox:
        ob = pd.read_csv(a.onebox)
        if not {"time_s", "value"}.issubset(ob.columns):
            raise ValueError("OneBox CSV must contain time_s,value columns")
        ob_t, ob_c = decode(ob.time_s.values, ob.value.values.astype(int), t_end=a.onebox_end_s)
        if a.onebox_end_s is None:
            print("WARNING: --onebox-end-s omitted; cannot verify that a final OneBox barcode is complete")
        ni_unique = pd.DataFrame({"code": ni_c, "time": ni_t}).drop_duplicates("code")
        ob_unique = pd.DataFrame({"code": ob_c, "time": ob_t}).drop_duplicates("code")
        matched = ni_unique.merge(ob_unique, on="code", suffixes=("_ni", "_ob"))
        if len(matched) < 2:
            raise ValueError(f"Need at least two unique matching barcodes for OneBox fit; got {len(matched)}")
        slope, intercept = np.polyfit(matched.time_ni, matched.time_ob, 1)
        residuals = matched.time_ob - (slope * matched.time_ni + intercept)
        print(f"OneBox: matched {len(matched)} unique barcodes, drift {(slope-1)*1e6:+.1f} ppm, max residual {np.abs(residuals).max()*1e3:.3f} ms")
        df["onebox_barcode_s"] = slope * df.ni_barcode_s + intercept
        df["onebox_photodiode_s"] = slope * df.photodiode_onset_s + intercept
    plog = os.path.join(a.session, "psychopy_trials.csv")
    if os.path.exists(plog):
        df = pd.read_csv(plog).merge(df, on="barcode", how="outer")
        missing = df.ni_barcode_s.isna().sum()
        if missing:
            print(f"WARNING: {missing} PsychoPy trials have no NI barcode")
    out = os.path.join(a.session, "trials_aligned.bin")
    schema_path = save_aligned_bin(df, out)
    lat = df.display_latency_ms.to_numpy(dtype=float) if "display_latency_ms" in df else np.array([])
    print(f"{len(ni_c)} barcodes | median display latency {np.nanmedian(lat) if np.isfinite(lat).any() else float('nan'):.2f} ms | wrote {out}")
    print(f"Binary schema: {schema_path}")
    return df


if __name__ == "__main__":
    main()
