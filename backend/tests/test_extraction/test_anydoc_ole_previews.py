"""OLE previews follow explicit relationships, including repeated object labels."""

import zipfile

import pytest
from PIL import Image

from scripts.anydoc_ole_previews import crop_png, image_markdown, read_previews


def make_docx(tmp_path, *, missing_preview=False, external=False):
    second_image = "" if missing_preview else '<v:imagedata r:id="img-first"/>'
    mode = ' TargetMode="External"' if external else ""
    document = f'''<w:document
      xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
      xmlns:v="urn:schemas-microsoft-com:vml"
      xmlns:o="urn:schemas-microsoft-com:office:office"><w:body>
      <w:p><w:r><w:object>
        <v:shape id="decoy"><v:imagedata r:id="img-first"/></v:shape>
        <v:shape id="shape-a"><v:imagedata r:id="img-second"/></v:shape>
        <o:OLEObject ShapeID="shape-a" ProgID="ChemDraw.Document.6.0" r:id="ole-a"/>
      </w:object></w:r></w:p>
      <w:p><w:r><w:object>
        <v:shape id="shape-b">{second_image}</v:shape>
        <o:OLEObject ShapeID="shape-b" ProgID="ChemDraw.Document.6.0" r:id="ole-b"/>
      </w:object></w:r></w:p>
    </w:body></w:document>'''
    rels = f'''<Relationships>
      <Relationship Id="ole-b" Target="embeddings/b.bin"/>
      <Relationship Id="img-first" Target="media/image1.emf"/>
      <Relationship Id="ole-a" Target="embeddings/a.bin"/>
      <Relationship Id="img-second" Target="media/image2.emf"{mode}/>
    </Relationships>'''
    path = tmp_path / "source.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", document)
        archive.writestr("word/_rels/document.xml.rels", rels)
        archive.writestr("word/media/image1.emf", b"preview-b")
        archive.writestr("word/media/image2.emf", b"preview-a")
    return path


def test_shape_relationships_override_media_names_and_decoy_shapes(tmp_path):
    records = read_previews(make_docx(tmp_path))
    assert [r["payload"] for r in records] == [b"preview-a", b"preview-b"]
    assert [r["ole_part"] for r in records] == ["word/embeddings/a.bin", "word/embeddings/b.bin"]
    label = "Embedded object: ChemDraw.Document.6.0"
    converted = image_markdown(f"第一处：{label}\n第二处：{label}", records, "previews")
    assert converted == (
        "第一处：![OLE 预览 1](previews/ole-001.png) ([SVG](previews/ole-001.svg))\n"
        "第二处：![OLE 预览 2](previews/ole-002.png) ([SVG](previews/ole-002.svg))"
    )


def test_missing_preview_does_not_shift_later_images(tmp_path):
    with pytest.raises(ValueError, match="no preview"):
        read_previews(make_docx(tmp_path, missing_preview=True))


def test_external_preview_is_not_fetched(tmp_path):
    with pytest.raises(ValueError, match="internal relationship"):
        read_previews(make_docx(tmp_path, external=True))


def test_repeated_placeholder_in_body_is_ambiguous(tmp_path):
    records = read_previews(make_docx(tmp_path))
    with pytest.raises(ValueError, match="ambiguous mapping"):
        image_markdown("Embedded object: ChemDraw.Document.6.0\n" * 3, records, "previews")


def test_crop_keeps_all_content_including_edge_marks(tmp_path):
    path = tmp_path / "preview.png"
    im = Image.new("RGB", (100, 100), "white")
    im.putpixel((20, 30), (255, 0, 0))
    im.putpixel((80, 60), (0, 0, 255))
    im.save(path)
    assert crop_png(path) == (85, 55)
    with Image.open(path) as cropped:
        assert cropped.getpixel((12, 12)) == (255, 0, 0)
        assert cropped.getpixel((72, 42)) == (0, 0, 255)


def test_empty_render_is_not_success(tmp_path):
    path = tmp_path / "empty.png"
    Image.new("RGB", (100, 100), "white").save(path)
    with pytest.raises(ValueError, match="empty preview"):
        crop_png(path)
