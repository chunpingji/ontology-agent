import { ReactNode, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface DataProperty {
  iri: string | null;
  label: string;
  value: string;
}

interface EditDialogProps<T> {
  trigger: ReactNode;
  title: string;
  description: string;
  item: T | null;
  fields: Array<{
    key: keyof T;
    label: string;
    type?: "text" | "textarea";
    required?: boolean;
    readonly?: boolean;
  }>;
  onSave: (item: T) => Promise<unknown>;
  onClose?: () => void;
}

export function EditDialog<T extends Record<string, any>>({
  trigger,
  title,
  description,
  item,
  fields,
  onSave,
  onClose,
}: EditDialogProps<T>) {
  const [open, setOpen] = useState(false);
  const [formData, setFormData] = useState<T>(item || ({} as T));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleOpen = (isOpen: boolean) => {
    setOpen(isOpen);
    if (isOpen) {
      setFormData(item || ({} as T));
      setError("");
    } else {
      onClose?.();
    }
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      await onSave(formData);
      setOpen(false);
    } catch (err) {
      setError(String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4 py-4">
          {fields.map((field) => (
            <div key={String(field.key)} className="space-y-2">
              <Label htmlFor={String(field.key)}>
                {field.label}
                {field.required && <span className="text-destructive ml-1">*</span>}
              </Label>
              {field.type === "textarea" ? (
                <Textarea
                  id={String(field.key)}
                  value={String(formData[field.key] || "")}
                  onChange={(e) =>
                    setFormData({ ...formData, [field.key]: e.target.value })
                  }
                  disabled={field.readonly}
                  rows={4}
                />
              ) : (
                <Input
                  id={String(field.key)}
                  value={String(formData[field.key] || "")}
                  onChange={(e) =>
                    setFormData({ ...formData, [field.key]: e.target.value })
                  }
                  disabled={field.readonly}
                />
              )}
            </div>
          ))}
        </div>

        {error && (
          <div className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={saving}>
            取消
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? "保存中..." : "保存"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
