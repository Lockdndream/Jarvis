#include <jni.h>

extern "C"
JNIEXPORT jint JNICALL
Java_com_jarvis_companion_wakeword_WakeWordNativeSelfTest_nativeSelfTest(JNIEnv *env, jobject /* this */) {
    return 42;
}