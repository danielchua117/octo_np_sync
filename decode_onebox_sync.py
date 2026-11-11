import sys
from pathlib import Path

import numpy as np
from barcode_tools import decode

if len(sys.argv) != 3:
    raise SystemExit(
        'Usage: python decode_onebox_sync.py "recording.imec0.ap.bin" '
        '"recording.imec0.ap.meta"'
    )

bin_path = Path(sys.argv[1])
meta_path = Path(sys.argv[2])

meta = {}
for line in meta_path.read_text(encoding="utf-8").splitlines():
    if "=" in line:
        key, value = line.split("=", 1)
        meta[key] = value

nch = int(meta["nSavedChans"])
fs = float(meta["imSampRate"])
subset = meta.get("snsSaveChanSubset", "")
if nch != 385 or subset != "0:384":
    raise SystemExit(
        f"Unexpected saved channel layout: nSavedChans={nch}, "
        f"snsSaveChanSubset={subset!r}; do not assume SY is channel 384."
    )

words = np.memmap(bin_path, dtype="<i2", mode="r")
if words.size % nch:
    raise SystemExit("Binary file size is not an integer number of samples.")

nsamp = words.size // nch
# SY0 is saved channel 384; interpret its 16-bit word as unsigned.
sy = words[384::nch].view(np.uint16)
bit6 = ((sy >> 6) & 1).astype(np.uint8)

edge_idx = np.flatnonzero(bit6[1:] != bit6[:-1]) + 1
edge_times = edge_idx / fs
edge_values = bit6[edge_idx].astype(int)

starts, codes = decode(edge_times, edge_values, t_end=nsamp / fs)

print(f"Stream duration: {nsamp / fs:.3f} s")
print(f"Decoded {len(codes)} complete barcodes:")
for t, code in zip(starts, codes):
    print(f"  file time {t:.6f} s: {int(code)}")