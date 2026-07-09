"""Safe project alias resolution for the Jarvis supervisor."""
import json
import os

PROJECTS_FILE = os.environ.get(
    "JARVIS_PROJECTS_FILE",
    os.path.join(os.path.dirname(__file__), "..", "..", "projects.json"),
)

_default_projects = {}


def _load_projects() -> dict:
    try:
        if os.path.exists(PROJECTS_FILE):
            with open(PROJECTS_FILE, encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, OSError):
        pass
    return dict(_default_projects)


def set_default_projects(projects: dict) -> None:
    _default_projects.clear()
    _default_projects.update(projects)


def get_projects() -> dict:
    """Return all configured safe projects."""
    return dict(_load_projects())


def resolve_project(ref: str) -> tuple[str | None, str | None]:
    """Resolve a user-friendly reference to a (alias, path) or (None, reason).

    Resolution order:
      1. Exact alias match
      2. Display name match
      3. Case-insensitive substring match on alias
      4. Case-insensitive substring match on display name
    """
    projects = _load_projects()
    if not projects:
        return None, "No projects configured"

    ref_lower = ref.lower().strip()

    # Exact alias
    if ref_lower in projects:
        p = projects[ref_lower]
        return ref_lower, p.get("path")

    # Display name
    for alias, info in projects.items():
        if info.get("display_name", "").lower() == ref_lower:
            return alias, info.get("path")

    # Substring on alias
    matches = [(a, i) for a, i in projects.items() if ref_lower in a.lower()]
    if len(matches) == 1:
        return matches[0][0], matches[0][1].get("path")

    # Substring on display name
    matches = [(a, i) for a, i in projects.items() if ref_lower in i.get("display_name", "").lower()]
    if len(matches) == 1:
        return matches[0][0], matches[0][1].get("path")

    if len(matches) > 1:
        names = [f"'{a}'" for a, _ in matches]
        return None, f"Multiple projects match '{ref}': {', '.join(names)}"

    all_names = [f"'{a}' ({i.get('display_name', '')})" for a, i in projects.items()]
    return None, f"Unknown project '{ref}'. Available: {', '.join(all_names)}"
