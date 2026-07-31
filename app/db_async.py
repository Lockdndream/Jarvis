"""Async wrappers over app.database (F1.15 / Architecture Audit 3a).

Every function here runs its synchronous app.database counterpart in a worker
thread via asyncio.to_thread, so an async handler never blocks the event loop
on SQLite I/O. Same names, same signatures, same return values as app.database
— only awaited.

app.database itself stays synchronous: its functions are called from sync
contexts too (43 sites in app/, 202 in tests/), and converting them would break
every one of those callers.

sqlite3.Connection objects cannot cross threads, so each wrapper's underlying
function must open and close its own connection — which app.database's
functions already do. Never hold a connection across an await.
"""
import asyncio
from app import database as db
from app import memory


async def get_task(task_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_task, task_id)


async def update_task_status(task_id: str, status: str, exit_code: int | None = None) -> None:
    return await asyncio.to_thread(db.update_task_status, task_id, status, exit_code)


async def get_recent_tasks(limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(db.get_recent_tasks, limit)


async def create_question_record(question_id: str, task_id: str, question_text: str, context: str | None, options_json: str | None) -> None:
    return await asyncio.to_thread(db.create_question_record, question_id, task_id, question_text, context, options_json)


async def answer_question_record(question_id: str, answer: str) -> None:
    return await asyncio.to_thread(db.answer_question_record, question_id, answer)


async def cancel_question_record(question_id: str) -> None:
    return await asyncio.to_thread(db.cancel_question_record, question_id)


async def get_question_record(question_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_question_record, question_id)


async def get_pending_questions() -> list[dict]:
    return await asyncio.to_thread(db.get_pending_questions)


async def update_opencode_task_status(task_id: str, status: str) -> None:
    return await asyncio.to_thread(db.update_opencode_task_status, task_id, status)


async def update_opencode_task_evidence(task_id: str, evidence_type: str) -> None:
    return await asyncio.to_thread(db.update_opencode_task_evidence, task_id, evidence_type)


async def update_opencode_task_result(task_id: str, result_summary: str) -> None:
    return await asyncio.to_thread(db.update_opencode_task_result, task_id, result_summary)


async def get_opencode_task(task_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_opencode_task, task_id)


async def get_opencode_task_by_session(session_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_opencode_task_by_session, session_id)


async def get_opencode_running_tasks() -> list[dict]:
    return await asyncio.to_thread(db.get_opencode_running_tasks)


async def get_opencode_degraded_tasks() -> list[dict]:
    return await asyncio.to_thread(db.get_opencode_degraded_tasks)


async def get_attention_request(attention_request_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_attention_request, attention_request_id)


async def get_attention_request_by_source(source_type: str, source_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_attention_request_by_source, source_type, source_id)


async def get_due_attention_requests(now: str | None = None) -> list[dict]:
    return await asyncio.to_thread(db.get_due_attention_requests, now)


async def get_unresolved_attention_requests() -> list[dict]:
    return await asyncio.to_thread(db.get_unresolved_attention_requests)


async def get_attention_requests_for_task(task_id: str) -> list[dict]:
    return await asyncio.to_thread(db.get_attention_requests_for_task, task_id)


async def transition_attention_status(
    attention_request_id: str,
    from_statuses: tuple[str, ...],
    to_status: str,
    **extra_fields: object,
) -> bool:
    return await asyncio.to_thread(db.transition_attention_status, attention_request_id, from_statuses, to_status, **extra_fields)


async def record_attention_contact(attention_request_id: str) -> None:
    return await asyncio.to_thread(db.record_attention_contact, attention_request_id)


async def create_contact_attempt(
    contact_attempt_id: str,
    attention_request_id: str,
    channel: str,
    notification_id: str | None = None,
    voice_session_id: str | None = None,
) -> dict:
    return await asyncio.to_thread(db.create_contact_attempt, contact_attempt_id, attention_request_id, channel, notification_id, voice_session_id)


async def update_contact_attempt_status(
    contact_attempt_id: str,
    status: str,
    result: str | None = None,
    error_code: str | None = None,
) -> None:
    return await asyncio.to_thread(db.update_contact_attempt_status, contact_attempt_id, status, result, error_code)


async def get_voice_session(voice_session_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_voice_session, voice_session_id)


async def set_pending_tool_call(voice_session_id: str, name: str, args: dict) -> None:
    return await asyncio.to_thread(db.set_pending_tool_call, voice_session_id, name, args)


async def clear_pending_tool_call(voice_session_id: str) -> None:
    return await asyncio.to_thread(db.clear_pending_tool_call, voice_session_id)


async def save_event(event_type: str, content: str | None = None, trace_id: str | None = None) -> None:
    return await asyncio.to_thread(db.save_event, event_type, content, trace_id)


async def get_push_subscriptions() -> list[dict]:
    return await asyncio.to_thread(db.get_push_subscriptions)


async def get_recent_events(limit: int = 100) -> list[dict]:
    return await asyncio.to_thread(db.get_recent_events, limit)


async def get_previous_conversation_boundary(current_conversation_id: str | None) -> str | None:
    return await asyncio.to_thread(db.get_previous_conversation_boundary, current_conversation_id)


async def create_task_record(task_id: str, name: str, command: str, trace_id: str | None = None) -> None:
    return await asyncio.to_thread(db.create_task_record, task_id, name, command, trace_id)


async def create_opencode_task_record(
    task_id: str, session_id: str, project_dir: str, instruction: str | None = None,
    trace_id: str | None = None,
) -> None:
    return await asyncio.to_thread(db.create_opencode_task_record, task_id, session_id, project_dir, instruction, trace_id)


async def create_attention_request(
    attention_request_id: str,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str,
    attention_type: str,
    urgency: str,
    summary: str,
    context_json: str | None,
    contact_policy: str | None,
    dedup_key: str,
) -> dict:
    return await asyncio.to_thread(
        db.create_attention_request,
        attention_request_id, conversation_id, task_id, source_type, source_id,
        attention_type, urgency, summary, context_json, contact_policy, dedup_key,
    )


async def create_notification(
    notification_id: str,
    conversation_id: str | None,
    task_id: str | None,
    source_type: str,
    source_id: str | None,
    notification_type: str,
    title: str,
    body: str,
    priority: str,
    dedup_key: str,
) -> dict:
    return await asyncio.to_thread(
        db.create_notification,
        notification_id, conversation_id, task_id, source_type, source_id,
        notification_type, title, body, priority, dedup_key,
    )


async def mark_notification_delivered(notification_id: str) -> None:
    return await asyncio.to_thread(db.mark_notification_delivered, notification_id)


async def delete_push_subscription(endpoint: str) -> None:
    return await asyncio.to_thread(db.delete_push_subscription, endpoint)


# ── Plans and plan steps ─────────────────────────────────────────────


async def create_plan_record(plan_id: str, title: str, context_json: str | None = None) -> None:
    return await asyncio.to_thread(db.create_plan_record, plan_id, title, context_json)


async def create_plan_step_record(
    step_id: str,
    plan_id: str,
    step_index: int,
    description: str,
    worker_name: str,
    on_failure: str = "stop",
    verification: str | None = None,
    depends_on_json: str | None = None,
) -> None:
    return await asyncio.to_thread(db.create_plan_step_record, step_id, plan_id, step_index, description, worker_name, on_failure, verification, depends_on_json)


async def get_plan(plan_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_plan, plan_id)


async def get_plan_steps(plan_id: str) -> list[dict]:
    return await asyncio.to_thread(db.get_plan_steps, plan_id)


async def get_recent_plans(limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(db.get_recent_plans, limit)


async def get_plans_by_status(status: str) -> list[dict]:
    return await asyncio.to_thread(db.get_plans_by_status, status)


async def claim_plan_start(plan_id: str) -> bool:
    return await asyncio.to_thread(db.claim_plan_start, plan_id)


async def update_plan_status(plan_id: str, status: str, current_step_index: int | None = None) -> None:
    return await asyncio.to_thread(db.update_plan_status, plan_id, status, current_step_index)


async def update_plan_step(
    step_id: str,
    *,
    status: str | None = None,
    worker_task_id: str | None = None,
    result: str | None = None,
    error: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    verification_task_id: str | None = None,
    verification_result: str | None = None,
    verification_error: str | None = None,
) -> None:
    return await asyncio.to_thread(
        db.update_plan_step,
        step_id,
        status=status,
        worker_task_id=worker_task_id,
        result=result,
        error=error,
        started_at=started_at,
        completed_at=completed_at,
        verification_task_id=verification_task_id,
        verification_result=verification_result,
        verification_error=verification_error,
    )


async def get_plan_step(step_id: str) -> dict | None:
    return await asyncio.to_thread(db.get_plan_step, step_id)


# ── Memory wrappers ─────────────────────────────────────────────────


async def store_memory(
    category: str,
    content: str,
    project: str | None = None,
    source: str = "conversation",
    source_id: str | None = None,
    metadata: str | None = None,
    expires_at: str | None = None,
) -> str:
    return await asyncio.to_thread(memory.store_memory, category, content, project, source, source_id, metadata, expires_at)


async def retrieve_memories(query: str, project: str | None = None, limit: int = 5) -> list[dict]:
    return await asyncio.to_thread(memory.retrieve_memories, query, project, limit)


async def get_core_facts() -> list[dict]:
    return await asyncio.to_thread(memory.get_core_facts)


async def update_memory(id: str, content: str) -> None:
    return await asyncio.to_thread(memory.update_memory, id, content)


async def delete_memory(id: str) -> None:
    return await asyncio.to_thread(memory.delete_memory, id)


async def get_memories_by_source(source: str, source_id: str | None) -> list[dict]:
    return await asyncio.to_thread(memory.get_memories_by_source, source, source_id)


async def get_recent_activity(since: str | None = None, project: str | None = None, limit: int = 20) -> list[dict]:
    return await asyncio.to_thread(memory.get_recent_activity, since, project, limit)


async def get_activity_summary(since: str | None = None, project: str | None = None) -> str:
    return await asyncio.to_thread(memory.get_activity_summary, since, project)


async def get_recent_memories(project: str | None = None, limit: int = 10) -> list[dict]:
    return await asyncio.to_thread(memory.get_recent_memories, project, limit)
