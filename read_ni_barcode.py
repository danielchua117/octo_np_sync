import json
import sys
from pathlib import Path

import numpy as np

# Add the repository root (the folder containing this script and barcode_tools.py).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from barcode_tools import edges_from_analog, decode


if len(sys.argv) != 2:
    raise SystemExit(
        'Usage: python read_ni_barcode.py "path\\to\\session_folder"'
    )

session = Path(sys.argv[1])
meta_path = session / "ni_ai.json"
bin_path = session / "ni_ai.bin"

if not meta_path.is_file():
    raise SystemExit(f"Metadata file not found: {meta_path}")
if not bin_path.is_file():
    raise SystemExit(f"NI binary file not found: {bin_path}")

meta = json.loads(meta_path.read_text(encoding="utf-8"))
channels = meta["channels"]
n_channels = len(channels)
if n_channels == 0:
    raise SystemExit("ni_ai.json has no channel names")

raw = np.fromfile(bin_path, dtype="<i2")
if raw.size % n_channels:
    raise SystemExit("ni_ai.bin size does not match the channel metadata")

counts = raw.reshape(-1, n_channels)
try:
    barcode_col = channels.index("barcode_loop")
except ValueError:
    raise SystemExit(f"No 'barcode_loop' channel. Channels listed: {channels}")

barcode_v = counts[:, barcode_col] * float(meta["v_per_count"])
sample_rate = float(meta["rate_hz"])
duration_s = len(barcode_v) / sample_rate

edge_times_s, edge_values = edges_from_analog(barcode_v, sample_rate)
times_s, codes = decode(edge_times_s, edge_values, t_end=duration_s)

print(f"Session: {session}")
print(f"Channels: {channels}")
print(f"Sample rate: {sample_rate:g} Hz")
print(f"Duration: {duration_s:.3f} s")
print(f"Decoded {len(codes)} complete barcodes:")
for t, code in zip(times_s, codes):
    print(f"  {t:.6f} s  barcode {int(code)}")