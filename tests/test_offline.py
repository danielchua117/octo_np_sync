"""Hardware-free end-to-end alignment test with synthetic NI and OneBox data."""
import json, os, sys, shutil, subprocess
import numpy as np
import pandas as pd
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
import config as C
from barcode_tools import event_waveform, edges_from_analog
from analysis.align_session import load_aligned_bin


def run(name, pd_lo, pd_hi, pd_noise, pd_flicker):
    rng = np.random.default_rng(0)
    fs, T = C.AI_RATE_HZ, 120.0
    n = int(fs * T)
    ai = np.zeros((3, n))
    sd = os.path.join(ROOT, "data", "_selftest")
    shutil.rmtree(sd, ignore_errors=True)
    os.makedirs(sd, exist_ok=True)

    onsets, codes, latency = [], [], []
    t, code = 2.0, 1000
    while t < T - 3:
        code += 1
        w = event_waveform(code)
        up = np.repeat(w, fs // C.DO_RATE_HZ)
        i0 = int(t * fs)
        ai[1, i0:i0 + len(up)] = 5.0 * ((up >> C.BARCODE_BIT) & 1)
        lat = rng.uniform(0.008, 0.025)
        p0 = int((t + lat) * fs)
        ai[0, p0:p0 + int(0.5 * fs)] = pd_hi - pd_lo
        onsets.append(t)
        codes.append(code)
        latency.append(lat)
        t += 0.5 + rng.uniform(C.ITI_MIN_S, C.ITI_MAX_S)

    ai[0] += pd_lo + rng.normal(0, pd_noise, n) + pd_flicker * np.sin(2 * np.pi * 100 * np.arange(n) / fs)
    ai[2] = 5.0
    for f in np.arange(0.5, T, 1 / C.CAMERA_FPS):
        ai[2, int(round(f * fs)):int(round(f * fs)) + 10] = 0.65

    np.round(ai.T / C.AI_STORE_V_PER_COUNT).astype("<i2").tofile(os.path.join(sd, "ni_ai.bin"))
    with open(os.path.join(sd, "ni_ai.json"), "w") as fh:
        json.dump(dict(rate_hz=fs, channels=list(C.AI_CHANNELS), v_per_count=C.AI_STORE_V_PER_COUNT), fh)
    pd.DataFrame(dict(trial=range(len(codes)), condition="face", barcode=codes,
                      flip_time_s=np.array(onsets) + 3.0)).to_csv(os.path.join(sd, "psychopy_trials.csv"), index=False)

    bt, bv = edges_from_analog(ai[1], fs)
    keep = (bt < 40) | (bt > 60)
    onebox_csv = os.path.join(sd, "onebox.csv")
    pd.DataFrame(dict(time_s=7.5 + bt[keep] * (1 + 25e-6), value=bv[keep])).to_csv(onebox_csv, index=False)
    subprocess.run([sys.executable, os.path.join(ROOT, "analysis", "align_session.py"), sd,
                    "--onebox", onebox_csv, "--onebox-end-s", str(7.5 + T * (1 + 25e-6))], check=True)

    out_bin = os.path.join(sd, "trials_aligned.bin")
    assert os.path.isfile(out_bin)
    assert os.path.isfile(os.path.join(sd, "trials_aligned.schema.json"))
    df = load_aligned_bin(out_bin)
    assert list(df.barcode.astype(int)) == codes, "barcodes decoded incorrectly"
    assert np.allclose(df.ni_barcode_s, onsets, atol=2e-4)
    assert np.allclose(df.display_latency_ms, np.array(latency) * 1000, atol=0.3)
    assert (df.camera_strobe_index >= 0).all()
    exp_ob = 7.5 + np.array(onsets) * (1 + 25e-6)
    assert np.allclose(df.onebox_barcode_s, exp_ob, atol=2e-4)
    print(f"PASS [{name}]: {len(codes)} trials, aligned binary table round-tripped")


SCENARIOS = [
    ("strong photodiode", 0.10, 3.60, 0.020, 0.0),
    ("weak photodiode = measured rig levels", 0.013, 0.240, 0.002, 0.002),
]
for scenario in SCENARIOS:
    run(*scenario)
print("ALL TESTS PASSED")
