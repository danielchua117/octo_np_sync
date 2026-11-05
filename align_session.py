"""align_session.py -- one table per trial linking PsychoPy, NI, photodiode, camera and OneBox.

Usage:
  python analysis/align_session.py data/<session> [--onebox onebox_barcode_edges.csv]

OneBox CSV (optional): columns time_s,value  (value 1 = rising, 0 = falling) for the
SMA1 sync-input bit, exported from SpikeGLX (e.g. CatGT -xd/-xid edge extraction).

Output: data/<session>/trials_aligned.csv with, per trial:
  barcode, condition, ni_barcode_s, photodiode_onset_s, display_latency_ms,
  camera_frame (index of first frame exposed at/after onset), camera_frame_time_s,
  marker_frame, onebox_time_s (barcode onset on the neural clock, if provided).
"""
import argparse, json, os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import config as C
from barcode_tools import edges_from_analog, decode


def load_ai(sdir):
    meta = json.load(open(os.path.join(sdir, "ni_ai.json")))
    raw = np.fromfile(os.path.join(sdir, "ni_ai.bin"), dtype="<i2")
    nch = len(meta["channels"])
    x = raw[: len(raw) // nch * nch].reshape(-1, nch).T * meta.get("v_per_count", 0.001)  # volts (old files: 1 mV)
    return {n: x[i] for i, n in enumerate(meta["channels"])}, meta["rate_hz"]


def photodiode_onsets(pd_v, fs, after, window_s=0.2):
    lo, hi = np.percentile(pd_v, [5, 99.5])
    on_th, off_th = lo + 0.6 * (hi - lo), lo + 0.4 * (hi - lo)
    # hysteresis (Schmitt trigger): ON above on_th, OFF below off_th, hold state between.
    # Robust to noise/flicker when the black-white step is small.
    state = pd.Series(np.where(pd_v > on_th, 1.0, np.where(pd_v < off_th, 0.0, np.nan))).ffill().fillna(0).to_numpy()
    rises = (np.flatnonzero(np.diff(state) > 0) + 1) / fs
    out = []
    for a in after:
        k = np.searchsorted(rises, a)
        out.append(rises[k] if k < len(rises) and rises[k] - a < window_s else np.nan)
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session"); ap.add_argument("--onebox", default=None)
    a = ap.parse_args()
    ai, fs = load_ai(a.session)

    # 1) barcodes on the NI timeline (loopback); barcode start = stimulus onset TTL
    bt, bv = edges_from_analog(ai["barcode_loop"], fs)
    ni_t, ni_c = decode(bt, bv, t_end=len(ai['barcode_loop']) / fs)

    # 2) photodiode = true on-screen onset
    pd_on = photodiode_onsets(ai["photodiode"], fs, ni_t)

    # 3) camera frames from exposure output
    cam = ai["camera_exposure"]
    ct, cv = edges_from_analog(cam, fs)
    edge = C.CAMERA_EXPOSURE_EDGE
    if edge == "auto":                       # strobe pulse = the shorter state
        edge = "falling" if np.mean(cam < 2.5) < 0.5 else "rising"
        print(f"camera: strobe detected as active-{'LOW' if edge == 'falling' else 'HIGH'}")
    frames = ct[cv == (0 if edge == "falling" else 1)]
    if len(frames) > 2:
        ifi = np.diff(frames)
        gaps = np.flatnonzero(ifi > 1.5 / C.CAMERA_FPS)
        print(f"camera: {len(frames)} frames, mean {(len(frames)-1)/(frames[-1]-frames[0]):.2f} fps, "
              f"{len(gaps)} gaps >1.5 frame (possible drops)")

    rows = []
    for t0, code, pdt in zip(ni_t, ni_c, pd_on, strict=True):
        f = np.searchsorted(frames, t0) if len(frames) else -1
        rows.append(dict(barcode=code, ni_barcode_s=t0,
                         photodiode_onset_s=pdt,
                         display_latency_ms=(t0 - pdt) * -1000 if np.isfinite(pdt) else np.nan,
                         camera_frame=f if 0 <= f < len(frames) else -1,
                         camera_frame_time_s=frames[f] if 0 <= f < len(frames) else np.nan))
    df = pd.DataFrame(rows)

    # 4) OneBox alignment by barcode value
    if a.onebox:
        ob = pd.read_csv(a.onebox)
        ob_t, ob_c = decode(ob.time_s.values, ob.value.values.astype(int))
        common, i, j = np.intersect1d(ni_c, ob_c, return_indices=True)
        k_, b_ = np.polyfit(ni_t[i], ob_t[j], 1)
        res = ob_t[j] - (k_ * ni_t[i] + b_)
        print(f"OneBox: matched {len(common)}/{len(ni_c)} barcodes, drift {(k_-1)*1e6:+.1f} ppm, "
              f"residual max {np.abs(res).max()*1e3:.3f} ms")
        df["onebox_barcode_s"] = df.ni_barcode_s.map(lambda t: k_ * t + b_)
        df["onebox_photodiode_s"] = df.photodiode_onset_s.map(lambda t: k_ * t + b_)

    # 5) merge PsychoPy log
    plog = os.path.join(a.session, "psychopy_trials.csv")
    if os.path.exists(plog):
        df = pd.read_csv(plog).merge(df, on="barcode", how="outer")
        missing = df.ni_barcode_s.isna().sum()
        if missing: print(f"WARNING: {missing} PsychoPy trials have no NI barcode")
    out = os.path.join(a.session, "trials_aligned.csv")
    df.to_csv(out, index=False)
    print(f"{len(ni_c)} barcodes | median display latency "
          f"{np.nanmedian(df.display_latency_ms):.2f} ms | wrote {out}")
    return df


if __name__ == "__main__":
    main()
