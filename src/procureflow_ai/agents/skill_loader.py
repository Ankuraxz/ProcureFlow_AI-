"""Procedural memory (`skills/*/SKILL.md`) loader for ADK `SkillToolset`."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from google.adk.skills import Skill, load_skill_from_dir
from google.adk.tools.skill_toolset import SkillDiscoveryMode, SkillToolset

from ..config import get_skills_dir


def load_domain_skills(
    skill_names: Sequence[str],
    skills_dir: Path | str | None = None,
) -> list[Skill]:
    """Load named `SKILL.md` directories into ADK `Skill` models."""
    base = Path(skills_dir).resolve() if skills_dir else get_skills_dir()
    loaded: list[Skill] = []
    for name in skill_names:
        skill_path = base / name
        loaded.append(load_skill_from_dir(skill_path))
    return loaded


def build_lazy_skill_toolset(
    skill_names: Sequence[str],
    skills_dir: Path | str | None = None,
) -> SkillToolset:
    """Create a progressive-disclosure `SkillToolset` configured with `SkillDiscoveryMode.LAZY`."""
    skills = load_domain_skills(skill_names, skills_dir=skills_dir)
    return SkillToolset(
        skills=skills,
        discovery_mode=SkillDiscoveryMode.LAZY,
    )
