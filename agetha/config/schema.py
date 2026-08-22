"""Conservative machine-readable metadata for stable settings."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SettingKind(str, Enum):
    INT = "int"
    FLOAT = "float"
    ENUM = "enum"


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    default: str
    kind: SettingKind
    group: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    choices: frozenset[str] = frozenset()
    restart_required: bool = False

    def __post_init__(self) -> None:
        if not self.key or self.key != self.key.upper():
            raise ValueError("SettingSpec keys must be non-empty uppercase names")
        if (self.minimum is None) != (self.maximum is None):
            raise ValueError(f"{self.key}: minimum and maximum must be paired")
        if self.kind is SettingKind.ENUM and not self.choices:
            raise ValueError(f"{self.key}: enum choices are required")
        if self.kind is not SettingKind.ENUM and self.choices:
            raise ValueError(f"{self.key}: choices apply only to enum settings")


def _build_setting_specs(specs: tuple[SettingSpec, ...]) -> dict[str, SettingSpec]:
    registry: dict[str, SettingSpec] = {}
    for spec in specs:
        if spec.key in registry:
            raise ValueError(f"Duplicate SettingSpec: {spec.key}")
        registry[spec.key] = spec
    return registry


SETTING_SPECS = _build_setting_specs((
    SettingSpec(
        "AI_MAX_TOKENS", "400", SettingKind.INT, "ai", 64, 8192,
        restart_required=True,
    ),
    SettingSpec(
        "HISTORY_LIMIT", "6", SettingKind.INT, "memory", 1, 20,
        restart_required=True,
    ),
    SettingSpec(
        "MEMORY_CHARS", "600", SettingKind.INT, "memory", 100, 5000,
        restart_required=True,
    ),
    SettingSpec(
        "EPISODIC_PROMPT_LIMIT", "10", SettingKind.INT, "memory", 0, 50,
        restart_required=True,
    ),
    SettingSpec(
        "AI_TEMPERATURE", "0.85", SettingKind.FLOAT, "ai", 0.0, 2.0,
        restart_required=True,
    ),
    SettingSpec(
        "AI_TOP_P", "0.95", SettingKind.FLOAT, "ai", 0.0, 1.0,
        restart_required=True,
    ),
    SettingSpec(
        "SCREEN_POLL_INTERVAL_SEC", "120", SettingKind.INT, "screen", 15, 3600,
        restart_required=False,
    ),
    SettingSpec(
        "OCR_MAX_DIMENSION", "2560", SettingKind.INT, "screen", 640, 8192,
        restart_required=True,
    ),
    SettingSpec(
        "OCR_FORCE_REFRESH_SECONDS", "20", SettingKind.FLOAT, "screen", 1.0, 3600.0,
        restart_required=True,
    ),
    SettingSpec(
        "OCR_PREPROCESSING",
        "auto",
        SettingKind.ENUM,
        "screen",
        choices=frozenset({"basic", "auto"}),
        restart_required=True,
    ),
    SettingSpec(
        "UNICODE_TYPING_MODE",
        "auto",
        SettingKind.ENUM,
        "typing",
        choices=frozenset({"auto", "unicode", "paste", "preview", "paced"}),
        restart_required=False,
    ),
))
