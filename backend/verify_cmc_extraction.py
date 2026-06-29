"""端到端实证：CMCReport 全量关系/属性抽取 on 《原料药 HRS-1234 临床备样生产信息.docx》。

加载**真** OntologyEngine（真实反查对象属性，绕过 owlready2 get_class_properties bug）→
真三阶段 annotate_word → 规则式 extract_relationships → 分组打印「分类 / 产品 / 合成路线
（步骤+设备+中间体）/ 设备 / 安全风险 / 质量风险 / 清洗 / 残留 / 存放 / 共线 / 降解」
全图与溯源，并断言每类非空。证明规则链在真实本体 + 真实文档上端到端可用。

运行：``cd backend && HF_HUB_OFFLINE=1 uv run python verify_cmc_extraction.py``
（离线；首跑加载 GLiNER + bge 本地权重，约 20-30s。）
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from app.services.extraction.document_annotator import annotate_word
from app.services.extraction.relation_extractor import extract_relationships
from app.services.ontology_engine import OntologyEngine

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DOC = _REPO_ROOT / "data" / "uploads" / "原料药 HRS-1234 临床备样生产信息.docx"


def _short(iri: str) -> str:
    return iri.rsplit("/", 1)[-1] if iri else "?"


def _print_dps(dps: list[dict], indent: str) -> None:
    for d in dps:
        tag = "" if d.get("iri") else " (raw)"
        print(f"{indent}· {d['label']}: {d['value']}{tag}")


def main() -> int:
    if not _DOC.is_file():
        print(f"✗ 源文档不存在：{_DOC}")
        return 2

    engine = OntologyEngine()
    engine.load()

    print("运行 annotate_word（真模型，离线）…")
    _doc_json, _warnings, triples, _ckpt = annotate_word(str(_DOC), engine)
    print(f"三阶段产出：{len(triples)} 实体三元组\n")

    graph = extract_relationships(engine, str(_DOC), triples)
    cls = graph["doc_class"]
    rels = graph["relationships"]

    print("=" * 72)
    if cls:
        print(f"文档分类：{cls['label']}  ({_short(cls['doc_class_iri'])})  "
              f"score={cls['score']}  signals={cls['signals']}")
    else:
        print("文档分类：未识别")
    print("=" * 72)

    by_pred: dict[str, list[dict]] = defaultdict(list)
    for r in rels:
        by_pred[r["predicate_label"]].append(r)

    for pred, edges in by_pred.items():
        print(f"\n【{pred}】 {len(edges)} 条")
        for e in edges:
            print(f"  ─{pred}→ {e['object_class_label']}: {e['object_text']!r}"
                  f"  [src={e['object_source']}, ref={e.get('source_ref')}]")
            _print_dps(e["object_data_properties"], "      ")
            for sub in e["sub_relationships"]:
                print(f"      ↳ {sub['predicate_label']}→ "
                      f"{sub['object_class_label']}: {sub['object_text']!r}")
                _print_dps(sub["object_data_properties"], "          ")

    # 断言每个核心类别非空。
    preds = {r["predicate_iri"].rsplit("/", 1)[-1] for r in rels}
    expected = {
        "describes", "hasSynthesisRoute", "usesEquipment",
        "hasSafetyRiskAssessment", "hasQualityRiskAssessment",
        "hasCleaningMethod", "hasCleaningResidue", "hasStorageCondition",
        "hasSharedLineData", "hasDegradationPathway",
    }
    missing = expected - preds
    print("\n" + "=" * 72)
    print(f"覆盖谓词 {len(preds & expected)}/{len(expected)}：{sorted(preds & expected)}")
    if missing:
        print(f"✗ 缺失：{sorted(missing)}")
        return 1
    print("✓ 全部核心关系类别均已产出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
