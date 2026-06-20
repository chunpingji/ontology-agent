"use client";

import { useEffect, useState } from "react";
import { getIntegrationSpecs } from "@/lib/api";
import type { IntegrationSpec } from "@/lib/api";

export default function IntegrationPage() {
  const [specs, setSpecs] = useState<IntegrationSpec[]>([]);

  useEffect(() => {
    getIntegrationSpecs().then(setSpecs).catch(console.error);
  }, []);

  const TYPE_LABELS: Record<string, string> = {
    mes: "制造执行系统 (MES)",
    erp: "企业资源规划 (ERP)",
    lims: "实验室信息管理 (LIMS)",
    ctms: "临床试验管理 (CTMS)",
  };

  return (
    <div>
      <h1 className="mb-4 text-xl font-bold">系统集成</h1>
      <p className="mb-6 text-sm text-gray-500">
        以下为标准化集成接口规范。具体系统对接将按需实现。
      </p>

      <div className="space-y-4">
        {specs.map((spec) => (
          <div key={spec.system_type} className="rounded-lg border bg-white p-5">
            <h2 className="mb-1 font-semibold">
              {TYPE_LABELS[spec.system_type] || spec.system_type}
            </h2>
            <p className="mb-3 text-sm text-gray-500">{spec.description}</p>
            <h3 className="mb-1 text-sm font-semibold text-gray-400">接口端点</h3>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs text-gray-400">
                  <th className="pb-1 pr-3">方法</th>
                  <th className="pb-1 pr-3">路径</th>
                  <th className="pb-1">参数</th>
                </tr>
              </thead>
              <tbody>
                {spec.endpoints.map((ep, i) => (
                  <tr key={i} className="border-b last:border-0">
                    <td className="py-1.5 pr-3">
                      <span className="rounded bg-blue-100 px-1.5 py-0.5 font-mono text-xs text-blue-700">
                        {ep.method}
                      </span>
                    </td>
                    <td className="py-1.5 pr-3 font-mono text-xs">{ep.path}</td>
                    <td className="py-1.5 text-xs text-gray-500">{ep.params}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  );
}
