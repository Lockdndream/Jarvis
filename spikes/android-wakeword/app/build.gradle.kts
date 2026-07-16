plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.jarvis.wakewordspike"
    compileSdk = 34
    ndkVersion = "28.2.13676358"

    defaultConfig {
        applicationId = "com.jarvis.wakewordspike"
        minSdk = 33
        targetSdk = 34
        versionCode = 1
        versionName = "0.1-spike"

        // Real device (S20 FE, Snapdragon 865) is arm64-v8a only — no need to
        // build/ship other ABIs for a disposable feasibility spike.
        ndk {
            abiFilters += "arm64-v8a"
        }

        externalNativeBuild {
            cmake {
                cppFlags += ""
            }
        }
    }

    externalNativeBuild {
        cmake {
            path = file("src/main/cpp/CMakeLists.txt")
            version = "3.22.1"
        }
    }

    buildTypes {
        debug {
            isMinifyEnabled = false
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
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    testImplementation("junit:junit:4.13.2")
}
