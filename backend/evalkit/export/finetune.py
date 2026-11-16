"""Curated Fine-Tuning Data Exporter."""

import json
import os
from typing import Any, Dict, List
from datetime import datetime, timezone
from backend.app.db.session import get_db_context
from backend.app.db.models import Trace, HumanLabel, FinetuneExport
from backend.evalkit.tracing.redact import redact_payload


def export_finetune_data(project_id: str, output_file: str) -> Dict[str, Any]:
    """Exports traces labeled 'good' or with ideal answers to JSONL."""
    with get_db_context() as db:
        labels = db.query(HumanLabel).join(Trace).filter(
            HumanLabel.project_id == project_id,
            (HumanLabel.label == "good") | (HumanLabel.ideal_output.isnot(None))
        ).all()

        examples = []
        provenance = []

        for hl in labels:
            t = hl.trace
            input_text = ""
            if isinstance(t.input, dict):
                input_text = t.input.get("query") or json.dumps(t.input)
            else:
                input_text = str(t.input or "")

            output_text = hl.ideal_output
            if not output_text:
                if isinstance(t.output, dict):
                    output_text = t.output.get("text") or json.dumps(t.output)
                else:
                    output_text = str(t.output or "")

            record = {
                "messages": [
                    {"role": "user", "content": redact_payload(input_text)},
                    {"role": "assistant", "content": redact_payload(output_text)}
                ],
                "metadata": {
                    "trace_id": t.id,
                    "feature": t.feature,
                    "scenario": t.scenario
                }
            }
            examples.append(record)
            provenance.append(t.id)

        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        with open(output_file, "w", encoding="utf-8") as f:
            for ex in examples:
                f.write(json.dumps(ex) + "\n")

        ft_rec = FinetuneExport(
            project_id=project_id,
            filter={"criteria": "good_or_ideal"},
            example_count=len(examples),
            storage_key=output_file,
            provenance=provenance
        )
        db.add(ft_rec)

        return {
            "exported_count": len(examples),
            "file_path": output_file,
            "export_id": ft_rec.id
        }
