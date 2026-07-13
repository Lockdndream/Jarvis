plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.jarvis.companion"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.jarvis.companion"
        // minSdk 33 matches the only real device this app targets so far
        // (Galaxy S20 FE, Android 13) — see ARCHITECTURE.md / SESSION.md
        // Milestone 9B.0 real-device validation.
        minSdk = 33
        targetSdk = 34
        versionCode = 1
        versionName = "0.1.0"
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
        }
        // No release build type is configured. Milestone 9B.1 is scoped to
        // dev-only signing (see signingConfigs.debug below) — production
        // release signing is a deliberately separate, later decision, not
        // an oversight.
    }

    signingConfigs {
        getByName("debug") {
            // AGP's implicit auto-generated ~/.android/debug.keystore.
            // Explicit here (rather than left implicit) so it's visible as
            // a documented decision: this is the only signing config this
            // milestone ships, and it is not suitable for distribution.
            storeFile = file(System.getProperty("user.home") + "/.android/debug.keystore")
            storePassword = "android"
            keyAlias = "androiddebugkey"
            keyPassword = "android"
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        viewBinding = true
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("androidx.security:security-crypto:1.1.0-alpha06")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.4")
    implementation("androidx.activity:activity-ktx:1.9.1")

    testImplementation("junit:junit:4.13.2")
    testImplementation("org.jetbrains.kotlinx:kotlinx-coroutines-test:1.8.1")
    // Only used to stand in for an X509Certificate in PinnedTrustManagerTest
    // — constructing a real, validly-encoded certificate in a plain JUnit
    // (non-instrumented) test isn't practical without a much heavier
    // dependency (e.g. Bouncy Castle) this foundation doesn't otherwise need.
    testImplementation("org.mockito:mockito-core:5.12.0")
    // Milestone 9B.3: AttentionParser uses org.json for real nested/array
    // JSON parsing (unlike DeviceStatus/ws_tokens' flat, escaping-free
    // payloads, attention_* messages have genuine structure hand-rolled
    // string parsing would be a real correctness risk for). org.json is
    // Android-stubbed (throws) in local JVM unit tests by default — this
    // is the standard, well-known workaround: a real implementation on
    // the test classpath takes priority over the Android stub jar.
    testImplementation("org.json:json:20240303")
    // Milestone 9B.2: WsTokenClient makes real HTTPS calls (through the
    // pinned TLS trust manager) — MockWebServer lets its unit tests run a
    // real local HTTP server instead of mocking OkHttp's internals.
    testImplementation("com.squareup.okhttp3:mockwebserver:4.12.0")
}
