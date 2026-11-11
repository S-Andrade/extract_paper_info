# extract_paper_info

Reads your Zotero papers, finds the information you care about, and highlights it in the
Zotero reader — one color per kind of information — with a comment summarising what was found.
It also builds a table (`output/extractions.csv`) with one row per paper.

## Setup

```bash
python -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in the three values
```

- **ANTHROPIC_API_KEY**: from https://console.anthropic.com/settings/keys
- **ZOTERO_LIBRARY_ID** and **ZOTERO_API_KEY**: from https://www.zotero.org/settings/keys.
  The ID is "Your user ID for use in API calls". Create a new key with
  *Allow library access* and *Allow write access*.

Zotero must be syncing to zotero.org (annotations are created online and sync down to your app).

## What to extract

Edit `config.yaml`. Each field has a name, a description (what Claude looks for), and a color:

```yaml
fields:
  - name: sample
    description: Participants or data used (number, demographics, recruitment).
    color: green
```

For reading on a reMarkable Paper Pro, stick to yellow, green, blue, magenta (pink) and orange:
they stay distinct on its color e-ink. Purple, red and gray are too close to other colors there.

## Running

```bash
# Try it on one PDF first: writes an annotated copy to output/preview/, no Zotero needed
./venv/bin/python -m paper_annotator --pdf path/to/paper.pdf

# See what it would do for a collection without touching Zotero
./venv/bin/python -m paper_annotator --collection "Literature review" --limit 3 --dry-run --preview

# Annotate for real
./venv/bin/python -m paper_annotator --collection "Literature review"
./venv/bin/python -m paper_annotator --tag to-extract
./venv/bin/python -m paper_annotator --item ABCD1234
```

Processed papers get the tag `auto-annotated` and are skipped next time. Use `--force` to
redo them; the tool's old annotations (tagged `auto-extract`) are replaced, yours are never touched.
Each highlight is also tagged `extract:<field>`, so you can find e.g. all `extract:sample`
highlights across your library.

## Notes

- Scanned PDFs without a text layer are skipped — run OCR on them first.
- If Claude's quote can't be matched to the PDF text, it's listed as `?` in the output and
  not highlighted (the value still goes into the table).
- Cost is roughly $0.10–0.30 per paper with the default model; lower `effort` in
  `config.yaml` to make it cheaper.
