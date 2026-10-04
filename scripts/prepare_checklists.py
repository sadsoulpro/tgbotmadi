"""Prepare the supplied checklist PDFs for the current Telegram bot username.

Requires pypdf. The originals stay untouched; run again after a BotFather rename.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject, TextStringObject


CHECKLISTS = (
    ("Чек-лист Хороший или удобный.pdf", "checklist_udobniy", 1),
    ("Чек-лист «Муж или сын».pdf", "checklist_rebenok", 0),
    ("Чек-лист «Живёшь на автомате».pdf", "checklist_avtomat", 0),
    ("Чек-лист про гнев.pdf", "checklist_gnev", 0),
    ("Чек-лист «Один среди своих».pdf", "checklist_odin", 0),
)


def prepare(source_dir: Path, output_dir: Path, username: str) -> None:
    username = username.lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        raise ValueError("Invalid Telegram username")
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, tag, source_page in CHECKLISTS:
        source = source_dir / filename
        reader = PdfReader(source)
        writer = PdfWriter()
        writer.add_page(reader.pages[source_page])
        link = f"https://t.me/{username}?start={tag}"
        annotations = writer.pages[0].get("/Annots") or []
        found_link = False
        for reference in annotations:
            annotation = reference.get_object()
            if annotation.get("/Subtype") == "/Link" and annotation.get("/A"):
                annotation["/A"].get_object()[NameObject("/URI")] = TextStringObject(link)
                found_link = True
        if not found_link:
            # Covers the visible purple CTA in the supplied "Муж или сын" PDF.
            writer.add_uri(0, link, (45, 80, 765, 165),
                           border=ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)]))
        with (output_dir / f"{tag}.pdf").open("wb") as stream:
            writer.write(stream)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    prepare(args.source_dir, args.output_dir, args.username)
