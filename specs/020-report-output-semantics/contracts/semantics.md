# Semantic contracts

需求 §4～§12 规定模型和状态；编译接受实际固定 schema及注册契约，拒绝未知/执行型字段、IRI域值域错误、不支持的datatype facet、类型冲突、循环和越授权引用。

facts 根 source_root/entity_ref/binding/repeat_item + 完整路径；Input property/identity/entity/entities/record/records/field。派生视图采用有限 project/filter/sort/group/join/range/unit_convert，规则和条件使用三值有限表达式。模板不得提供业务常量，审核规则/参数契约可定义常量。

坏字段不可消费，记录保留合法兄弟；身份/基数/发现祖先约束另算。InputUse只提升required，UNKNOWN守卫保留可能分支强制要求。FALSE不激活条件也不产生否定，absence须发布证明。lineage/hash含关联/范围/发现/诊断版本。

输出仅 document/section/group/paragraph/text/value/table/row/cell/list/form_field/citation/signature_region 等通用节点，ID稳定。模型只生成授权内容节点；关键引用验证后插值，自由文字默认未批准。网页和DOCX同AST，各自转义，不反解析Markdown/HTML。封装保留原body AST引用并附签署树，所有hash无环。
