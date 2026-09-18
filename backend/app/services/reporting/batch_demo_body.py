"""Map the shared graph into the reference sample's batch record forms."""

from app.services.reporting.batch_demo_operation_forms import operation_table
from app.services.reporting.batch_demo_sample_content import operation_records, reference_table
from app.services.reporting.output_ast import OutputNode

BLANK = ""


def properties(entity):
    return {p["label"]: p["value"] for p in entity["object_data_properties"]}


def build_body(graph, template, layout):
    # Import here to keep the public demo service's selector as the single path contract.
    from app.services.reporting.batch_demo import select_path

    counter = 0
    selected = {s["id"]: select_path(graph, s["path"]) for s in template["sections"]}

    def node(kind, meta=None, **kwargs):
        nonlocal counter
        counter += 1
        return OutputNode(node_id=f"batch-demo-{counter}", kind=kind,
                          provenance_refs=[meta] if meta else [], **kwargs)

    def paragraph(text, role="paragraph", prototype=None):
        prototype = prototype if prototype is not None else (6 if role == "title" else 16)
        p = layout.paragraphs[prototype]
        return node("paragraph", {"kind": "template_layout", "role": role,
                                  **layout.paragraph_style(p),
                                  "prototype_paragraph": prototype,
                                  "space_before_pt": 0 if role in ("title", "spacer") else 6,
                                  "space_after_pt": 0 if role in ("title", "spacer") else 6},
                    text=text)

    def table(role, rows, fixed=False, refs=None):
        prototype = layout.table(role)
        nodes = []
        if not fixed:
            headers = ["".join(p.text for p in c.paragraphs).strip()
                       for c in prototype.rows[0].cells]
            if role == "control":
                headers = ["项目", "原文要求", "实际记录", "操作/复核"]
            rows = [headers, *rows]
        for i, values in enumerate(rows):
            source_row = i if fixed else min(i, 1)
            expected = len(prototype.rows[source_row]._tr.tc_lst)
            if len(values) != expected:
                raise ValueError(f"{role}: expected {expected} cells, got {len(values)}")
            height = prototype.rows[source_row].height
            row = node("row", {"kind": "template_layout", "prototype_row": source_row,
                               "height_pt": height.pt if height else None},
                       header=i == 0 and not fixed, children=[
                           node("cell", layout.cell_metadata(role, source_row, j),
                                text=str(value)) for j, value in enumerate(values)
                       ])
            if refs and i > 0:
                row.provenance_refs.append({"kind": "source", **refs[i - 1]})
            nodes.append(row)
        return node("table", layout.table_metadata(role), children=nodes)

    def section(title, children, index=2, cover=False, header=True):
        if not cover:
            children = [paragraph(title, "heading"), *children]
            if header:
                children.insert(0, reference_table(layout, f"header:{index}", node, "header"))
        return node("section", {**layout.page_metadata(index, "cover" if cover else "page"),
                                "header_section": index if header and not cover else None},
                    text=title, children=children)

    def equipment(nodes):
        return table("equipment", [[properties(e).get("设备名称", e["object_text"]),
                                    properties(e).get("设备编号（原文备选组合）", BLANK),
                                    BLANK, BLANK, BLANK, BLANK] for e in nodes])

    def detail_rows(entities):
        return [[e["object_text"] + " · " + p["label"], p["value"], BLANK, BLANK]
                for e in entities for p in e["object_data_properties"]
                if p["value"] not in (None, "", [])]

    product = properties(selected["product"][0]) if selected["product"] else {}
    cover = section("批生产记录", [
        *[paragraph("", "spacer", i) for i in range(6)],
        paragraph("批生产记录", "title"),
        *[paragraph("", "spacer", i) for i in range(7, 9)],
        table("cover", [
            ["产  品  名  称", product.get("项目名称", "HRS-5592"), ""],
            ["规          格", "原料药", ""], ["批          号", BLANK, ""],
            ["本  批  得  量", BLANK, ""], ["车间负责人审核/日期", BLANK, ""],
            ["QA审核/日期", BLANK, ""],
        ], fixed=True),
        paragraph("HRS-5592 · 演示草稿"),
        paragraph("生产日期：　　　　　　　　　实际得量/收率："),
        paragraph("演示草稿：尚未执行生产、检查或审核。"),
    ], index=0, cover=True)
    preparation = section("物料信息表与生产准备", [
        paragraph("1、物料信息表", "heading"),
        table("materials", [[properties(e).get(k, BLANK) for k in (
            "物料编码", "物料名称", "规格", "批耗量", "生产商",
        )] for e in selected["materials"]]),
        paragraph("批耗量为原文计划值，实际领用与投料记录打印后填写。"),
        paragraph("2、设备检查", "heading"), equipment(selected["equipment"]),
        paragraph("3、计量器具检查", "heading"),
        table("instruments", [[BLANK] * 7]),
        paragraph("4、生产计划", "heading"),
        table("control", detail_rows(selected["product"] + selected["plan"])),
    ], index=1)
    sections = [cover, preparation]
    for stage_index, step in enumerate(selected["steps"], start=2):
        props = properties(step)
        children = [paragraph("1、工艺描述", "heading"),
                    paragraph(props.get("工艺条件摘要", "")),
                    paragraph("2、设备检查", "heading")]
        linked = [e for e in step["sub_relationships"]
                  if e["predicate_iri"].endswith("/usesEquipment")]
        children += [equipment(linked)] if linked else [paragraph(
            "本工序设备按操作原文核对；实际使用设备及检查记录打印后填写。",
        )]
        children += [paragraph("3、生产现场检查", "heading"), table("site", [
            [label, "□是    □否", BLANK, BLANK] for label in (
                "无其他无关的文件", "无其他无关的物料", "现场卫生已清洁", "其他生产器具状态正常",
            )]), *([] if stage_index == 2 else [paragraph("4、生产操作记录", "heading")]),
            *(operation_records(layout, node) if stage_index == 2 else operation_table_nodes(
                step, layout, node, paragraph,
            )),
            paragraph("5、过程与产物控制", "heading"),
            table("control", [[key, props[key], BLANK, BLANK] for key in (
                "过程控制", "参考得量范围", "参考收率范围",
            ) if props.get(key)]),
        ]
        outputs = [e for e in step["sub_relationships"] if e["predicate_iri"].endswith(
            ("/producesIntermediate", "/producesFinalProduct"),
        )]
        storage = [e for output in outputs for e in output["sub_relationships"]
                   if e["predicate_iri"].endswith("/hasStorageCondition")]
        children += [table("control", detail_rows(outputs + storage)),
                     paragraph("6、清场", "heading"), table("clearance", [
                         [label, "□是    □否", BLANK, BLANK] for label in (
                             "清洁其他生产器具", "关闭本岗位公用工程系统", "生产批记录清离现场",
                             "整理废弃杂物", "清洁生产区域",
                         )]), paragraph("7、偏差处理", "heading"),
                     table("deviation", [["有无偏差", "□无    □有；偏差号：",
                                           "偏差日期：", "操作人/日期", "复核人/日期"],
                                          ["偏差情况简单描述", BLANK, BLANK, BLANK, BLANK],
                                          ["偏差处理：", BLANK, BLANK]], fixed=True)]
        title = layout.table(f"header:{stage_index}").cell(0, 1).text
        sections.append(section(title, children, index=stage_index))
    sections.append(section("设备清洗、安全控制及来源", [
        paragraph("1、设备清洗记录", "heading"),
        table("control", [[properties(e).get("设备名称及编号", e["object_text"]),
                           properties(e).get("清洁方法", BLANK), BLANK, BLANK]
                             for e in selected["cleaning"]]),
        paragraph("2、安全控制", "heading"), table("control", detail_rows(selected["safety"])),
        paragraph("3、源文档补充信息", "heading"),
        table("control", detail_rows(selected["equipment"] + selected["materials"])),
        paragraph("来源：" + graph["source_filename"]),
        paragraph("页眉及 SM5592-A14 操作记录来自参考模板；其他内容来自共享静态关系图谱。"),
        *[paragraph(w) for w in graph["warnings"]],
        *[paragraph(field + "：") for field in template["manual_fields"]],
    ], index=1, header=False))
    return node("document", {"kind": "template_layout", **layout.manifest}, children=sections)


def operation_table_nodes(step, layout, node, paragraph):
    return [paragraph("参数按模板逐行列出；记录栏保留单位和填写格式，实际数值及签署打印后填写。"),
            operation_table(step, layout, node)]
