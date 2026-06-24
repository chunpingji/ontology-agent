"use client";

import { useEffect, useState } from "react";
import {
  createRiskDataProperty,
  getRiskVocabularies,
  type RiskVocabulary,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const MANAGED_PREFIX = "https://ontology.pharma-gmp.cn/slpra/core/";

/**
 * 风险属性向导（T037）：基于受控词表（OEB / PDE / 致敏）为类创建风险数据属性，
 * 词表取值由后端 `/risk-vocabularies` 提供（FR-010）。
 */
export function RiskAttributeWizard({
  selectedClassIri,
  onChanged,
  onClose,
}: {
  selectedClassIri: string | null;
  onChanged: () => void;
  onClose?: () => void;
}) {
  const [vocabs, setVocabs] = useState<RiskVocabulary[]>([]);
  const [vocab, setVocab] = useState<string>("");
  const [form, setForm] = useState({ slpra_iri: MANAGED_PREFIX, label: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getRiskVocabularies()
      .then((v) => {
        setVocabs(v);
        if (v.length) setVocab(v[0].key);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const current = vocabs.find((v) => v.key === vocab);

  const submit = async () => {
    setError(null);
    setMsg(null);
    try {
      await createRiskDataProperty({
        slpra_iri: form.slpra_iri,
        label: form.label,
        domain_iri: selectedClassIri || null,
        vocab,
      });
      setMsg(`已创建风险属性（${vocab}）`);
      onChanged();
      onClose?.();
    } catch (e) {
      setError(String(e));
    }
  };

  return (
    <div className="space-y-2">
      <h3 className="text-sm font-semibold text-foreground">风险属性向导</h3>
      {error && <p className="rounded bg-destructive/10 px-2 py-1 text-xs text-destructive">{error}</p>}
      {msg && <p className="rounded bg-success/10 px-2 py-1 text-xs text-success">{msg}</p>}

      <Select value={vocab} onValueChange={(value) => setVocab(value)}>
        <SelectTrigger className="h-auto rounded px-2 py-1 text-sm">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {vocabs.map((v) => (
            <SelectItem key={v.key} value={v.key}>
              {v.key} — {v.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {current && (
        <div className="flex flex-wrap gap-1">
          {current.values.map((val) => (
            <Badge
              key={val}
              variant="destructive"
              className="rounded bg-destructive/10 px-1.5 py-0.5 text-xs font-normal text-destructive"
            >
              {val}
            </Badge>
          ))}
        </div>
      )}

      <Input
        placeholder="IRI"
        value={form.slpra_iri}
        onChange={(e) => setForm({ ...form, slpra_iri: e.target.value })}
        className="h-auto rounded px-2 py-1 font-mono text-xs"
      />
      <Input
        placeholder="标签"
        value={form.label}
        onChange={(e) => setForm({ ...form, label: e.target.value })}
        className="h-auto rounded px-2 py-1 text-sm"
      />
      <div className="flex gap-2">
        <Button
          onClick={submit}
          size="sm"
          className="h-auto rounded bg-destructive px-3 py-1.5 text-sm text-destructive-foreground hover:bg-destructive/90"
        >
          创建风险属性
        </Button>
        {onClose && (
          <Button
            onClick={onClose}
            variant="outline"
            size="sm"
            className="h-auto rounded px-3 py-1.5 text-sm text-muted-foreground"
          >
            取消
          </Button>
        )}
      </div>
    </div>
  );
}
