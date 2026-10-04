"""Restore DOCX OLE preview images in Anydoc Markdown using local LibreOffice.

Requires Pillow and soffice. Reads the main document's w:object elements;
matches each OLE ShapeID to its VML preview and resolves package relationships.
Neither embedded OLE programs nor external relationships are executed/fetched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from PIL import Image, ImageChops

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "o": "urn:schemas-microsoft-com:office:office",
    "v": "urn:schemas-microsoft-com:vml",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
}
SVG = "{http://www.w3.org/2000/svg}"


def _part(rels: dict, rel_id: str | None) -> str:
    rel = rels.get(rel_id)
    if rel is None or rel.get("TargetMode") == "External":
        raise ValueError(f"OLE preview requires an internal relationship: {rel_id}")
    target = unquote(rel["Target"])
    part = posixpath.normpath(posixpath.join("word", target))
    if part.startswith("/"):
        part = part.lstrip("/")
    if part == ".." or part.startswith("../") or ":" in part:
        raise ValueError(f"Invalid package relationship: {rel_id}")
    return part


def read_previews(source: Path) -> list[dict]:
    """Read objects in body order, selecting previews by ShapeID, not filenames."""
    records = []
    with zipfile.ZipFile(source) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
        rels = {
            rel.get("Id"): rel.attrib
            for rel in ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        }
        for obj in root.findall(".//w:body//w:object", NS):
            ole = obj.find(".//o:OLEObject", NS)
            if ole is None:
                continue
            shape_id = ole.get("ShapeID")
            shapes = [s for s in obj.findall(".//v:shape", NS) if s.get("id") == shape_id]
            if not shape_id or len(shapes) != 1:
                raise ValueError(f"Cannot identify OLE preview shape: {shape_id}")
            preview = shapes[0].find(".//v:imagedata", NS)
            if preview is None:
                raise ValueError(f"OLE object has no preview image: {shape_id}")
            image_rel = preview.get(f"{{{NS['r']}}}id")
            ole_rel = ole.get(f"{{{NS['r']}}}id")
            image_part = _part(rels, image_rel)
            payload = archive.read(image_part)
            if not payload:
                raise ValueError(f"Empty preview image: {image_part}")
            prog_id = ole.get("ProgID", "object")
            doc_pr = obj.find(".//wp:docPr", NS)
            descr = doc_pr.get("descr", "").strip() if doc_pr is not None else ""
            if descr:
                raise ValueError("Custom OLE descriptions need explicit Markdown mapping")
            records.append({
                "index": len(records) + 1,
                "shape_id": shape_id,
                "prog_id": prog_id,
                "ole_relationship_id": ole_rel,
                "ole_part": _part(rels, ole_rel),
                "image_relationship_id": image_rel,
                "image_part": image_part,
                "image_sha256": hashlib.sha256(payload).hexdigest(),
                "payload": payload,
            })
    return records


def image_markdown(markdown: str, records: list[dict], directory: str) -> str:
    """Require an exact placeholder sequence before replacing repeated labels."""
    labels = [f"Embedded object: {r['prog_id']}" for r in records]
    if not labels:
        return markdown
    pattern = re.compile("|".join(re.escape(s) for s in sorted(set(labels), key=len, reverse=True)))
    if [m.group() for m in pattern.finditer(markdown)] != labels:
        raise ValueError("Markdown OLE placeholders differ from DOCX; refusing ambiguous mapping")
    replacements = iter(records)

    def replace(match):
        record = next(replacements)
        stem = f"{directory}/ole-{record['index']:03d}"
        return f"![OLE 预览 {record['index']}]({stem}.png) ([SVG]({stem}.svg))"

    return pattern.sub(replace, markdown)


def crop_png(path: Path) -> tuple[int, int]:
    with Image.open(path) as original:
        rgba = original.convert("RGBA")
        image = Image.new("RGBA", rgba.size, "white")
        image.alpha_composite(rgba)
        image = image.convert("RGB")
    bounds = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    if bounds is None:
        raise ValueError(f"LibreOffice rendered an empty preview: {path.name}")
    left, top, right, bottom = bounds
    cropped = image.crop((max(0, left - 12), max(0, top - 12),
                          min(image.width, right + 12), min(image.height, bottom + 12)))
    cropped.save(path)
    return cropped.size


def crop_svg(path: Path) -> None:
    """Limit the SVG viewport to Draw's graphic bounds, retaining vector content."""
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text)
    if root.tag != f"{SVG}svg":
        raise ValueError(f"Invalid SVG: {path.name}")
    boxes = [r for r in root.iter(f"{SVG}rect") if r.get("class") == "BoundingBox"]
    if boxes:
        x = min(float(r.get("x", "0")) for r in boxes) - 100
        y = min(float(r.get("y", "0")) for r in boxes) - 100
        right = max(float(r.get("x", "0")) + float(r.get("width")) for r in boxes) + 100
        bottom = max(float(r.get("y", "0")) + float(r.get("height")) for r in boxes) + 100
        width, height = right - x, bottom - y
        opening = re.search(r"<svg\b[^>]*>", text).group()
        changed = re.sub(r'viewBox="[^"]*"', f'viewBox="{x:g} {y:g} {width:g} {height:g}"',
                         opening)
        changed = re.sub(r'\bwidth="[^"]*"', f'width="{width / 100:g}mm"', changed, count=1)
        changed = re.sub(r'\bheight="[^"]*"', f'height="{height / 100:g}mm"', changed, count=1)
        text = text.replace(opening, changed, 1)
    # The exported vector is self-contained; no external DTD is required.
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)
    path.write_text(text, encoding="utf-8")


