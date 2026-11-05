"""Build event waveforms and decode barcodes from edge transitions."""
import numpy as np
import config as C


def event_waveform(code):
    ms = lambda x: int(round(x * C.DO_RATE_HZ / 1000))
    w = np.zeros(ms(C.event_duration_s() * 1000), dtype=np.uint32)
    w[:ms(C.CAM_MARKER_MS)] |= 1 << C.CAM_BIT
    bc = [1] * ms(C.BARCODE_START_MS) + [0] * ms(C.BARCODE_GAP_MS)
    for i in range(C.BARCODE_BITS):
        bc += [(int(code) >> i) & 1] * ms(C.BARCODE_BIT_MS)
    w[:len(bc)] |= np.array(bc, dtype=np.uint32) << C.BARCODE_BIT
    w[-1] = 0
    return w


def edges_from_analog(x, fs, thresh=2.5, t0=0.0):
    hi = np.asarray(x) > thresh
    d = np.diff(hi.astype(np.int8))
    idx = np.flatnonzero(d) + 1
    return t0 + idx / fs, (d[idx - 1] > 0).astype(int)


def decode(times, vals, min_gap_s=None, t_end=None, tol_s=None):
    """Return complete barcode start times and codes. t_end is recording end time."""
    S, G, B, N = (C.BARCODE_START_MS / 1000, C.BARCODE_GAP_MS / 1000,
                  C.BARCODE_BIT_MS / 1000, C.BARCODE_BITS)
    tol = S / 4 if tol_s is None else tol_s
    dur = C.barcode_duration_s()
    order = np.argsort(times, kind="stable")
    times, vals = np.asarray(times, float)[order], np.asarray(vals, int)[order]
    out_t, out_c = [], []
    busy_until = last_edge = -np.inf
    for i in range(len(times) - 1):
        t0 = times[i]
        if vals[i] != 1 or t0 < busy_until:
            if vals[i] == 1:
                last_edge = t0
            continue
        quiet_ok = min_gap_s is None or t0 - last_edge >= min_gap_s
        last_edge = t0
        if not (vals[i + 1] == 0 and abs(times[i + 1] - t0 - S) <= tol):
            continue
        if i + 2 < len(times) and times[i + 2] < t0 + S + G - tol:
            continue
        if not quiet_ok:
            continue
        centers = t0 + S + G + (np.arange(N) + 0.5) * B
        if t_end is not None and centers[-1] + B / 2 > t_end:
            continue
        idx = np.searchsorted(times, centers, side="right") - 1
        bits = vals[idx].astype(np.int64)
        out_t.append(t0)
        out_c.append(int((bits << np.arange(N)).sum()))
        busy_until = t0 + dur - B / 2
    return np.asarray(out_t), np.asarray(out_c, dtype=np.int64)
