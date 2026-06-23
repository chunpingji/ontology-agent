"use client";

import { useCallback, useEffect, useState } from "react";
import {
  createLinkType,
  deleteLinkType,
  listLinkTypes,
  updateLinkType,
  type TBoxLinkType,
} from "@/lib/api";
import { Field } from "@/components/ontology/field";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

type Mode = "list" | "create" | "edit";
type FormState = {
  slpra_iri: string;
  label: string;
  domain_iri: string;
  range_iri: string;
  inverse_iri: string;
  min_cardinality: string;
  max_cardinality: string;
};

const emptyForm = (domainIri: string | null): FormState => ({
  slpra_iri: MANAGED_PREFIX,
  label: "",
  domain_iri: domainIri ?? "",
  range_iri: "",
  inverse_iri: "",
  min_cardinality: "",
  max_cardinality: "",
});

const tail = (iri: string | null) => (iri ? iri.split("/").pop() : null);

/**
 * 对象属性 / 关系面板（T033）。以「列表 + 列表元素增删改」组织：
 * 默认展示当前类（domain）挂接的关系列表，每行可编辑 / 删除；顶部按钮新增关系。
 */
export function LinkTypePanel({
  selectedClassIri,
  onChanged,
}: {
  selectedClassIri: string | null;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<TBoxLinkType[]>([]);
  const [mode, setMode] = useState<Mode>("list");
  const [editing, setEditing] = useState<TBoxLinkType | null>(null);
  const [form, setForm] = useState<FormState>(emptyForm(selectedClassIri));
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    listLinkTypes(selectedClassIri ?? undefined, true)
      .then((rows) =>
        // 本类直接声明的排在前，继承来的排在后
        setItems([...rows].sort((a, b) => Number(!!a.inherited_from_iri) - Number(!!b.inherited_from_iri))),
      )
      .catch((e) => setError(String(e)));
  }, [selectedClassIri]);

  useEffect(() => {
    load();
    setMode("list");
  }, [load]);

  const backToList = () => {
    setMode("list");
    setEditing(null);
    setError(null);
  };

  const afterWrite = (message: string) => {
    setMsg(message);
    load();
    onChanged();
    backToList();
  };

  const startCreate = () => {
    setEditing(null);
    setForm(emptyForm(selectedClassIri));
    setError(null);
    setMsg(null);
    setMode("create");
  };

  const startEdit = (lt: TBoxLinkType) => {
    setEditing(lt);
    setForm({
      slpra_iri: lt.slpra_iri,
      label: lt.label ?? "",
      domain_iri: lt.domain_iri ?? "",
      range_iri: lt.range_iri ?? "",
      inverse_iri: lt.inverse_iri ?? "",
      min_cardinality: lt.min_cardinality?.toString() ?? "",
      max_cardinality: lt.max_cardinality?.toString() ?? "",
    });
    setError(null);
    setMsg(null);
    setMode("edit");
  };

  const submitForm = async () => {
    setError(null);
    const card = {
      min_cardinality: form.min_cardinality === "" ? null : Number(form.min_cardinality),
      max_cardinality: form.max_cardinality === "" ? null : Number(form.max_cardinality),
    };
    try {
      if (mode === "edit" && editing) {
        await updateLinkType(editing.slpra_iri, {
          label: form.label,
          domain_iri: form.domain_iri || null,
          range_iri: form.range_iri || null,
          ...card,
          expected_version: editing.version,
        });
        afterWrite("已更新关系");
      } else {
        await createLinkType({
          slpra_iri: form.slpra_iri,
          label: form.label,
          domain_iri: form.domain_iri || null,
          range_iri: form.range_iri || null,
          inverse_iri: form.inverse_iri || null,
          ...card,
        });
        afterWrite("已创建关系");
      }
    } catch (e) {
      setError(String(e));
    }
  };

  const remove = async (lt: TBoxLinkType) => {
    if (!confirm(`删除关系「${lt.label || lt.slpra_iri}」？`)) return;
    setError(null);
    try {
      await deleteLinkType(lt.slpra_iri, lt.version);
      afterWrite("已删除关系");
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-700">
          对象属性 / 关系
          <span className="ml-1 text-xs font-normal text-gray-400">({items.length})</span>
        </h3>
        {mode === "list" && (
          <button
            onClick={startCreate}
            className="rounded bg-blue-600 px-2.5 py-1 text-xs text-white hover:bg-blue-700"
          >
            + 关系
          </button>
        )}
      </div>

      {error && <p className="rounded bg-red-50 px-2 py-1 text-xs text-red-600">{error}</p>}
      {msg && mode === "list" && (
        <p className="rounded bg-green-50 px-2 py-1 text-xs text-green-600">{msg}</p>
      )}

      {/* 列表视图 */}
      {mode === "list" && (
        <ul className="divide-y rounded border text-sm">
          {items.map((lt) => {
            const inherited = lt.inherited_from_iri != null;
            return (
              <li
                key={lt.id}
                className={`flex items-center justify-between gap-2 px-2 py-1.5 ${inherited ? "bg-gray-50/60" : ""}`}
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-mono text-xs text-gray-500">{tail(lt.slpra_iri)}</span>
                    {lt.label && <span className="truncate text-gray-800">{lt.label}</span>}
                    {inherited && (
                      <span
                        className="shrink-0 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] text-amber-700"
                        title={`继承自 ${lt.inherited_from_label ?? lt.inherited_from_iri}`}
                      >
                        继承自 {lt.inherited_from_label ?? tail(lt.inherited_from_iri)}
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-gray-400">
                    <span className="rounded bg-gray-100 px-1.5 py-0.5 font-mono">
                      {tail(lt.domain_iri) ?? "—"} → {tail(lt.range_iri) ?? "—"}
                    </span>
                    {lt.is_functional && <span className="rounded bg-gray-100 px-1.5 py-0.5">functional</span>}
                    {lt.is_symmetric && <span className="rounded bg-gray-100 px-1.5 py-0.5">symmetric</span>}
                    {lt.is_transitive && <span className="rounded bg-gray-100 px-1.5 py-0.5">transitive</span>}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  {inherited ? (
                    <span className="rounded border border-dashed px-2 py-0.5 text-xs text-gray-400">
                      只读
                    </span>
                  ) : (
                    <>
                      <button
                        onClick={() => startEdit(lt)}
                        className="rounded border px-2 py-0.5 text-xs text-gray-600 hover:bg-gray-50"
                      >
                        编辑
                      </button>
                      <button
                        onClick={() => remove(lt)}
                        className="rounded border border-red-200 px-2 py-0.5 text-xs text-red-600 hover:bg-red-50"
                      >
                        删除
                      </button>
                    </>
                  )}
                </div>
              </li>
            );
          })}
          {items.length === 0 && (
            <li className="px-2 py-3 text-center text-xs text-gray-400">
              {selectedClassIri ? "该类暂无关系" : "暂无关系"}
            </li>
          )}
        </ul>
      )}

      {/* 新建 / 编辑表单 */}
      {(mode === "create" || mode === "edit") && (
        <div className="space-y-2 rounded border bg-gray-50 p-2">
          <p className="text-xs font-medium text-gray-600">
            {mode === "edit" ? "编辑关系" : "新建对象属性 / 关系"}
          </p>
          <Field label="IRI" hint="关系唯一标识">
            <input
              placeholder="IRI"
              value={form.slpra_iri}
              disabled={mode === "edit"}
              onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs disabled:bg-gray-100 disabled:text-gray-400"
            />
          </Field>
          <Field label="标签" hint="显示名称">
            <input
              placeholder="标签"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              className="w-full rounded border px-2 py-1 text-sm"
            />
          </Field>
          <Field label="定义域 domain" hint="关系起点（主语）类 IRI">
            <input
              placeholder="domain IRI"
              value={form.domain_iri}
              onChange={(e) => setForm({ ...form, domain_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs"
            />
          </Field>
          <Field label="值域 range" hint="关系指向（宾语）类 IRI">
            <input
              placeholder="range IRI"
              value={form.range_iri}
              onChange={(e) => setForm({ ...form, range_iri: e.target.value })}
              className="w-full rounded border px-2 py-1 font-mono text-xs"
            />
          </Field>
          <div className="flex gap-2">
            <Field label="最小基数 min" className="w-1/2">
              <input
                placeholder="min 基数"
                value={form.min_cardinality}
                onChange={(e) => setForm({ ...form, min_cardinality: e.target.value })}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </Field>
            <Field label="最大基数 max" className="w-1/2">
              <input
                placeholder="max 基数"
                value={form.max_cardinality}
                onChange={(e) => setForm({ ...form, max_cardinality: e.target.value })}
                className="w-full rounded border px-2 py-1 text-sm"
              />
            </Field>
          </div>
          {mode === "create" && (
            <Field label="逆属性 inverse" hint="可选，已存在关系的 IRI">
              <input
                placeholder="逆属性 IRI（可选）"
                value={form.inverse_iri}
                onChange={(e) => setForm({ ...form, inverse_iri: e.target.value })}
                className="w-full rounded border px-2 py-1 font-mono text-xs"
              />
            </Field>
          )}
          <div className="flex gap-2">
            <button
              onClick={submitForm}
              className="rounded bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-700"
            >
              {mode === "edit" ? "保存" : "创建关系"}
            </button>
            <button
              onClick={backToList}
              className="rounded border px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-50"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
