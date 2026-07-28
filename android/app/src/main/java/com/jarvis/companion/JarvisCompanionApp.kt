package com.jarvis.companion

import android.app.Application
import com.jarvis.companion.attention.AttentionRepository
import com.jarvis.companion.core.ConnectionState
import com.jarvis.companion.core.DeviceIdentity
import com.jarvis.companion.core.SecureConfigStore
import com.jarvis.companion.opencode.OpenCodeTaskRepository
import com.jarvis.companion.pairing.PairingClient
import com.jarvis.companion.pairing.PairingRepository
import com.jarvis.companion.settings.ConnectivityPolicy
import com.jarvis.companion.settings.OperationalSettingsRepository
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

    // Interaction Layer v1 (Goals 2/3): client-side mirror of the most
    // recent delegated OpenCode task's lifecycle, same sharing rationale
    // as attentionRepository/voiceSessionRepository above — PresenceService
    // (writer, via CompanionWebSocketClient) and VoiceActivity/notifications
    // (readers) observe the same instance.
    lateinit var openCodeTaskRepository: OpenCodeTaskRepository
        private set

    // Milestone 9B.7: wake-word settings (opt-in enabled flag, confidence
    // threshold, diagnostic mode), same SecureConfigStore-backed pattern
    // as pairingRepository. Shared here so PresenceService (constructs
    // WakeWordManager from it) and a future Settings toggle read/write
    // the same instance.
    lateinit var wakeWordConfigRepository: WakeWordConfigRepository
        private set

    // Android Companion Integration v1.0 (ADR-023 Android-side enforcement):
    // cache of the connectivity policy JOPS defines/persists server-side,
    // same SecureConfigStore-backed sharing rationale as
    // wakeWordConfigRepository — PresenceService (enforcer) and the
    // Settings UI (display + Manual-mode Connect/Disconnect control) read
    // and write the same instance.
    lateinit var operationalSettingsRepository: OperationalSettingsRepository
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

    // Live mirror of operationalSettingsRepository.connectivityMode(), so
    // PresenceService's policy-enforcement collector reacts to a mode
    // change immediately (Phase 6: "Policy changes while connected/
    // disconnected") rather than only on the next settings-sync poll.
    // Seeded from the persisted cache so a fresh process starts from the
    // last known value, not always "always", before its first sync
    // completes.
    private val _connectivityMode = MutableStateFlow(ConnectivityPolicy.DEFAULT_MODE)
    val connectivityMode: StateFlow<String> = _connectivityMode

    fun updateConnectivityMode(mode: String) {
        operationalSettingsRepository.setConnectivityMode(mode)
        _connectivityMode.value = mode
    }

    // MODE_MANUAL only — mirrors operationalSettingsRepository.
    // manualConnectRequested() the same way connectivityMode mirrors its
    // own persisted value, for the same reactive-collector reason.
    private val _manualConnectRequested = MutableStateFlow(false)
    val manualConnectRequested: StateFlow<Boolean> = _manualConnectRequested

    fun updateManualConnectRequested(requested: Boolean) {
        operationalSettingsRepository.setManualConnectRequested(requested)
        _manualConnectRequested.value = requested
    }

    // App-wide, in-memory only, same "PresenceService is sole writer"
    // rationale as connectionState — whether the device's current default
    // network has the Wi-Fi transport, per ADR-023 Phase 2's MODE_WIFI_ONLY
    // enforcement. ConnectionStatusActivity's "Current Wi-Fi status" field
    // reads this rather than registering its own ConnectivityManager
    // callback.
    private val _wifiAvailable = MutableStateFlow(false)
    val wifiAvailable: StateFlow<Boolean> = _wifiAvailable

    fun updateWifiAvailable(available: Boolean) {
        _wifiAvailable.value = available
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
        openCodeTaskRepository = OpenCodeTaskRepository()
        wakeWordConfigRepository = WakeWordConfigRepository(secureConfigStore)
        operationalSettingsRepository = OperationalSettingsRepository(secureConfigStore)
        _connectivityMode.value = operationalSettingsRepository.connectivityMode()
        _manualConnectRequested.value = operationalSettingsRepository.manualConnectRequested()
        telemetry.record(TelemetryRecorder.APP_CREATED, "deviceId=${deviceIdentity.get()}")
    }
}
