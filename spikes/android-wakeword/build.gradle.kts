// Milestone 9B.5 disposable feasibility spike (D5) — root build file, deliberately
// minimal. Not production code. Proves whether the home-assistant/android
// microWakeWord reference architecture (NDK/CMake + TensorFlow Lite Micro JNI)
// can be built and run on the real S20 FE. See README.md.
plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
