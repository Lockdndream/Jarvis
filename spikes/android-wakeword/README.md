# android-wakeword spike

This was the Milestone 9B.5 disposable feasibility spike for on-device wake-word detection ("hey jarvis") using microWakeWord, package `com.jarvis.wakewordspike`.

## What it proved

Real-device validation on the Samsung S20 FE confirmed recall, false-positive rate, resource usage, and stability. Evidence and detailed results are recorded in `SESSION.md`.

## Where the code went

The implementation was ported into the production module in Milestone 9B.6 Task 7:

- `android/app/src/main/cpp/`
- `android/app/src/main/java/com/jarvis/companion/wakeword/`

The JNI class path was renamed from `com/jarvis/wakewordspike/MicroWakeWord` to `com/jarvis/companion/wakeword/MicroWakeWord`.

## Status

`android/` is now canonical. The original spike sources were removed in Milestone F1 task F1.5 to eliminate byte-identical duplication. Upstream Apache-2.0 attribution and the full provenance chain remain in `android/app/src/main/cpp/NOTICE.md`.
