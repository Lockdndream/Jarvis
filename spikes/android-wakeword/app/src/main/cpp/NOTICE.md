The files in this directory (`Logging.h`, `MicroFrontendWrapper.h/.cpp`,
`MicroWakeWordEngine.h/.cpp`, `MicroWakeWord_jni.cpp`, `CMakeLists.txt`) are
vendored, with only the JNI class path changed (`io/homeassistant/companion/android/microwakeword/MicroWakeWord`
-> `com/jarvis/wakewordspike/MicroWakeWord`), from:

    home-assistant/android, microwakeword/src/main/cpp/
    https://github.com/home-assistant/android
    Commit: master branch as of 2026-07-14
    License: Apache License 2.0

`app/src/main/java/com/jarvis/wakewordspike/MicroWakeWord.kt` is likewise
vendored (package renamed) from
`microwakeword/src/main/kotlin/io/homeassistant/companion/android/microwakeword/MicroWakeWord.kt`
in the same repository, same license.

`app/src/main/assets/hey_jarvis.tflite` and the parameters in
`WakeWordSpikeActivity.kt` derived from `hey_jarvis.json` are from
`esphome/micro-wake-word-models`, `models/v2/`, also Apache License 2.0.

This is a disposable Milestone 9B.5 feasibility spike (the D5 gate deferred
since Milestone 9A/9B.0) — not production code, not built upon directly by
`android/` (the production Jarvis companion). See `README.md`.
