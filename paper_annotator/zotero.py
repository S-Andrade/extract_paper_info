"""Find PDF attachments in the Zotero library and write annotations through the Web API."""

from dataclasses import dataclass
from pathlib import Path

from pyzotero import zotero

from .config import Config


@dataclass
class Paper:
    item: dict  # parent item (full API object)
    attachment_key: str
    pdf_path: Path

    @property
    def title(self) -> str:
        return self.item["data"].get("title", "(untitled)")

    @property
    def tags(self) -> set[str]:
        return {t["tag"] for t in self.item["data"].get("tags", [])}


def connect(cfg: Config, library_id: str, api_key: str) -> zotero.Zotero:
    return zotero.Zotero(library_id, cfg.library_type, api_key)


def _collection_key(zot: zotero.Zotero, name: str) -> str:
    for c in zot.everything(zot.collections()):
        if c["data"]["name"] == name or c["key"] == name:
            return c["key"]
    raise SystemExit(f"No Zotero collection named {name!r}")


def _pdf_path(cfg: Config, att: dict) -> Path | None:
    data = att["data"]
    if data.get("linkMode") == "linked_file":
        path = data.get("path", "")
        return Path(path).expanduser() if path and not path.startswith("attachments:") else None
    if data.get("filename"):
        return cfg.storage_dir / att["key"] / data["filename"]
    return None


def find_papers(zot: zotero.Zotero, cfg: Config, collection: str | None, tag: str | None,
                item_keys: list[str] | None) -> list[Paper]:
    if item_keys:
        items = [zot.item(key) for key in item_keys]
    elif collection:
        items = zot.everything(zot.collection_items_top(_collection_key(zot, collection), tag=tag or None))
    else:
        items = zot.everything(zot.top(tag=tag) if tag else zot.top())

    papers = []
    for item in items:
        if item["data"]["itemType"] in ("attachment", "note", "annotation"):
            continue
        for att in zot.children(item["key"], itemType="attachment"):
            if att["data"].get("contentType") != "application/pdf":
                continue
            path = _pdf_path(cfg, att)
            if path and path.exists():
                papers.append(Paper(item, att["key"], path))
                break  # one PDF per paper
    return papers


def delete_auto_annotations(zot: zotero.Zotero, cfg: Config, attachment_key: str) -> int:
    old = [a for a in zot.everything(zot.children(attachment_key, itemType="annotation"))
           if cfg.annotation_tag in {t["tag"] for t in a["data"].get("tags", [])}]
    for i in range(0, len(old), 50):
        zot.delete_item(old[i : i + 50])
    return len(old)


def create_annotations(zot: zotero.Zotero, annotations: list[dict]) -> int:
    created = 0
    for i in range(0, len(annotations), 50):
        resp = zot.create_items(annotations[i : i + 50])
        created += len(resp.get("successful", {}))
        for idx, err in resp.get("failed", {}).items():
            print(f"    ! Zotero rejected annotation {idx}: {err.get('message')}")
    return created


def mark_done(zot: zotero.Zotero, cfg: Config, paper: Paper) -> None:
    zot.add_tags(paper.item, cfg.done_tag)
