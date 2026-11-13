# octopus neuropixels synchronization rig

## Aligned table binary output

`analysis/align_session.py` writes `trials_aligned.bin` as a compressed NumPy NPZ container (binary ZIP of typed NumPy arrays) and `trials_aligned.schema.json` describing its columns and text null masks. The `.bin` extension is retained as requested; this is **not** a headerless raw binary table. Do not rename or remove the schema sidecar. Read it safely from Python with `from analysis.align_session import load_aligned_bin` and `load_aligned_bin(path)`. The older `trials_aligned.csv` is no longer written by the updated script. The OneBox edge CSV remains an input format.

Run the offline test from the repository root with `python tests/test_offline.py` to check binary write/read round-tripping along with synthetic alignment.
