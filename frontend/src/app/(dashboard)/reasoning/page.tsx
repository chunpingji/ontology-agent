"use client";

import { useState } from "react";
import { runAssessment, calculatePDE, calculateMACO } from "@/lib/api";
import type { AssessmentResponse, PDEResponse, MACOResult } from "@/lib/api";

function PDECalculator() {
  const [pod, setPod] = useState("1.0");
  const [bw, setBw] = useState("50");
  const [f1, setF1] = useState("1");
  const [f2, setF2] = useState("10");
  const [f3, setF3] = useState("1");
  const [f4, setF4] = useState("1");
  const [f5, setF5] = useState("1");
  const [mf, setMf] = useState("1");
  const [result, setResult] = useState<PDEResponse | null>(null);

  const handleCalc = async () => {
    const r = await calculatePDE({
      pod: parseFloat(pod), bw: parseFloat(bw),
      f1: parseFloat(f1), f2: parseFloat(f2), f3: parseFloat(f3),
      f4: parseFloat(f4), f5: parseFloat(f5), mf: parseFloat(mf),
    });
    setResult(r);
  };

  const fields = [
    { label: "PoD (mg/kg/day)", value: pod, set: setPod },
    { label: "体重 BW (kg)", value: bw, set: setBw },
    { label: "F1 种属间", value: f1, set: setF1 },
    { label: "F2 个体间", value: f2, set: setF2 },
    { label: "F3 研究周期", value: f3, set: setF3 },
    { label: "F4 严重毒性", value: f4, set: setF4 },
    { label: "F5 NOAEL/LOAEL", value: f5, set: setF5 },
    { label: "MF 修饰因子", value: mf, set: setMf },
  ];

  return (
    <div className="rounded-lg border bg-white p-4">
      <h3 className="mb-3 font-semibold">PDE 计算器</h3>
      <p className="mb-3 text-xs text-gray-500">PDE = (PoD x BW) / (F1 x F2 x F3 x F4 x F5 x MF)</p>
      <div className="grid grid-cols-4 gap-2">
        {fields.map((f) => (
          <div key={f.label}>
            <label className="text-xs text-gray-500">{f.label}</label>
            <input
              type="number"
              step="any"
              value={f.value}
              onChange={(e) => f.set(e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm"
            />
          </div>
        ))}
      </div>
      <button onClick={handleCalc} className="mt-3 rounded bg-blue-600 px-4 py-1.5 text-sm text-white hover:bg-blue-700">
        计算 PDE
      </button>
      {result && (
        <div className="mt-3 rounded bg-blue-50 p-3">
          <p className="font-bold">PDE = {result.pde_value.toFixed(6)} mg/day</p>
        </div>
      )}
    </div>
  );
}

function MACOCalculator() {
  const [pde, setPde] = useState("");
  const [mbs, setMbs] = useState("1000");
  const [tddNext, setTddNext] = useState("1000");
  const [minDose, setMinDose] = useState("");
  const [ld50, setLd50] = useState("");
  const [route, setRoute] = useState("oral");
  const [result, setResult] = useState<MACOResult | null>(null);

  const handleCalc = async () => {
    const r = await calculateMACO({
      pde: pde ? parseFloat(pde) : undefined,
      mbs: parseFloat(mbs), tdd_next: parseFloat(tddNext),
      min_therapeutic_dose: minDose ? parseFloat(minDose) : undefined,
      ld50: ld50 ? parseFloat(ld50) : undefined, route,
    });
    setResult(r);
  };

  return (
    <div className="rounded-lg border bg-white p-4">
      <h3 className="mb-3 font-semibold">MACO 计算器</h3>
      <p className="mb-3 text-xs text-gray-500">取 4 种方法最小值 (CFDI 指南)</p>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-xs text-gray-500">PDE (mg/day)</label>
          <input type="number" step="any" value={pde} onChange={(e) => setPde(e.target.value)} placeholder="可选" className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">MBS 最小批量 (g)</label>
          <input type="number" value={mbs} onChange={(e) => setMbs(e.target.value)} className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">TDD next (mg)</label>
          <input type="number" value={tddNext} onChange={(e) => setTddNext(e.target.value)} className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">最小治疗剂量 (mg)</label>
          <input type="number" step="any" value={minDose} onChange={(e) => setMinDose(e.target.value)} placeholder="可选" className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">LD50 (mg/kg)</label>
          <input type="number" step="any" value={ld50} onChange={(e) => setLd50(e.target.value)} placeholder="可选" className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">给药途径</label>
          <select value={route} onChange={(e) => setRoute(e.target.value)} className="w-full rounded border px-2 py-1 text-sm">
            <option value="oral">口服</option>
            <option value="intravenous">注射</option>
            <option value="topical">外用</option>
          </select>
        </div>
      </div>
      <button onClick={handleCalc} className="mt-3 rounded bg-green-600 px-4 py-1.5 text-sm text-white hover:bg-green-700">
        计算 MACO
      </button>
      {result && (
        <div className="mt-3 rounded bg-green-50 p-3">
          <p className="font-bold">MACO = {result.maco_value.toFixed(6)} {result.unit || "mg"}</p>
          <p className="text-sm text-gray-600">采用方法: {result.method_used}</p>
          <div className="mt-2">
            <p className="text-xs text-gray-500">全部方法对比:</p>
            {Object.entries(result.all_methods).map(([method, val]) => (
              <p key={method} className={`text-xs ${method === result.method_used ? "font-bold text-green-700" : "text-gray-500"}`}>
                {method}: {(val as number).toFixed(6)} mg {method === result.method_used && "← 最小值"}
              </p>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AssessmentPanel() {
  const [drugIri, setDrugIri] = useState("https://ontology.pharma-gmp.cn/slpra/drug/DrugX");
  const [equipIris, setEquipIris] = useState("https://ontology.pharma-gmp.cn/slpra/equipment/CT64201");
  const [result, setResult] = useState<AssessmentResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const handleAssess = async () => {
    setLoading(true);
    try {
      const r = await runAssessment({
        drug_iri: drugIri,
        equipment_iris: equipIris.split("\n").map((s) => s.trim()).filter(Boolean),
      });
      setResult(r);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-lg border bg-white p-4">
      <h3 className="mb-3 font-semibold">风险评估</h3>
      <div className="space-y-2">
        <div>
          <label className="text-xs text-gray-500">药品 IRI</label>
          <input type="text" value={drugIri} onChange={(e) => setDrugIri(e.target.value)} className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <div>
          <label className="text-xs text-gray-500">设备 IRI (每行一个)</label>
          <textarea value={equipIris} onChange={(e) => setEquipIris(e.target.value)} rows={3} className="w-full rounded border px-2 py-1 text-sm" />
        </div>
        <button onClick={handleAssess} disabled={loading} className="rounded bg-red-600 px-4 py-1.5 text-sm text-white hover:bg-red-700 disabled:opacity-50">
          {loading ? "评估中..." : "运行评估"}
        </button>
      </div>

      {result && (
        <div className="mt-4 space-y-3">
          <div className={`rounded p-3 ${result.risk_level === "HighRisk" ? "bg-red-50" : result.risk_level === "MediumRisk" ? "bg-yellow-50" : "bg-green-50"}`}>
            <p className="font-bold">风险等级: {result.risk_level}</p>
            <p className="text-sm">设备专用化: {result.requires_dedication ? "必须专用" : "允许共线"}</p>
          </div>

          {result.rules_fired.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-500">触发的规则 ({result.rules_fired.length})</h4>
              {result.rules_fired.map((r, i) => (
                <div key={i} className="mt-1 rounded border p-2 text-xs">
                  <span className="font-mono font-bold text-blue-600">{r.rule_id}</span>
                  <span className="ml-2 text-gray-600">{r.description}</span>
                  {r.regulation_ref && <span className="ml-2 text-gray-400">[{r.regulation_ref}]</span>}
                </div>
              ))}
            </div>
          )}

          {result.scenarios.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-500">识别的 CFDI 情景</h4>
              {result.scenarios.map((s, i) => (
                <span key={i} className="mr-2 inline-block rounded bg-purple-100 px-2 py-0.5 text-xs text-purple-700">
                  {s.scenario_name}
                </span>
              ))}
            </div>
          )}

          {result.recommendations.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-gray-500">建议</h4>
              <ul className="list-inside list-disc text-sm">
                {result.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {result.maco && (
            <div className="rounded bg-gray-50 p-3">
              <p className="text-sm font-semibold">MACO = {result.maco.maco_value.toFixed(6)} mg ({result.maco.method_used})</p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function ReasoningPage() {
  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">推理控制台</h1>
      <div className="space-y-6">
        <AssessmentPanel />
        <div className="grid gap-4 lg:grid-cols-2">
          <PDECalculator />
          <MACOCalculator />
        </div>
      </div>
    </div>
  );
}
