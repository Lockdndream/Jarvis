package com.jarvis.companion.wakeword

/**
 * Milestone 9B.6, Task 6: proves the minimal Kotlin/JNI/C++/NDK boundary
 * actually builds, loads, and executes inside the production android/
 * module (which had zero native code before this task). Deliberately
 * trivial — a future task replaces/supersedes this with the real vendored
 * microWakeWord engine (already proven separately in the disposable
 * spikes/android-wakeword/ spike); this class does not implement any
 * wake-word logic and should not be extended to do so.
 */
object WakeWordNativeSelfTest {
    init {
        System.loadLibrary("wakewordnativeinfra")
    }

    /** Returns the deterministic constant 42 from native code — the
     * exact value proves the JNI round trip actually executed native
     * code rather than e.g. silently returning a JVM default. */
    external fun nativeSelfTest(): Int
}