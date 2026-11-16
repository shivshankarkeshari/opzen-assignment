"""Load the five generated PDFs through the real authenticated upload API."""

import json

import httpx

from backend.config import settings
from scripts.financial_test_docs import OUTPUT


def load():
    manifest = json.loads((OUTPUT / "manifest.json").read_text())
    receipts = []
    with httpx.Client(
        base_url=settings().api_url,
        headers={"X-API-Key": settings().identities["analyst"]},
        timeout=45,
    ) as client:
        for spec in manifest:
            metadata = {
                "title": spec["title"],
                "document_type": spec["type"],
                "department_code": spec["department"],
                "approval_status": "approved",
                "effective_from": spec["effective_from"],
                "effective_to": spec["effective_to"],
                "recorded_author": "Synthetic test corpus",
                "share_with": ["reviewer"] if spec["share"] else [],
            }
            with (OUTPUT / spec["filename"]).open("rb") as source:
                response = client.post(
                    "/v1/documents",
                    headers={"Idempotency-Key": "financial-ui-v1-" + spec["filename"]},
                    files={"file": (spec["filename"], source, "application/pdf")},
                    data={"metadata": json.dumps(metadata)},
                )
            response.raise_for_status()
            receipts.append({**spec, **response.json()})
            print(spec["filename"], response.json()["status"])
    (OUTPUT / "receipts.json").write_text(json.dumps(receipts, indent=2))


if __name__ == "__main__":
    load()
