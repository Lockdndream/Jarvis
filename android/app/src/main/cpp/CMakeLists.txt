cmake_minimum_required(VERSION 3.22.1)
project("wakewordnativeinfra")

# Milestone 9B.6, Task 6: minimal JNI infrastructure proof only — no
# Android log/audio/native dependencies needed for a function that
# returns a constant. Deliberately not linking liblog (unused).
add_library(wakewordnativeinfra SHARED
    wakeword_self_test.cpp
)