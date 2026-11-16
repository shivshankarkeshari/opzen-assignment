"""Docling runs in a subprocess so conversion memory dies with each batch."""

import json
import subprocess
import sys
from pathlib import Path

from backend.config import settings


def batches(path: Path, config: dict):
    if path.suffix.lower() != ".pdf":
        yield path, 0, 1
        return
    import pypdfium2 as pdfium

    with pdfium.PdfDocument(path) as original:
        count = len(original)
        if count > config["max_pages"]:
            raise ValueError(f"PDF exceeds the {config['max_pages']}-page limit")
        for start in range(0, count, config["pdf_batch_pages"]):
            end = min(start + config["pdf_batch_pages"], count)
            batch = path.parent / f"batch-{start}.pdf"
            with pdfium.PdfDocument.new() as document:
                document.import_pages(original, pages=list(range(start, end)))
                document.save(batch)
            yield batch, start, end - start


def convert_batch(path: Path, output: Path, config: dict):
    output.mkdir(parents=True, exist_ok=True)
    (output / "config.json").write_text(json.dumps(config))
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.converter", str(path), str(output)],
            capture_output=True,
            text=True,
            timeout=config["conversion_timeout"],
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Document conversion timed out; reduce the PDF batch size") from None
    if result.returncode:
        # Keep diagnostic logs local; the UI receives a safe category, not paths or raw source.
        import logging

        logging.getLogger(__name__).error("Docling task failed: %s", result.stderr[-3000:])
        raise ValueError("Docling conversion failed; check model artifacts and the input document")
    return json.loads((output / "manifest.json").read_text())


def task(path: Path, output: Path):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption
    from docling_core.types.doc import ImageRefMode

    s = settings().model_copy(update=json.loads((output / "config.json").read_text()))
    options = PdfPipelineOptions(
        do_ocr=s.ocr_enabled,
        ocr_options=RapidOcrOptions(backend="onnxruntime"),
        do_table_structure=True,
        generate_picture_images=True,
        generate_page_images=False,
        enable_remote_services=False,
        document_timeout=s.conversion_timeout - 5,
        artifacts_path=Path(s.docling_artifacts_path) if s.docling_artifacts_path else None,
    )
    converter = DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.DOCX, InputFormat.IMAGE],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        },
    )
    result = converter.convert(path, max_num_pages=s.max_pages)
    doc = result.document
    doc.save_as_json(output / "canonical.json", image_mode=ImageRefMode.PLACEHOLDER)
    (output / "preview.md").write_text(doc.export_to_markdown())
    warnings, tables, pictures = [], [], []
    for i, table in enumerate(doc.tables):
        try:
            data = {
                "source_ref": table.self_ref,
                "caption": table.caption_text(doc),
                "markdown": table.export_to_markdown(doc=doc),
                "rows": json.loads(table.export_to_dataframe(doc=doc).to_json(orient="records")),
            }
            filename = f"table-{i}.json"
            (output / filename).write_text(json.dumps(data))
            tables.append({"file": filename, "source_ref": table.self_ref})
        except Exception:
            warnings.append(f"Table {i + 1} could not be exported; canonical source is retained")
    for i, picture in enumerate(doc.pictures):
        image = picture.get_image(doc)
        if image:
            filename = f"picture-{i}.png"
            image.save(output / filename)
            pictures.append(
                {
                    "file": filename,
                    "source_ref": picture.self_ref,
                    "caption": picture.caption_text(doc),
                }
            )
    status = result.status.value
    errors = [
        str(getattr(error, "error_message", "Conversion warning"))[:200] for error in result.errors
    ]
    (output / "manifest.json").write_text(
        json.dumps(
            {
                "status": status,
                "warnings": warnings + errors,
                "tables": tables,
                "pictures": pictures,
                "page_count": len(doc.pages),
            }
        )
    )


if __name__ == "__main__":
    task(Path(sys.argv[1]), Path(sys.argv[2]))
