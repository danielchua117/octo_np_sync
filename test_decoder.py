"""Decoder stress test: random codes incl. 0 and 2**32-1, gaps from 0 s to 3 s,
recordings that start mid-barcode, and sample-rate jitter on edge times."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config as C
from barcode_tools import event_waveform, edges_from_analog, decode

rng = np.random.default_rng(1)
fs = C.AI_RATE_HZ
GAPS = [float(g) for g in os.environ.get("GAPS", "0.001,0.05,0.3,1.0,3.0").split(",")]
for trial in range(20):
    codes = [0, 2**C.BARCODE_BITS - 1] + list(rng.integers(0, 2**C.BARCODE_BITS, 30))
    rng.shuffle(codes)
    gaps = rng.choice(GAPS, len(codes))
    sig, starts, t = [np.zeros(int(0.5 * fs))], [], 0.5
    for c, g in zip(codes, gaps, strict=True):
        w = np.repeat((event_waveform(int(c)) >> C.BARCODE_BIT) & 1, fs // C.DO_RATE_HZ)
        w = w[: int(round(C.barcode_duration_s() * fs))]          # barcode only, back-to-back
        starts.append(t); sig.append(w.astype(float)); t += len(w) / fs
        z = int(round(g * fs)); sig.append(np.zeros(z)); t += z / fs
    x = np.concatenate(sig) * 5.0
    bt, bv = edges_from_analog(x, fs)
    bt = bt + rng.normal(0, 2e-5, len(bt))                         # 20 us edge jitter
    got_t, got_c = decode(bt, bv, t_end=len(x) / fs)
    assert list(got_c) == [int(c) for c in codes], f"trial {trial}: decode mismatch"
    assert np.allclose(got_t, starts, atol=2e-4)
    # recording starts in the middle of barcode #3: first 2 lost, #3 skipped, rest exact
    cut = starts[2] + 0.3
    m = bt > cut
    _, c2 = decode(bt[m], bv[m], t_end=len(x) / fs)
    assert list(c2) == [int(c) for c in codes[3:]], f"trial {trial}: mid-barcode resync failed"
print("PASS [decoder]: 20 x 32 barcodes, gaps 1 ms-3 s, extreme codes, mid-barcode start, jitter")
