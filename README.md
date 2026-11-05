# Octopus Neuropixels synchronization rig

PsychoPy stimuli are synchronized with a NI USB-6421, a Neuropixels OneBox and a Hikrobot camera. NI AI records the barcode loopback, photodiode and camera exposure strobe. The barcode links NI time to OneBox time; the photodiode measures actual visible stimulus onset.

## Timing configuration

`config.py` sets `DISPLAY_REFRESH_HZ = 319.98` and `STIM_DURATION_S = 0.5`. `run_experiment.py` measures the display rate at startup and uses it to select the nearest whole number of stimulus frames (about 160 frames at 319.98 Hz). It reports a warning if the measured refresh differs from configuration by more than 1%. The PsychoPy CSV records the selected frame count and rate.

Camera acquisition is configured separately: `CAMERA_FPS = 60`. `CAMERA_PREVIEW_FPS = 30` describes the MVS preview only; preview/display rate does not set sensor acquisition. Confirm in MVS that the camera is acquiring and recording at 60 fps, and inspect its dropped-frame count. The alignment output's `camera_frame` / `camera_strobe_index` is the index of the NI-recorded exposure strobe, not guaranteed to be a saved-video frame number if frames were dropped or omitted by recording.

## Running

Install Python 3.11, NI-DAQmx driver, and dependencies from `requirements.txt`. Run `python run_experiment.py --subject M01`; use `--simulate` for a no-hardware PsychoPy test. Run `python tests/test_decoder.py` for the decoder test. Start SpikeGLX, then the experiment and NI, then start MVS camera acquisition/recording before starting trials.

## Alignment

After acquisition, run `python analysis/align_session.py data/<session> --onebox onebox_barcode_edges.csv`. For strongest protection against a truncated last barcode, pass the actual recording end time in the OneBox CSV's time base: `--onebox-end-s 1234.5`. You do not need to predict duration: obtain it from the finished recording's sample count/rate or metadata. Without this option, the script warns that final-barcode completeness cannot be verified.

MVS per-frame counters/timestamps are optional for initial hardware checks. They are required to prove exact correspondence between saved-video frame numbers and NI strobe indices, particularly if frames are dropped. SDK acquisition scripts generally need exclusive camera access; stop MVS acquisition before running a separate SDK logger.

## Safety and validation

This project controls timing and recording only. Before experimental use, bench-test event counts, TTL levels at the OneBox input, photodiode contrast, camera strobes, recording completeness, and the alignment outputs. Verify OneBox input voltage/termination against its documentation; do not infer SMA1 compatibility from the NI loopback alone.
