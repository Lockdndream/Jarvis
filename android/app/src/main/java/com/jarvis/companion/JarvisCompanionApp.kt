package com.jarvis.companion

import android.app.Application
import com.jarvis.companion.attention.AttentionRepository
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.core.DeviceIdentity
import com.jarvis.companion.core.SecureConfigStore
import com.jarvis.companion.pairing.PairingClient
import com.jarvis.companion.pairing.PairingRepository
import com.jarvis.companion.telemetry.TelemetryRecorder
import com.jarvis.companion.voice.VoiceSessionRepository
import com.jarvis.companion.wakeword.WakeWordConfigRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Constructs this app's shared singletons once, so PresenceService and
 * every UI screen see the same PairingRepository/TelemetryRecorder rather
 * than each building its own (and disagreeing about pairing state or
 * missing each other's telemetry). No business logic lives here. */
class JarvisCompanionApp : Application() {
    lateinit var secureConfigStore: SecureConfigStore
        private set
    lateinit var deviceIdentity: DeviceIdentity
        private set
    lateinit var pairingRepository: PairingRepository
        private set
    lateinit var pairingClient: PairingClient
        private set
    lateinit var telemetry: TelemetryRecorder
        private set

    // Milestone 9B.3 (ADR-015): the one client-side mirror of server
    // attention state, fed exclusively by CompanionWebSocketClient's frame
    // parsing. Shared here so PresenceService (writer), AttentionActivity,
    // and AttentionWidgetProvider (readers) observe the same instance
    // rather than each holding a divergent copy.
    lateinit var attentionRepository: AttentionRepository
        private set

    // Milestone 9B.4: the one client-side mirror of the current voice
    // session, fed by CompanionWebSocketClient's voice_session_* frame
    // parsing, same sharing rationale as attentionRepository above — this
    // is what makes rotation/process-recreation survival possible for
    // VoiceActivity without it doing anything special itself.
    lateinit var voiceSessionRepository: VoiceSessionRepository
        private set

    // Milestone 9B.7: wake-word settings (opt-in enabled flag, confidence
    // threshold, diagnostic mode), same SecureConfigStore-backed pattern
    // as pairingRepository. Shared here so PresenceService (constructs
    // WakeWordManager from it) and a future Settings toggle read/write
    // the same instance.
    lateinit var wakeWordConfigRepository: WakeWordConfigRepository
        private set

    // App-wide, in-memory only (not persisted — this is live status, not
    // config). PresenceService is the sole writer; ConnectionStatusActivity
    // and any other observer only read it. Deliberately not a bound-service
    // Binder/Messenger: this single StateFlow is sufficient for the one
    // process-local reader this milestone has, without that machinery.
    private val _connectionState = MutableStateFlow(ConnectionState.DISCONNECTED)
    val connectionState: StateFlow<ConnectionState> = _connectionState

    fun updateConnectionState(state: ConnectionState) {
        _connectionState.value = state
    }

    override fun onCreate() {
        super.onCreate()
        secureConfigStore = SecureConfigStore(this)
        deviceIdentity = DeviceIdentity(secureConfigStore)
        pairingRepository = PairingRepository(secureConfigStore)
        pairingClient = PairingClient()
        telemetry = TelemetryRecorder(this)
        attentionRepository = AttentionRepository()
        voiceSessionRepository = VoiceSessionRepository()
        wakeWordConfigRepository = WakeWordConfigRepository(secureConfigStore)
        telemetry.record(TelemetryRecorder.APP_CREATED, "deviceId=${deviceIdentity.get()}")
    }
}
