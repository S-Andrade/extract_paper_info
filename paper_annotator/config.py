from dataclasses import dataclass
from pathlib import Path

import yaml

# Zotero reader's built-in highlight colors.
ZOTERO_COLORS = {
    "yellow": "#ffd400",
    "red": "#ff6666",
    "green": "#5fb236",
    "blue": "#2ea8e5",
    "purple": "#a28ae5",
    "magenta": "#e56eee",
    "orange": "#f19837",
    "gray": "#aaaaaa",
}


@dataclass
class Field:
    name: str
    description: str
    color: str | None  # hex; None = summary-only field, not highlighted


@dataclass
class Config:
    context: str
    fields: list[Field]
    library_type: str
    storage_dir: Path
    annotation_tag: str
    done_tag: str
    model: str
    effort: str
    max_quotes_per_field: int

    def field(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)


def _color(value: str) -> str:
    value = value.strip().lower()
    if value in ZOTERO_COLORS:
        return ZOTERO_COLORS[value]
    if value.startswith("#") and len(value) == 7:
        return value
    raise ValueError(f"Unknown color {value!r}; use one of {', '.join(ZOTERO_COLORS)} or a #rrggbb hex")


def load_config(path: str | Path) -> Config:
    raw = yaml.safe_load(Path(path).read_text())
    fields = [Field(f["name"], f["description"], _color(f["color"]) if f.get("color") else None)
              for f in raw["fields"]]
    if len({f.name for f in fields}) != len(fields):
        raise ValueError("Field names in config must be unique")
    z = raw.get("zotero", {})
    c = raw.get("claude", {})
    return Config(
        context=raw.get("context", "").strip(),
        fields=fields,
        library_type=z.get("library_type", "user"),
        storage_dir=Path(z.get("storage_dir", "~/Zotero/storage")).expanduser(),
        annotation_tag=z.get("annotation_tag", "auto-extract"),
        done_tag=z.get("done_tag", "auto-annotated"),
        model=c.get("model", "claude-haiku-5-5"),
        effort=c.get("effort", "high"),
        max_quotes_per_field=c.get("max_quotes_per_field", 5),
    )
