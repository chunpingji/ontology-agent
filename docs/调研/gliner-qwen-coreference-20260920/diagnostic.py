"""Supplementary GLiNER2.5 probes, declared after observing the primary run.

Tests a structured coreference schema and the official simple relation example.
Does not replace the primary comparison or tune its thresholds/prompts.
"""
import argparse
from pathlib import Path

import torch
from gliner2 import AutoExtractor

from probe import CASES, digest, timed, write


parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
torch.set_num_threads(4)
torch.manual_seed(42)
model = AutoExtractor.from_pretrained(
    "/app/models/gliner2.5-multi-v1-20260916", local_files_only=True,
    map_location="cpu", word_splitter="char", use_flashdeberta=False,
    quantize=False, compile=False,
)
model.eval()
model.strict_extraction = True


def exact_source(batch):
    processor = model.processor
    records = [processor.transform_and_format(text, schema) for text, schema in batch]
    assert all(record.text == text and len(record.input_ids) <= 512
               for record, (text, _) in zip(records, batch))
    result = processor._pad_batch(records)
    assert result.original_texts == [text for text, _ in batch]
    return processor._add_boundary_metadata(
        result, "boundary", is_training=False, build_targets=False,
        on_capacity_exceeded="raise", ignore_missing_entities=False,
    )


model._inference_collator = exact_source
schema = (model.create_schema().structure("指代关系")
          .field("指代表达", dtype="str", description="原文中的代词或指代表达")
          .field("先行词", dtype="str", description="该指代表达实际指向的原文实体名称或名词短语"))
write(args.output / "protocol.json", {
    "post_primary_diagnostic": True, "script_sha256": digest(__file__),
    "schema": schema.build(), "threshold": 0.5, "device": "cpu", "splitter": "char",
})
for case in CASES:
    timed(args.output, case["id"] + "-coreference-records", case["text"],
          lambda: model.extract(case["text"], schema, threshold=0.5,
                                include_spans=True, include_confidence=True,
                                overlap_policy="allow"))
text = "John works for Apple Inc. and lives in San Francisco. Apple Inc. is located in Cupertino."
for splitter in ("char", "whitespace"):
    model.set_word_splitter(splitter)
    timed(args.output, "official-english-relations-" + splitter, text,
          lambda: model.extract_relations(
              text, ["works_for", "lives_in", "located_in"], threshold=0.5,
              include_spans=True, include_confidence=True,
          ))