def process(source: Path, markdown_path: Path, output: Path) -> dict:
    source, markdown_path, output = source.resolve(), markdown_path.resolve(), output.resolve()
    if output in (source, markdown_path):
        raise ValueError("Use a separate output path for the illustrated Markdown")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        raise RuntimeError("LibreOffice (soffice) is required to render OLE previews")
    records = read_previews(source)
    if not records:
        raise ValueError("No OLE objects with previews found in the DOCX main body")
    directory = f"{output.stem}-assets"
    markdown = image_markdown(markdown_path.read_text(encoding="utf-8"), records, directory)
    output.parent.mkdir(parents=True, exist_ok=True)
    assets = output.parent / directory
    assets.mkdir(exist_ok=True)
    # Convert in a fresh directory so stale output cannot hide a failed render.
    with tempfile.TemporaryDirectory(prefix="anydoc-ole-") as temporary:
        work = Path(temporary)
        inputs = []
        for record in records:
            suffix = Path(record["image_part"]).suffix.lower()
            if suffix not in {".emf", ".wmf", ".png", ".jpg", ".jpeg"}:
                raise ValueError(f"Unsupported preview format: {suffix}")
            path = work / f"ole-{record['index']:03d}{suffix}"
            path.write_bytes(record["payload"])
            inputs.append(path)
        rendered = work / "rendered"
        rendered.mkdir()
        for extension in ("svg", "png"):
            subprocess.run([
                soffice, "--headless", "--norestore",
                f"-env:UserInstallation={(work / 'profile').as_uri()}",
                "--convert-to", f"{extension}:draw_{extension}_Export",
                "--outdir", str(rendered), *map(str, inputs),
            ], check=True, capture_output=True, text=True, timeout=90)
        for record, original in zip(records, inputs):
            stem = original.stem
            svg, png = rendered / f"{stem}.svg", rendered / f"{stem}.png"
            if not svg.is_file() or not png.is_file():
                raise RuntimeError(f"LibreOffice did not render {original.name}")
            crop_svg(svg)
            record["png_dimensions"] = crop_png(png)
            for path in (original, svg, png):
                shutil.copyfile(path, assets / path.name)
            record["original_preview"] = f"{directory}/{original.name}"
            record["png"] = f"{directory}/{png.name}"
            record["svg"] = f"{directory}/{svg.name}"
            del record["payload"]
    output.write_text(markdown, encoding="utf-8")
    result = {
        "source": str(source),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "input_markdown": str(markdown_path),
        "output_markdown": str(output),
        "preview_count": len(records),
        "objects": records,
    }
    (assets / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("docx", type=Path)
    parser.add_argument("markdown", type=Path, help="Raw Anydoc Markdown for this DOCX")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = process(args.docx, args.markdown, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
