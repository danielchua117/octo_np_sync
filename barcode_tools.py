"""barcode_tools.py -- build the event waveform and decode barcodes from edges/analog."""
import numpy as np
import config as C


def event_waveform(code):
    """uint32 port samples: barcode + camera marker, both starting at sample 0."""
    ms = lambda x: int(round(x * C.DO_RATE_HZ / 1000))
    n = ms(C.event_duration_s() * 1000)
    w = np.zeros(n, dtype=np.uint32)
    w[:ms(C.CAM_MARKER_MS)] |= 1 << C.CAM_BIT
    bc = [1] * ms(C.BARCODE_START_MS) + [0] * ms(C.BARCODE_GAP_MS)
    for i in range(C.BARCODE_BITS):
        bc += [(code >> i) & 1] * ms(C.BARCODE_BIT_MS)
    w[:len(bc)] |= np.array(bc, dtype=np.uint32) << C.BARCODE_BIT
    w[-1] = 0                                   # lines idle LOW afterwards
    return w


def edges_from_analog(x, fs, thresh=2.5, t0=0.0):
    """Analog trace -> (times, values) of threshold crossings (value 1 = rising)."""
    hi = np.asarray(x) > thresh
    d = np.diff(hi.astype(np.int8))
    idx = np.flatnonzero(d) + 1
    return t0 + idx / fs, (d[idx - 1] > 0).astype(int)


def decode(times, vals, min_gap_s=None, t_end=None, tol_s=None):
    """Centre-sampling decoder; returns (start_times, codes) exactly as sent.

    A barcode start is recognised by its signature, not by silence before it:
    a HIGH pulse of BARCODE_START_MS (+/- tol) followed by at least BARCODE_GAP_MS
    of LOW. Data bits are always >= BARCODE_BIT_MS long, so a bit can't be
    mistaken for a start pulse. After a barcode is accepted, edges inside it are
    skipped. Barcodes may follow each other with any LOW gap >= 1 sample
    (run_experiment.py enforces >= 0.1 s between the end of one event and the next).

    t_end: end of the recording (s); barcodes not finished by then are skipped.
    min_gap_s: optional extra quiet time required before a start (default 0).
    tol_s: start/gap width tolerance (default 1/4 of the start pulse, 5 ms).
    """
    S, G, B, N = (C.BARCODE_START_MS / 1000, C.BARCODE_GAP_MS / 1000,
                  C.BARCODE_BIT_MS / 1000, C.BARCODE_BITS)
    tol = S / 4 if tol_s is None else tol_s
    dur = C.barcode_duration_s()
    o = np.argsort(times, kind="stable")
    times, vals = np.asarray(times, dtype=float)[o], np.asarray(vals)[o]
    out_t, out_c = [], []
    busy_until, last_edge = -np.inf, -np.inf
    for i in range(len(times) - 1):
        t0 = times[i]
        if vals[i] != 1 or t0 < busy_until:
            last_edge = t0 if vals[i] == 1 else last_edge
            continue
        quiet_ok = min_gap_s is None or t0 - last_edge >= min_gap_s
        last_edge = t0
        if not (vals[i + 1] == 0 and abs(times[i + 1] - t0 - S) <= tol):
            continue                                   # not a 20 ms start pulse
        if i + 2 < len(times) and times[i + 2] < t0 + S + G - tol:
            continue                                   # gap after start too short
        if not quiet_ok:
            continue
        c = t0 + S + G + (np.arange(N) + 0.5) * B      # bit centres
        if t_end is not None and c[-1] + B / 2 > t_end:
            continue
        idx = np.searchsorted(times, c, side="right") - 1
        bits = vals[idx].astype(np.int64)
        out_t.append(t0); out_c.append(int((bits << np.arange(N)).sum()))
        busy_until = t0 + dur - B / 2                  # skip this barcode's own edges
    return np.array(out_t), np.array(out_c, dtype=np.int64)
