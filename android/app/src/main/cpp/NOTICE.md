The files in this directory (`Logging.h`, `MicroFrontendWrapper.h/.cpp`,
`MicroWakeWordEngine.h/.cpp`, `MicroWakeWord_jni.cpp`, `CMakeLists.txt`) are
vendored, with only the JNI class path changed, from:

    home-assistant/android, microwakeword/src/main/cpp/
    https://github.com/home-assistant/android
    Commit: master branch as of 2026-07-14
    License: Apache License 2.0

Provenance chain (JNI class path at each step):
1. `io/homeassistant/companion/android/microwakeword/MicroWakeWord` — original.
2. `com/jarvis/wakewordspike/MicroWakeWord` — Milestone 9B.5 disposable
   feasibility spike (`spikes/android-wakeword/`), real-device-proven on
   the S20 FE (recall/false-positive/resource/stability evidence in
   `SESSION.md`).
3. `com/jarvis/companion/wakeword/MicroWakeWord` — **this copy**, ported
   into the production `android/` module, Milestone 9B.6 Task 7. The C++
   files above are byte-identical to step 2's spike source except this
   file's own `JNI_OnLoad`, whose hardcoded `FindClass` class-path string
   was updated to match this step's package rename (verified independently
   — a stale path here would silently fail `RegisterNatives`, causing
   every native method to throw `UnsatisfiedLinkError` at first use).

`app/src/main/java/com/jarvis/companion/wakeword/MicroWakeWord.kt` is
likewise vendored (package renamed at each step above) from
`microwakeword/src/main/kotlin/io/homeassistant/companion/android/microwakeword/MicroWakeWord.kt`
in the home-assistant/android repository, same license.

`app/src/main/assets/hey_jarvis.tflite` is from
`esphome/micro-wake-word-models`, `models/v2/`, also Apache License 2.0.

This is production code as of Milestone 9B.6 Task 7 — no longer disposable.
The engine is exposed but not yet wired into any lifecycle/audio-capture/
state-machine logic (explicitly out of scope for Task 7; see a later
milestone's `WakeWordManager`). See `spikes/android-wakeword/README.md`
for the original disposable spike this was ported from.
