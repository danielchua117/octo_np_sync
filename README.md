# Octopus Neuropixels synchronization rig

For installing this branch on a new Windows laptop, follow
[SETUP_NEW_LAPTOP.txt](SETUP_NEW_LAPTOP.txt).

## Startup and periodic barcode schedule

Run `python run_experiment.py --subject test --trials 3` for a short hardware bench test. The script starts NI AI first so it can timestamp camera exposures from the beginning. It then prompts the operator to start MVS camera acquisition and press **C**; after the configured 0.2 s lead, it prompts for SpikeGLX recording and **S** confirmation. SpikeGLX has a configurable initialization wait (default 3 s). After this the first stimulus flip starts the periodic barcode schedule, and barcodes are emitted every 5 s independently of stimulus onsets. This is a manual coordination workflow: the Python code does not start MVS or SpikeGLX itself.

Run all hardware-free synchronization checks with one command:

```text
python tests/test_all.py
```

The combined runner executes the decoder, offline alignment, and NI mock tests in separate Python processes and exits with status 1 if any test fails. These checks do not test the physical NI card, camera, or SpikeGLX.

## Looming-stimulus pilot branch

The separate `run_loom_experiment.py` entry point implements the standalone
looming-disk protocol while retaining NI periodic barcodes and the photodiode
patch. It writes `psychopy_trials.csv`, `barcode_events.csv`, `ni_ai.bin`, and
`ni_ai.json` into a timestamped session folder. The original
`run_experiment.py` remains available for the older image-stimulus protocol.

Run the pilot from the repository root:

```text
python run_loom_experiment.py --subject loom_pilot
```

Follow the prompts to select the monitor, loom level, trial count and inter-trial
interval. Start MVS and SpikeGLX when prompted, confirm their recording states,
then press SPACE to begin. For hardware-free display testing only, use
`--simulate`; simulation does not validate NI, barcodes, or photodiode data.
After a hardware run, align and validate it with:

```text
python analysis/align_session.py "data\SESSION_FOLDER"
python tools/validate_sync_session.py "data\SESSION_FOLDER" ^
  "D:\recording\recording.imec0.ap.bin" ^
  "D:\recording\recording.imec0.ap.meta"
```

NI AI starts before the camera, intentionally: starting the DAQ only after the camera would mean early camera frames have no NI timestamps. The camera gets a 0.2 s head start before SpikeGLX, as requested. Verify SpikeGLX reports recording before pressing S. Use `--camera-spikeglx-delay 0.2` and adjust `--spikeglx-settle` if your acquisition needs longer initialization. `--no-wait` skips prompts for software testing; do not use it as evidence that camera or SpikeGLX started.

The periodic schedule is anchored to the first stimulus-onset flip using a monotonic clock. The NI DO call is software initiated, so actual edge times can be late by a small, measurable amount. `barcode_events.csv` records slot number, code, target and actual event time, lateness, and missed/error slots. If the process is delayed enough that an event would crowd the preceding waveform, it skips that slot and advances the barcode value instead of emitting a catch-up burst. The OneBox decoder can use missing sequence values to detect omitted barcodes.

The 60 fps camera acquisition rate is distinct from the MVS preview rate. The output camera index is an NI strobe index, not guaranteed to equal saved-video frame number if frames are dropped.

## Data outputs

- `ni_ai.bin` and `ni_ai.json`: interleaved int16 NI analog recording and metadata.
- `psychopy_trials.csv`: trial condition and actual PsychoPy flip timestamps; barcodes are no longer trial IDs.
- `barcode_events.csv`: periodic barcode schedule and event status.
- `trials_aligned.bin` plus `trials_aligned.schema.json`: compressed NumPy binary table and schema sidecar from alignment. Read via `load_aligned_bin()` in `analysis/align_session.py`.

`analysis/align_session.py` supports both older trial logs with a per-trial
`barcode` column and the current periodic-barcode workflow. For periodic
sessions, it reads `barcode_events.csv`, validates the fired schedule against
decoded NI barcodes, fits NI time to the scheduler clock, and aligns
`psychopy_trials.csv` by `flip_time_s`. Periodic barcodes are reported as
clock landmarks rather than trial identifiers.

For one-command validation of a periodic session, including direct OneBox
SY0 bit-6 decoding and photodiode onset checks, run:

```text
python tools/validate_sync_session.py "data\bench_YYYYMMDD_HHMMSS" ^
  "D:\recording\recording.imec0.ap.bin" ^
  "D:\recording\recording.imec0.ap.meta"
```

This writes `sync_barcode_validation.csv` and
`sync_stimulus_validation.csv` in the session directory. It intentionally
does not evaluate camera strobes or PsychoPy dropped-frame counts.

## Caution

Bench-test with no animal first. Confirm NI barcode loopback, OneBox SMA1/SY bit 6 decoding, photodiode onsets, and camera strobe cadence. The startup prompts coordinate human-operated applications but cannot prove they are recording; verify their status in MVS and SpikeGLX. Do not rely on the period alone as proof no events were dropped: compare the decoded code sequence and actual OneBox/NI event timestamps.
