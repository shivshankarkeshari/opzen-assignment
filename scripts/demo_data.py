"""Generate small, clearly synthetic documents for local ingestion demos."""

from pathlib import Path

from docx import Document


def native_pdf(pages: list[str]) -> bytes:
    """A tiny text-only PDF fixture using the standard built-in Helvetica font."""
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs = []
    for text in pages:
        page_number = len(objects) + 1
        page_refs.append(f"{page_number} 0 R")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {page_number + 1} 0 R >>".encode()
        )
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content = f"BT /F1 16 Tf 50 760 Td ({escaped}) Tj ET".encode()
        objects.append(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
    objects[1] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(pages)} >>".encode()
    output, offsets = b"%PDF-1.4\n", [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(output)
    output += f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode()
    output += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return (
        output
        + f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )


def create(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    for years, name in [(5, "retention-policy-v1.docx"), (7, "retention-policy-v2.docx")]:
        doc = Document()
        doc.add_heading("Customer Records Retention Policy", 0)
        doc.add_paragraph("Synthetic demonstration data. Not an actual company policy.")
        doc.add_heading("Scope", 1)
        doc.add_paragraph("This policy applies to customer onboarding records in Operations.")
        doc.add_heading("Retention", 1)
        doc.add_paragraph(f"Customer onboarding records must be retained for {years} years.")
        doc.add_paragraph("No exceptions.")
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Record type"
        table.rows[0].cells[1].text = "Retention period"
        row = table.add_row().cells
        row[0].text, row[1].text = "Onboarding", f"{years} years"
        doc.save(directory / name)
    (directory / "access-procedure.pdf").write_bytes(
        native_pdf(
            [
                "SYNTHETIC DEMO: Staff access requires manager approval.",
                "SYNTHETIC DEMO: Review customer record access every year.",
            ]
        )
    )
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1200, 1500), "white")
    draw = ImageDraw.Draw(image)
    draw.multiline_text(
        (80, 100),
        "SYNTHETIC DEMO POLICY\n\nCustomer records are retained\nfor seven years.\n\nNo exceptions.",
        font=ImageFont.load_default(size=38),
        fill="black",
        spacing=12,
    )
    image.save(directory / "scanned-policy.pdf", "PDF", resolution=120)
    print(f"Synthetic demonstration documents: {directory}")


if __name__ == "__main__":
    create(Path("storage/demo"))
