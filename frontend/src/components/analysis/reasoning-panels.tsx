"use client";

import { useState } from "react";
import { calculateMACO, calculatePDE, runAssessment } from "@/lib/api";
import type { AssessmentResponse, MACOResult, PDEResponse } from "@/lib/api";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

// 推理面板（自 reasoning/page.tsx 抽出, T013）：PDE / MACO 计算器与风险评估。
// PendingSignaturesPanel 不在此 —— 签批已迁往审批中心。

export function PDECalculator() {
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
    <Card className="p-4">
      <h3 className="mb-3 font-semibold">PDE 计算器</h3>
      <p className="mb-3 text-xs text-muted-foreground">PDE = (PoD x BW) / (F1 x F2 x F3 x F4 x F5 x MF)</p>
      <div className="grid grid-cols-4 gap-2">
        {fields.map((f) => (
          <div key={f.label}>
            <label className="text-xs text-muted-foreground">{f.label}</label>
            <Input
              type="number"
              step="any"
              value={f.value}
              onChange={(e) => f.set(e.target.value)}
            />
          </div>
        ))}
      </div>
      <Button onClick={handleCalc} size="sm" className="mt-3">
        计算 PDE
      </Button>
      {result && (
        <div className="mt-3 rounded bg-primary/10 p-3">
          <p className="font-bold">PDE = {result.pde_value.toFixed(6)} mg/day</p>
        </div>
      )}
    </Card>
  );
}

export function MACOCalculator() {
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
    <Card className="p-4">
      <h3 className="mb-3 font-semibold">MACO 计算器</h3>
      <p className="mb-3 text-xs text-muted-foreground">取 4 种方法最小值 (CFDI 指南)</p>
      <div className="grid grid-cols-3 gap-2">
        <div>
          <label className="text-xs text-muted-foreground">PDE (mg/day)</label>
          <Input type="number" step="any" value={pde} onChange={(e) => setPde(e.target.value)} placeholder="可选" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">MBS 最小批量 (g)</label>
          <Input type="number" value={mbs} onChange={(e) => setMbs(e.target.value)} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">TDD next (mg)</label>
          <Input type="number" value={tddNext} onChange={(e) => setTddNext(e.target.value)} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">最小治疗剂量 (mg)</label>
          <Input type="number" step="any" value={minDose} onChange={(e) => setMinDose(e.target.value)} placeholder="可选" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">LD50 (mg/kg)</label>
          <Input type="number" step="any" value={ld50} onChange={(e) => setLd50(e.target.value)} placeholder="可选" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">给药途径</label>
          <Select value={route} onValueChange={setRoute}>
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="oral">口服</SelectItem>
              <SelectItem value="intravenous">注射</SelectItem>
              <SelectItem value="topical">外用</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>
      <Button onClick={handleCalc} size="sm" className="mt-3 bg-success text-success-foreground hover:bg-success/90">
        计算 MACO
      </Button>
      {result && (
        <div className="mt-3 rounded bg-success/10 p-3">
          <p className="font-bold">MACO = {result.maco_value.toFixed(6)} {result.unit || "mg"}</p>
          <p className="text-sm text-muted-foreground">采用方法: {result.method_used}</p>
          <div className="mt-2">
            <p className="text-xs text-muted-foreground">全部方法对比:</p>
            {Object.entries(result.all_methods).map(([method, val]) => (
              <p key={method} className={`text-xs ${method === result.method_used ? "font-bold text-success" : "text-muted-foreground"}`}>
                {method}: {(val as number).toFixed(6)} mg {method === result.method_used && "← 最小值"}
              </p>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

export function AssessmentPanel() {
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
    <Card className="p-4">
      <h3 className="mb-3 font-semibold">风险评估</h3>
      <div className="space-y-2">
        <div>
          <label className="text-xs text-muted-foreground">药品 IRI</label>
          <Input type="text" value={drugIri} onChange={(e) => setDrugIri(e.target.value)} />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">设备 IRI (每行一个)</label>
          <Textarea value={equipIris} onChange={(e) => setEquipIris(e.target.value)} rows={3} />
        </div>
        <Button onClick={handleAssess} disabled={loading} variant="destructive" size="sm">
          {loading ? "评估中..." : "运行评估"}
        </Button>
      </div>

      {result && (
        <div className="mt-4 space-y-3">
          <div className={`rounded p-3 ${result.risk_level === "HighRisk" ? "bg-destructive/10" : result.risk_level === "MediumRisk" ? "bg-warning/10" : "bg-success/10"}`}>
            <p className="font-bold">风险等级: {result.risk_level}</p>
            <p className="text-sm">设备专用化: {result.requires_dedication ? "必须专用" : "允许共线"}</p>
          </div>

          {result.rules_fired.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-muted-foreground">触发的规则 ({result.rules_fired.length})</h4>
              {result.rules_fired.map((r, i) => (
                <div key={i} className="mt-1 rounded border p-2 text-xs">
                  <span className="font-mono font-bold text-primary">{r.rule_id}</span>
                  <span className="ml-2 text-muted-foreground">{r.description}</span>
                  {r.regulation_ref && <span className="ml-2 text-muted-foreground">[{r.regulation_ref}]</span>}
                </div>
              ))}
            </div>
          )}

          {result.scenarios.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-muted-foreground">识别的 CFDI 情景</h4>
              {result.scenarios.map((s, i) => (
                <Badge key={i} variant="secondary" className="mr-2">
                  {s.scenario_name}
                </Badge>
              ))}
            </div>
          )}

          {result.recommendations.length > 0 && (
            <div>
              <h4 className="text-sm font-semibold text-muted-foreground">建议</h4>
              <ul className="list-inside list-disc text-sm">
                {result.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {result.maco && (
            <div className="rounded bg-muted p-3">
              <p className="text-sm font-semibold">MACO = {result.maco.maco_value.toFixed(6)} mg ({result.maco.method_used})</p>
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
