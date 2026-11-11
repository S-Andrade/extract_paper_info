"""Highlight configured information in papers, in Zotero or in a local PDF.

    python -m paper_annotator --pdf paper.pdf          # try it on one file, no Zotero needed
    python -m paper_annotator --collection "My review" # annotate a Zotero collection
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import anthropic
import pymupdf
from dotenv import load_dotenv

from . import pdf, zotero
from .config import Config, load_config
from .extract import Extraction, ExtractionError, extract
from .extract_codex import extract_codex

OUTPUT = Path("output")


def analyse(client, cfg: Config, doc: pymupdf.Document, provider: str = "anthropic"):
    """Run extraction and locate every quote. Returns (extraction, [(match, field, value)], missed quotes)."""
    pages = pdf.page_texts(doc)
    result = extract_codex(cfg, pages) if provider == "codex" else extract(client, cfg, pages)
    found, missed = [], []
    for fr in result.fields:
        field = cfg.field(fr.field)
        if field is None or field.color is None:
            continue
        for q in fr.quotes:
            m = pdf.locate(doc, q.text, q.page - 1)
            if m:
                found.append((m, field, fr.value))
            else:
                missed.append((field.name, q.page, q.text))
    return result, found, missed


def report(title: str, result: Extraction, found: list, missed: list) -> None:
    print(f"  {len(found)} highlights, {len(missed)} quotes not found in the PDF")
    for fr in result.fields:
        print(f"    {fr.field}: {fr.value or '(blank — not in the paper)'}")
    for name, page, text in missed:
        print(f"    ? [{name}] p.{page}: {text[:90]}...")


def save_results(cfg: Config, key: str, title: str, result: Extraction) -> None:
    OUTPUT.mkdir(exist_ok=True)
    (OUTPUT / "json").mkdir(exist_ok=True)
    (OUTPUT / "json" / f"{key}.json").write_text(
        json.dumps({"key": key, "title": title, **result.model_dump()}, indent=2, ensure_ascii=False))
    table = OUTPUT / "extractions.csv"
    evidence_fields = [f.name for f in cfg.fields if f.color]
    columns = ["key", "title"] + [f.name for f in cfg.fields] + [f"{n}_evidence" for n in evidence_fields]
    rows = []
    if table.exists():
        with table.open(newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r["key"] != key]
    by_name = {fr.field: fr for fr in result.fields}
    row = {"key": key, "title": title}
    for f in cfg.fields:
        fr = by_name.get(f.name)
        row[f.name] = fr.value if fr else ""
        if f.color:
            row[f"{f.name}_evidence"] = " | ".join(f'"{q.text}" (p. {q.page})' for q in fr.quotes) if fr else ""
    rows.append(row)
    with table.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def to_zotero_annotation(cfg: Config, doc, attachment_key: str, m: pdf.Match, field, value: str) -> dict:
    return {
        "itemType": "annotation",
        "parentItem": attachment_key,
        "annotationType": "highlight",
        "annotationText": m.text,
        "annotationComment": f"{field.name}: {value}",
        "annotationColor": field.color,
        "annotationPageLabel": m.page_label,
        "annotationSortIndex": pdf.zotero_sort_index(m),
        "annotationPosition": json.dumps(pdf.zotero_position(doc, m)),
        "tags": [{"tag": cfg.annotation_tag}, {"tag": f"extract:{field.name}"}],
    }


def run_local(client, cfg: Config, path: Path, provider: str = "anthropic") -> None:
    doc = pymupdf.open(path)
    if not pdf.has_text_layer(doc):
        sys.exit("This PDF has no text layer (scanned?). Run OCR on it first.")
    print(f"Reading {path.name} ...")
    result, found, missed = analyse(client, cfg, doc, provider)
    report(path.stem, result, found, missed)
    out = OUTPUT / "preview" / f"{path.stem}.annotated.pdf"
    pdf.write_preview(doc, [(m, f.color, f"{f.name}: {v}") for m, f, v in found], out)
    save_results(cfg, path.stem, path.stem, result)
    print(f"\nAnnotated copy: {out}\nTable: {OUTPUT / 'extractions.csv'}")


def run_zotero(client, cfg: Config, args, provider: str = "anthropic") -> None:
    library_id, api_key = os.environ.get("ZOTERO_LIBRARY_ID"), os.environ.get("ZOTERO_API_KEY")
    if not library_id or not api_key:
        sys.exit("Set ZOTERO_LIBRARY_ID and ZOTERO_API_KEY in .env (see .env.example).")
    zot = zotero.connect(cfg, library_id, api_key)
    papers = zotero.find_papers(zot, cfg, args.collection, args.tag, args.item)
    if not args.force:
        papers = [p for p in papers if cfg.done_tag not in p.tags]
    if args.limit:
        papers = papers[: args.limit]
    print(f"{len(papers)} paper(s) to process" + (" (dry run)" if args.dry_run else ""))

    for i, paper in enumerate(papers, 1):
        print(f"\n[{i}/{len(papers)}] {paper.title}")
        doc = pymupdf.open(paper.pdf_path)
        if not pdf.has_text_layer(doc):
            print("  skipped: no text layer (scanned PDF? run OCR first)")
            continue
        try:
            result, found, missed = analyse(client, cfg, doc, provider)
        except (ExtractionError, anthropic.APIError) as e:
            print(f"  skipped: {e}")
            continue
        report(paper.title, result, found, missed)
        save_results(cfg, paper.item["key"], paper.title, result)
        if args.preview:
            out = OUTPUT / "preview" / f"{paper.item['key']}.annotated.pdf"
            pdf.write_preview(doc, [(m, f.color, f"{f.name}: {v}") for m, f, v in found], out)
        if args.dry_run:
            continue
        removed = zotero.delete_auto_annotations(zot, cfg, paper.attachment_key)
        annotations = [to_zotero_annotation(cfg, doc, paper.attachment_key, m, f, v) for m, f, v in found]
        created = zotero.create_annotations(zot, annotations)
        zotero.mark_done(zot, cfg, paper)
        print(f"  Zotero: {created} annotations created" + (f", {removed} old ones replaced" if removed else ""))


def main() -> None:
    parser = argparse.ArgumentParser(prog="paper_annotator", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--provider", choices=("anthropic", "codex"), default="anthropic",
                        help="AI account to use: Anthropic API (default) or your signed-in Codex account")
    parser.add_argument("--pdf", type=Path, help="process one local PDF instead of Zotero (writes an annotated copy)")
    parser.add_argument("--collection", help="Zotero collection name (or key) to process")
    parser.add_argument("--tag", help="only process papers with this Zotero tag")
    parser.add_argument("--item", nargs="+", metavar="KEY", help="process specific Zotero items by key")
    parser.add_argument("--limit", type=int, help="process at most N papers")
    parser.add_argument("--force", action="store_true", help="re-process papers already marked done")
    parser.add_argument("--dry-run", action="store_true", help="extract and report, but don't write to Zotero")
    parser.add_argument("--preview", action="store_true", help="also save annotated PDF copies in output/preview")
    args = parser.parse_args()

    load_dotenv()
    cfg = load_config(args.config)
    client = anthropic.Anthropic() if args.provider == "anthropic" else None
    if args.pdf:
        run_local(client, cfg, args.pdf, args.provider)
    else:
        run_zotero(client, cfg, args, args.provider)


if __name__ == "__main__":
    main()
