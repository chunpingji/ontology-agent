"use client";

export default function ExtractionPage() {
  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">文档抽取</h1>
      <div className="rounded-lg border bg-white p-6">
        <h2 className="mb-3 font-semibold">上传文档</h2>
        <div className="rounded-lg border-2 border-dashed border-gray-300 p-8 text-center">
          <p className="text-gray-500">拖放 Excel (.xlsx) 或 Word (.docx) 文件到此处</p>
          <p className="mt-1 text-sm text-gray-400">或点击下方按钮选择文件</p>
          <button className="mt-4 rounded bg-blue-600 px-4 py-2 text-sm text-white hover:bg-blue-700">
            选择文件
          </button>
        </div>
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-semibold text-gray-500">抽取流程</h3>
          <div className="flex items-center gap-2 text-sm">
            <span className="rounded bg-gray-100 px-3 py-1">1. 上传文档</span>
            <span className="text-gray-300">→</span>
            <span className="rounded bg-gray-100 px-3 py-1">2. 选择抽取配置</span>
            <span className="text-gray-300">→</span>
            <span className="rounded bg-gray-100 px-3 py-1">3. LLM 实体抽取</span>
            <span className="text-gray-300">→</span>
            <span className="rounded bg-gray-100 px-3 py-1">4. 实体对齐</span>
            <span className="text-gray-300">→</span>
            <span className="rounded bg-gray-100 px-3 py-1">5. 人工审核</span>
            <span className="text-gray-300">→</span>
            <span className="rounded bg-blue-100 px-3 py-1 text-blue-700">6. 提交到知识图谱</span>
          </div>
        </div>
      </div>

      <div className="mt-6 rounded-lg border bg-white p-6">
        <h2 className="mb-3 font-semibold">抽取配置管理</h2>
        <p className="text-sm text-gray-500">
          分析师可以在此定义抽取配置：目标本体类、列名映射、LLM 提示词模板、few-shot 示例。
        </p>
        <div className="mt-4">
          <h3 className="mb-2 text-sm font-semibold">示例配置: 设备表抽取</h3>
          <pre className="overflow-auto rounded bg-gray-50 p-3 text-xs">
{`{
  "name": "设备表标准格式",
  "target_class_iri": "https://ontology.pharma-gmp.cn/slpra/equipment/Equipment",
  "source_type": "excel",
  "column_mapping": {
    "设备编号": "slpra-equip:equipmentID",
    "设备名称": "slpra-equip:equipmentName",
    "规格型号": "slpra-equip:modelSpecification",
    "材质": "slpra-equip:constructedOf",
    "区域": "slpra-equip:locatedIn",
    "是否洁净区": "slpra-equip:isInCleanArea"
  }
}`}
          </pre>
        </div>
      </div>
    </div>
  );
}
