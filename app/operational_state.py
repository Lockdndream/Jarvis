"""ADR-022/ADR-023: shared explicit service-lifecycle vocabulary, used by
every subsystem JOPS manages (OpenCodeSupervisor, JarvisProcessManager) so
"RUNNING"/"FAILED"/etc. mean the same thing everywhere rather than each
subsystem inventing its own near-identical state names.
"""


class OperationalState:
    STOPPED = "STOPPED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
