package com.jarvis.companion.wakeword

import java.util.UUID

/**
 * One wake-word detection event. [detectionId] is also used, unmodified,
 * as the com.jarvis.companion.voice layer's client_request_id when the
 * resulting VoiceSession open is requested (ADR-017) — one identifier
 * serves both local diagnostics/telemetry correlation and wire-protocol
 * correlation.
 *
 * [confidence] is null until a future native/JNI change exposes the
 * model's raw probability score — today's engine only returns the
 * already-thresholded boolean (disclosed gap, ADR-017).
 */
data class WakeWordDetection(
    val detectionId: UUID,
    val timestampMs: Long,
    val confidence: Float?,
    val modelVersion: String,
)
