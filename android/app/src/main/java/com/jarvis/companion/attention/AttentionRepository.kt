package com.jarvis.companion.attention

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

class AttentionRepository {
    private val _outstanding = MutableStateFlow<List<AttentionRequest>>(emptyList())
    val outstanding: StateFlow<List<AttentionRequest>> = _outstanding.asStateFlow()

    private val _lastContactAtMs = MutableStateFlow<Long?>(null)
    val lastContactAtMs: StateFlow<Long?> = _lastContactAtMs.asStateFlow()

    fun applyPendingAttention(requests: List<AttentionRequest>) {
        val active = requests.filter { !AttentionStatus.isTerminal(it.status) }
        _outstanding.value = active
        _lastContactAtMs.value = System.currentTimeMillis()
    }

    fun applyAttentionEvent(request: AttentionRequest) {
        // .update{} (compare-and-set retry loop) rather than a manual
        // read-then-write: this repository is written to from a WebSocket
        // callback and must stay correct even if that ever becomes
        // concurrent — plain `.value` assignment is atomic per-write but
        // not across the read-modify-write sequence this needs.
        _outstanding.update { current ->
            val filtered = current.filterNot { it.attentionRequestId == request.attentionRequestId }
            if (AttentionStatus.isTerminal(request.status)) {
                filtered
            } else {
                listOf(request) + filtered
            }
        }
        _lastContactAtMs.value = System.currentTimeMillis()
    }

    fun clear() {
        _outstanding.value = emptyList()
        _lastContactAtMs.value = null
    }
}
