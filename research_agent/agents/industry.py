"""Read the original Skill methods for the industry responsibility."""

from research_agent.skill_loader import load_skill


def methods() -> str:
    """Return authoritative material rather than a parallel research instruction."""
    return load_skill(task_type="industry").methodology
