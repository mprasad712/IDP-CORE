import {
  Plus,
  Sparkles,
  Pencil,
  Trash2,
  Copy,
  Eye,
  GripVertical,
  Search,
  X,
  FileText,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Lock,
  Globe,
  Users,
} from "lucide-react";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/utils/utils";
import {
  useGetFieldConfigs,
  usePostFieldConfig,
  usePutFieldConfig,
  useDeleteFieldConfig,
  usePostFieldConfigHeader,
  usePutFieldConfigHeader,
  useDeleteFieldConfigHeader,
  usePostFieldConfigLineItem,
  usePutFieldConfigLineItem,
  useDeleteFieldConfigLineItem,
  usePostGenerateConfig,
  type FieldConfig as ApiFieldConfig,
  type FieldConfigHeader,
  type FieldConfigLineItem,
} from "@/controllers/API/queries/field-configs";
import { useGetRegistryModels } from "@/controllers/API/queries/models/use-get-models";

// ─── Types ────────────────────────────────────────────────────────────────────

type FieldDataType = "text" | "number" | "date" | "boolean";

// Coerce a (possibly loose) backend type string to the strict ConfigField.dataType union.
function normalizeDataType(t: string | null | undefined): FieldDataType {
  const v = (t ?? "").toLowerCase().trim();
  if (["number", "integer", "int", "float", "decimal", "numeric"].includes(v)) return "number";
  if (["date", "datetime", "timestamp"].includes(v)) return "date";
  if (["boolean", "bool"].includes(v)) return "boolean";
  return "text";
}

interface ConfigField {
  id: string;       // UUID when from DB, short random string when newly added in form
  name: string;
  dataType: FieldDataType;
  required: boolean;
  displayOrder: number;
  prompt: string;
}

type Visibility = "private" | "org" | "dept";

interface FieldConfig {
  id: string;
  name: string;
  description: string;
  doc_type: string;
  visibility: Visibility;
  headerFields: ConfigField[];
  lineItemColumns: ConfigField[];
  createdBy: string;
  createdDate: string;
  isTemplate: boolean;
}

// ─── Adapter helpers ──────────────────────────────────────────────────────────

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const isDbId = (id: string) => UUID_RE.test(id);

function apiHeaderToField(h: FieldConfigHeader): ConfigField {
  return {
    id: h.id,
    name: h.field_name,
    dataType: h.field_type,
    required: h.is_required,
    displayOrder: h.display_order,
    prompt: h.prompt ?? "",
  };
}

function apiLineItemToField(li: FieldConfigLineItem): ConfigField {
  return {
    id: li.id,
    name: li.column_name,
    dataType: li.column_type as FieldDataType,
    required: li.is_required,
    displayOrder: li.display_order,
    prompt: li.prompt ?? "",
  };
}

function apiConfigToLocal(c: ApiFieldConfig): FieldConfig {
  return {
    id: c.id,
    name: c.name,
    description: c.description ?? "",
    doc_type: c.doc_type ?? "",
    visibility: (c.visibility ?? "org") as Visibility,
    headerFields: [...c.headers]
      .sort((a, b) => a.display_order - b.display_order)
      .map(apiHeaderToField),
    lineItemColumns: [...c.line_items]
      .sort((a, b) => a.display_order - b.display_order)
      .map(apiLineItemToField),
    createdBy: c.created_by ?? "system",
    createdDate: c.created_at.slice(0, 10),
    isTemplate: c.is_template,
  };
}

// ─── Constants ────────────────────────────────────────────────────────────────

const DATA_TYPES: FieldDataType[] = ["text", "number", "date", "boolean"];

function uid() { return Math.random().toString(36).slice(2, 9); }
function emptyField(order: number): ConfigField {
  return { id: uid(), name: "", dataType: "text", required: false, displayOrder: order, prompt: "" };
}

// ─── Shared field table (for edit dialog) ────────────────────────────────────
// Each row has an expand toggle that reveals an editable prompt textarea.

function FieldsTable({ fields, onChange }: { fields: ConfigField[]; onChange: (f: ConfigField[]) => void }) {
  // Expand all rows by default so prompts are always visible/editable.
  const [expandedRows, setExpandedRows] = useState<Set<string>>(
    () => new Set(fields.map((f) => f.id)),
  );

  const toggleExpand = (id: string) =>
    setExpandedRows((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const allExpanded = fields.length > 0 && fields.every((f) => expandedRows.has(f.id));
  const toggleAll = () =>
    setExpandedRows(allExpanded ? new Set() : new Set(fields.map((f) => f.id)));

  const addRow    = () => onChange([...fields, emptyField(fields.length + 1)]);
  const removeRow = (id: string) => {
    setExpandedRows((prev) => { const next = new Set(prev); next.delete(id); return next; });
    onChange(fields.filter((f) => f.id !== id));
  };
  const update = (id: string, key: keyof ConfigField, value: any) =>
    onChange(fields.map((f) => (f.id === id ? { ...f, [key]: value } : f)));

  return (
    <div className="space-y-2">
      <div className="rounded-xl border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30 hover:bg-muted/30">
              <TableHead className="w-6" />
              <TableHead className="w-6">
                {fields.length > 0 && (
                  <button
                    onClick={toggleAll}
                    title={allExpanded ? "Collapse all prompts" : "Expand all prompts"}
                    className="text-muted-foreground/40 hover:text-[#D04A02] transition-colors"
                  >
                    {allExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                  </button>
                )}
              </TableHead>
              <TableHead className="text-xs font-semibold uppercase tracking-wide">Field Name</TableHead>
              <TableHead className="w-32 text-xs font-semibold uppercase tracking-wide">Data Type</TableHead>
              <TableHead className="w-20 text-xs font-semibold uppercase tracking-wide text-center">Required</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {fields.length === 0 && (
              <TableRow>
                <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-8 italic">
                  No fields — click Add below.
                </TableCell>
              </TableRow>
            )}
            {fields.map((field) => {
              const expanded = expandedRows.has(field.id);
              return (
                <>
                  <TableRow key={field.id} className="hover:bg-muted/10">
                    <TableCell className="px-2 cursor-grab text-muted-foreground/30">
                      <GripVertical className="h-3.5 w-3.5" />
                    </TableCell>
                    <TableCell className="px-1">
                      <button
                        onClick={() => toggleExpand(field.id)}
                        title={expanded ? "Hide prompt" : "Edit extraction prompt"}
                        className={cn(
                          "relative text-muted-foreground/40 hover:text-[#D04A02] transition-colors",
                          expanded && "text-[#D04A02]",
                        )}
                      >
                        {expanded
                          ? <ChevronDown className="h-3.5 w-3.5" />
                          : <ChevronRight className="h-3.5 w-3.5" />}
                        {!expanded && field.prompt && (
                          <span className="absolute -top-0.5 -right-0.5 h-1.5 w-1.5 rounded-full bg-[#D04A02]" />
                        )}
                      </button>
                    </TableCell>
                    <TableCell>
                      <Input
                        value={field.name}
                        onChange={(e) => update(field.id, "name", e.target.value)}
                        placeholder="field_name"
                        className="h-7 text-sm font-mono"
                      />
                    </TableCell>
                    <TableCell>
                      <Select value={field.dataType} onValueChange={(v) => update(field.id, "dataType", v)}>
                        <SelectTrigger className="h-7 text-sm"><SelectValue /></SelectTrigger>
                        <SelectContent>
                          {DATA_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell className="text-center">
                      <Checkbox
                        checked={field.required}
                        onCheckedChange={(v) => update(field.id, "required", Boolean(v))}
                      />
                    </TableCell>
                    <TableCell>
                      <button
                        onClick={() => removeRow(field.id)}
                        className="text-muted-foreground/30 hover:text-destructive transition-colors"
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                    </TableCell>
                  </TableRow>

                  {expanded && (
                    <TableRow key={`${field.id}-prompt`} className="bg-muted/5 hover:bg-muted/5">
                      <TableCell colSpan={6} className="px-3 pb-3 pt-0">
                        <div className="flex flex-col gap-1 pl-12">
                          <label className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground/60">
                            Extraction Prompt
                          </label>
                          <textarea
                            value={field.prompt}
                            onChange={(e) => update(field.id, "prompt", e.target.value)}
                            placeholder="Describe how to extract this field from the document…"
                            rows={field.prompt ? 3 : 2}
                            className={cn(
                              "w-full resize-y rounded-lg border px-3 py-2 text-xs focus:outline-none focus:ring-1 placeholder:italic placeholder:text-muted-foreground/40",
                              field.prompt
                                ? "bg-background text-foreground border-[#D04A02]/30 focus:ring-[#D04A02]/40"
                                : "bg-muted/30 text-muted-foreground border-dashed focus:ring-[#D04A02]/30",
                            )}
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                  )}
                </>
              );
            })}
          </TableBody>
        </Table>
      </div>
      <Button variant="outline" size="sm" onClick={addRow} className="gap-1.5 text-xs rounded-lg">
        <Plus className="h-3.5 w-3.5" /> Add Field
      </Button>
    </div>
  );
}

function SchemaPreview({ config }: { config: Partial<FieldConfig> }) {
  const Section = ({ title, fields }: { title: string; fields: ConfigField[] }) => (
    <div>
      <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/50 mb-2">{title}</p>
      {fields.length === 0 ? (
        <p className="text-xs text-muted-foreground italic">None defined.</p>
      ) : (
        <div className="rounded-xl border divide-y overflow-hidden">
          {fields.map((f) => (
            <div key={f.id} className="flex items-center justify-between px-3 py-1.5 gap-2">
              <span className="font-mono text-xs truncate">{f.name || <span className="italic text-muted-foreground/40">unnamed</span>}</span>
              <div className="flex gap-1 flex-shrink-0">
                <Badge variant="secondary" className="text-[10px] px-1.5 py-0 rounded-md">{f.dataType}</Badge>
                {f.required && <Badge className="text-[10px] px-1.5 py-0 rounded-md bg-[#D04A02]/10 text-[#D04A02] border border-[#D04A02]/20 hover:bg-[#D04A02]/10">req</Badge>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
  return (
    <div className="space-y-5 text-sm">
      <Section title="Header Fields"     fields={config.headerFields ?? []} />
      <Section title="Line Item Columns" fields={config.lineItemColumns ?? []} />
    </div>
  );
}

// ─── Edit / Create dialog ─────────────────────────────────────────────────────

const VISIBILITY_OPTIONS: { value: Visibility; label: string; description: string; icon: React.ReactNode }[] = [
  { value: "private", label: "Private",      description: "Only visible to you",                    icon: <Lock    className="h-3.5 w-3.5" /> },
  { value: "org",     label: "Organisation", description: "Visible to all org members",             icon: <Globe   className="h-3.5 w-3.5" /> },
  { value: "dept",    label: "Department",   description: "Visible to your department members",     icon: <Users   className="h-3.5 w-3.5" /> },
];

function ConfigDialog({ open, initial, onSave, onClose, saving, error, templates }: {
  open: boolean;
  initial: Partial<FieldConfig> | null;
  onSave: (cfg: FieldConfig) => void;
  onClose: () => void;
  saving?: boolean;
  error?: string | null;
  templates?: FieldConfig[];
}) {
  const isNew = !initial?.id;
  const [name,           setName]           = useState(initial?.name ?? "");
  const [description,    setDescription]    = useState(initial?.description ?? "");
  const [docType,        setDocType]        = useState(initial?.doc_type ?? "");
  const [visibility,     setVisibility]     = useState<Visibility>(initial?.visibility ?? "org");
  const [headerFields,   setHeaderFields]   = useState<ConfigField[]>(initial?.headerFields ?? []);
  const [lineItemColumns,setLineItemColumns] = useState<ConfigField[]>(initial?.lineItemColumns ?? []);

  // Find the best-matching template by field-name overlap (for the "fill from template" feature).
  const matchedTemplate = templates?.find((t) => {
    const tNames = new Set(t.headerFields.map((f) => f.name));
    const overlap = (initial?.headerFields ?? []).filter((f) => tNames.has(f.name)).length;
    return overlap >= Math.min(3, (initial?.headerFields ?? []).length);
  }) ?? null;

  const fillFromTemplate = () => {
    if (!matchedTemplate) return;
    const tHeaderMap = new Map(matchedTemplate.headerFields.map((f) => [f.name, f.prompt]));
    const tLineMap   = new Map(matchedTemplate.lineItemColumns.map((f) => [f.name, f.prompt]));
    setHeaderFields((prev) =>
      prev.map((f) => ({ ...f, prompt: f.prompt || tHeaderMap.get(f.name) || f.prompt })),
    );
    setLineItemColumns((prev) =>
      prev.map((f) => ({ ...f, prompt: f.prompt || tLineMap.get(f.name) || f.prompt })),
    );
  };

  const handleSave = () => {
    if (!name.trim()) return;
    onSave({
      id: initial?.id ?? uid(),
      name: name.trim(),
      description: description.trim(),
      doc_type: docType.trim(),
      visibility,
      headerFields,
      lineItemColumns,
      createdBy:   initial?.createdBy  ?? "me",
      createdDate: initial?.createdDate ?? new Date().toISOString().slice(0, 10),
      isTemplate:  initial?.isTemplate ?? false,
    });
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-5xl h-[85vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-5 pb-4 border-b flex-shrink-0">
          <DialogTitle>{isNew ? "New Field Configuration" : `Edit: ${initial?.name}`}</DialogTitle>
        </DialogHeader>
        <div className="flex flex-1 overflow-hidden">
          <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6 border-r">
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Configuration Name <span className="text-destructive">*</span></label>
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Standard Invoice" className="rounded-lg" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Description</label>
              <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What documents does this schema extract?" className="rounded-lg" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Document Type</label>
              <p className="text-xs text-muted-foreground -mt-0.5">Used by the Document Classifier node to route documents to this configuration (e.g. "Invoice", "Contract").</p>
              <Input value={docType} onChange={(e) => setDocType(e.target.value)} placeholder="e.g. Invoice" className="rounded-lg" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Visibility</label>
              <p className="text-xs text-muted-foreground -mt-1">Controls who can use this configuration. When an agent using it is published to org or moved to prod, update this to match.</p>
              <div className="grid grid-cols-3 gap-2">
                {VISIBILITY_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setVisibility(opt.value)}
                    className={cn(
                      "flex flex-col items-start gap-1 rounded-xl border px-3 py-2.5 text-left transition-all",
                      visibility === opt.value
                        ? "border-[#D04A02] bg-[#D04A02]/5 text-[#D04A02]"
                        : "border-border bg-muted/10 text-muted-foreground hover:border-[#D04A02]/30 hover:text-foreground",
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-xs font-semibold">{opt.icon}{opt.label}</span>
                    <span className="text-[10px] leading-tight opacity-70">{opt.description}</span>
                  </button>
                ))}
              </div>
            </div>
            {matchedTemplate && (
              <div className="flex items-start gap-3 rounded-lg border border-[#D04A02]/30 bg-[#D04A02]/5 px-3 py-2.5">
                <FileText className="h-4 w-4 text-[#D04A02] mt-0.5 flex-shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-[#D04A02]">Cloned from: {matchedTemplate.name}</p>
                  <p className="text-[11px] text-muted-foreground mt-0.5">
                    Fill empty prompts from the original template. Your existing edits won't be overwritten.
                  </p>
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={fillFromTemplate}
                  className="flex-shrink-0 text-xs border-[#D04A02]/40 text-[#D04A02] hover:bg-[#D04A02]/10 rounded-lg"
                >
                  Fill Prompts
                </Button>
              </div>
            )}
            <div className="space-y-2">
              <p className="text-sm font-semibold">Header Fields</p>
              <p className="text-xs text-muted-foreground">
                Top-level fields extracted once per document. Edit each field's extraction prompt below its row.
              </p>
              <FieldsTable fields={headerFields} onChange={setHeaderFields} />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-semibold">Line Item Columns</p>
              <p className="text-xs text-muted-foreground">
                Columns in the repeating line items table. Edit each column's extraction prompt below its row.
              </p>
              <FieldsTable fields={lineItemColumns} onChange={setLineItemColumns} />
            </div>
          </div>
          <div className="w-64 shrink-0 overflow-y-auto px-5 py-5 bg-muted/10">
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/50 mb-4">Schema Preview</p>
            <SchemaPreview config={{ headerFields, lineItemColumns }} />
          </div>
        </div>
        <DialogFooter className="px-6 py-4 border-t flex-shrink-0 flex-col gap-2 items-stretch">
          {error && (
            <p className="text-xs text-destructive bg-destructive/5 border border-destructive/20 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={onClose} className="rounded-lg" disabled={saving}>Cancel</Button>
            <Button onClick={handleSave} disabled={!name.trim() || saving} className="bg-[#D04A02] hover:bg-[#B84000] text-white rounded-lg">
              {saving ? "Saving…" : isNew ? "Create Configuration" : "Save Changes"}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Generate-from-prompt dialog ──────────────────────────────────────────────
// Calls POST /field-configs/generate (LLM drafts header + line-item fields from a description),
// then hands the draft to the normal Create dialog for review + save.

function GenerateConfigDialog({ open, onClose, onGenerated }: {
  open: boolean;
  onClose: () => void;
  onGenerated: (draft: FieldConfig) => void;
}) {
  const [description, setDescription] = useState("");
  const [modelId, setModelId]         = useState("");
  const [error, setError]             = useState<string | null>(null);
  const [generating, setGenerating]   = useState(false);
  const { data: models } = useGetRegistryModels();
  const { mutateAsync: generate } = usePostGenerateConfig();

  const handleGenerate = async () => {
    if (!description.trim() || !modelId) return;
    setError(null);
    setGenerating(true);
    try {
      const draft = await generate({ description: description.trim(), model_id: modelId });
      onGenerated({
        id: uid(),                       // non-UUID → "new" so the Create dialog saves it
        name: draft.suggested_name || "Generated Configuration",
        description: description.trim(),
        doc_type: "",
        visibility: "org",
        headerFields: draft.headers.map((h) => ({
          id: uid(), name: h.field_name, dataType: normalizeDataType(h.field_type), required: h.is_required,
          displayOrder: h.display_order, prompt: h.prompt ?? "",
        })),
        lineItemColumns: draft.line_items.map((c) => ({
          id: uid(), name: c.column_name, dataType: normalizeDataType(c.column_type), required: c.is_required,
          displayOrder: c.display_order, prompt: c.prompt ?? "",
        })),
        createdBy: "me",
        createdDate: new Date().toISOString().slice(0, 10),
        isTemplate: false,
      });
      setDescription("");
      setModelId("");
    } catch (e: any) {
      setError(
        e?.response?.data?.detail ??
        "Could not generate a configuration. Try a clearer description or a different model.",
      );
    } finally {
      setGenerating(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) onClose(); }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Generate Field Configuration with AI</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 px-1 py-2">
          <div className="space-y-1.5">
            <label className="text-xs font-medium">Describe the document type</label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={4}
              placeholder="e.g. A purchase order with PO number, vendor, order date, and line items of description, quantity and unit price"
              className="w-full rounded-lg border bg-background/50 text-sm px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-[#D04A02]/30"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-medium">Model</label>
            <Select value={modelId} onValueChange={setModelId}>
              <SelectTrigger><SelectValue placeholder="Select a model" /></SelectTrigger>
              <SelectContent>
                {(models ?? []).map((m) => (
                  <SelectItem key={m.id} value={m.id}>
                    {m.provider} · {m.model_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {error && <p className="text-xs text-destructive">{error}</p>}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button
            onClick={handleGenerate}
            disabled={generating || !description.trim() || !modelId}
            className="bg-[#D04A02] hover:bg-[#B84000] text-white disabled:opacity-60"
          >
            {generating ? "Generating…" : "Generate"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Template Preview Dialog ──────────────────────────────────────────────────
// Read-only view. Shows field name, type, required, and the extraction prompt.

function TemplatePreviewDialog({ template, onClone, onClose }: {
  template: FieldConfig | null;
  onClone: (t: FieldConfig) => void;
  onClose: () => void;
}) {
  if (!template) return null;

  const FieldSection = ({ title, fields, accent }: { title: string; fields: ConfigField[]; accent: string }) => (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <span className={cn("text-[10px] font-bold uppercase tracking-widest", accent)}>{title}</span>
        <span className="text-[10px] text-muted-foreground">({fields.length} fields)</span>
      </div>
      {fields.length === 0 ? (
        <p className="text-xs text-muted-foreground italic px-1">Not applicable for this document type.</p>
      ) : (
        <div className="rounded-xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-muted/30 border-b">
                <th className="text-left px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Field Name</th>
                <th className="text-left px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground w-24">Type</th>
                <th className="text-center px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground w-20">Required</th>
                <th className="text-left px-3 py-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Extraction Prompt</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {fields.map((f) => (
                <tr key={f.id} className="hover:bg-muted/10 transition-colors align-top">
                  <td className="px-4 py-2.5 font-mono text-xs">{f.name}</td>
                  <td className="px-3 py-2.5">
                    <Badge variant="secondary" className="text-[10px] px-1.5 py-0 rounded-md font-normal">{f.dataType}</Badge>
                  </td>
                  <td className="px-3 py-2.5 text-center">
                    {f.required
                      ? <CheckCircle2 className="h-3.5 w-3.5 text-[#D04A02] mx-auto" />
                      : <span className="text-muted-foreground/30 text-xs">—</span>
                    }
                  </td>
                  <td className="px-3 py-2.5">
                    {f.prompt
                      ? (
                        <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3" title={f.prompt}>
                          {f.prompt}
                        </p>
                      )
                      : <span className="text-muted-foreground/30 text-xs">—</span>
                    }
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );

  return (
    <Dialog open={!!template} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-4xl h-[82vh] flex flex-col p-0 gap-0">
        <DialogHeader className="px-6 pt-5 pb-4 border-b flex-shrink-0">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-[#D04A02]/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <FileText className="h-5 w-5 text-[#D04A02]" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-lg leading-tight">{template.name}</DialogTitle>
              <div className="flex items-center gap-2 mt-0.5">
                <p className="text-sm text-muted-foreground">{template.description}</p>
                {template.doc_type && (
                  <Badge variant="outline" className="text-[10px] rounded-md border-[#D04A02]/40 text-[#D04A02] bg-[#D04A02]/5 flex-shrink-0">{template.doc_type}</Badge>
                )}
              </div>
              <div className="flex items-center gap-3 mt-2">
                <span className="text-[11px] text-muted-foreground">
                  <span className="font-semibold text-foreground">{template.headerFields.length}</span> header fields
                </span>
                {template.lineItemColumns.length > 0 && (
                  <>
                    <span className="text-muted-foreground/30">·</span>
                    <span className="text-[11px] text-muted-foreground">
                      <span className="font-semibold text-foreground">{template.lineItemColumns.length}</span> line item columns
                    </span>
                  </>
                )}
                <span className="text-muted-foreground/30">·</span>
                <span className="text-[11px] text-muted-foreground">
                  <span className="font-semibold text-foreground">
                    {template.headerFields.filter(f => f.required).length + template.lineItemColumns.filter(f => f.required).length}
                  </span> required
                </span>
              </div>
            </div>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6 min-h-0">
          <div className="flex items-center gap-2 rounded-lg bg-muted/20 border px-3 py-2">
            <Eye className="h-3.5 w-3.5 text-muted-foreground flex-shrink-0" />
            <p className="text-xs text-muted-foreground">
              This is a read-only system template. Clone it to create an editable copy where you can customise field names, types, and extraction prompts.
            </p>
          </div>
          <FieldSection title="Header Fields"     fields={template.headerFields}   accent="text-[#D04A02]" />
          <FieldSection title="Line Item Columns" fields={template.lineItemColumns} accent="text-blue-500" />
        </div>

        <DialogFooter className="px-6 py-4 border-t flex-shrink-0 bg-muted/5">
          <p className="text-xs text-muted-foreground mr-auto self-center">
            Cloning creates an editable copy in your organisation.
          </p>
          <Button variant="outline" onClick={onClose} className="rounded-lg">Close</Button>
          <Button
            onClick={() => { onClone(template); onClose(); }}
            className="flex-1 gap-1.5 text-xs rounded-lg bg-[#D04A02] hover:bg-[#B84000] text-white"
          >
            <Copy className="h-3.5 w-3.5" /> Clone & Edit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function FieldConfigurationsPage() {
  const [search,     setSearch]     = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing,    setEditing]    = useState<FieldConfig | null>(null);
  const [genOpen,    setGenOpen]    = useState(false);
  const [previewing, setPreviewing] = useState<FieldConfig | null>(null);
  const [saving,     setSaving]     = useState(false);
  const [saveError,  setSaveError]  = useState<string | null>(null);

  const queryClient = useQueryClient();

  // ── Queries ──
  const {
    data: customPage,
    isLoading: loadingCustom,
    isError: customError,
    error: customErrorRaw,
    refetch: refetchCustom,
  } = useGetFieldConfigs(
    { is_template: false, size: 100 },
    { staleTime: 0, refetchOnWindowFocus: true },
  );
  const { data: templatePage, isLoading: loadingTemplates } = useGetFieldConfigs(
    { is_template: true, size: 100 },
    { staleTime: 60_000 },
  );

  const configs   = (customPage?.items ?? []).map(apiConfigToLocal);
  const templates = (templatePage?.items ?? []).map(apiConfigToLocal);

  const filtered = configs.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase()),
  );

  // ── Mutations ──
  const { mutateAsync: createConfig }   = usePostFieldConfig();
  const { mutateAsync: updateConfig }   = usePutFieldConfig();
  const { mutateAsync: deleteConfig }   = useDeleteFieldConfig();
  const { mutateAsync: createHeader }   = usePostFieldConfigHeader();
  const { mutateAsync: updateHeader }   = usePutFieldConfigHeader();
  const { mutateAsync: deleteHeader }   = useDeleteFieldConfigHeader();
  const { mutateAsync: createLineItem } = usePostFieldConfigLineItem();
  const { mutateAsync: updateLineItem } = usePutFieldConfigLineItem();
  const { mutateAsync: deleteLineItem } = useDeleteFieldConfigLineItem();

  // ── Helpers ──
  const openCreate = () => { setEditing(null); setDialogOpen(true); };
  const openEdit   = (cfg: FieldConfig) => { setEditing(cfg); setDialogOpen(true); };

  const syncHeaders = async (configId: string, newFields: ConfigField[], originalFields: ConfigField[]) => {
    const originalMap = new Map(originalFields.map((f) => [f.id, f]));
    const newIds = new Set(newFields.filter((f) => isDbId(f.id)).map((f) => f.id));

    for (const orig of originalFields) {
      if (!newIds.has(orig.id)) {
        await deleteHeader({ configId, headerId: orig.id });
      }
    }
    for (const f of newFields) {
      if (!isDbId(f.id)) {
        await createHeader({
          configId,
          payload: {
            field_name: f.name,
            field_type: f.dataType,
            is_required: f.required,
            display_order: f.displayOrder,
            prompt: f.prompt || null,
          },
        });
      } else {
        const orig = originalMap.get(f.id);
        const changed = !orig
          || orig.name !== f.name
          || orig.dataType !== f.dataType
          || orig.required !== f.required
          || orig.displayOrder !== f.displayOrder
          || orig.prompt !== f.prompt;
        if (changed) {
          await updateHeader({
            configId,
            headerId: f.id,
            payload: {
              field_name: f.name,
              field_type: f.dataType,
              is_required: f.required,
              display_order: f.displayOrder,
              prompt: f.prompt || null,
            },
          });
        }
      }
    }
  };

  const syncLineItems = async (configId: string, newFields: ConfigField[], originalFields: ConfigField[]) => {
    const originalMap = new Map(originalFields.map((f) => [f.id, f]));
    const newIds = new Set(newFields.filter((f) => isDbId(f.id)).map((f) => f.id));

    for (const orig of originalFields) {
      if (!newIds.has(orig.id)) {
        await deleteLineItem({ configId, lineItemId: orig.id });
      }
    }
    for (const f of newFields) {
      const colType = (f.dataType === "boolean" ? "text" : f.dataType) as "text" | "number" | "date";
      if (!isDbId(f.id)) {
        await createLineItem({
          configId,
          payload: {
            column_name: f.name,
            column_type: colType,
            is_required: f.required,
            display_order: f.displayOrder,
            prompt: f.prompt || null,
          },
        });
      } else {
        const orig = originalMap.get(f.id);
        const changed = !orig
          || orig.name !== f.name
          || orig.dataType !== f.dataType
          || orig.required !== f.required
          || orig.displayOrder !== f.displayOrder
          || orig.prompt !== f.prompt;
        if (changed) {
          await updateLineItem({
            configId,
            lineItemId: f.id,
            payload: {
              column_name: f.name,
              column_type: colType,
              is_required: f.required,
              display_order: f.displayOrder,
              prompt: f.prompt || null,
            },
          });
        }
      }
    }
  };

  const handleSave = async (cfg: FieldConfig) => {
    setSaving(true);
    setSaveError(null);
    try {
      if (!isDbId(cfg.id)) {
        const created = await createConfig({
          name: cfg.name,
          description: cfg.description || null,
          doc_type: cfg.doc_type || null,
          is_template: false,
          visibility: cfg.visibility,
          headers: cfg.headerFields.map((f) => ({
            field_name: f.name,
            field_type: f.dataType,
            is_required: f.required,
            display_order: f.displayOrder,
            prompt: f.prompt || null,
          })),
        });
        await Promise.all(
          cfg.lineItemColumns.map((f) => {
            const colType = (f.dataType === "boolean" ? "text" : f.dataType) as "text" | "number" | "date";
            return createLineItem({
              configId: created.id,
              payload: {
                column_name: f.name,
                column_type: colType,
                is_required: f.required,
                display_order: f.displayOrder,
                prompt: f.prompt || null,
              },
            });
          }),
        );
      } else {
        await updateConfig({ id: cfg.id, payload: { name: cfg.name, description: cfg.description || null, doc_type: cfg.doc_type || null, visibility: cfg.visibility } });
        await syncHeaders(cfg.id, cfg.headerFields, editing?.headerFields ?? []);
        await syncLineItems(cfg.id, cfg.lineItemColumns, editing?.lineItemColumns ?? []);
      }
      // Force immediate re-fetch so the custom tab reflects the change
      await queryClient.invalidateQueries({ queryKey: ["useGetFieldConfigs"] });
      refetchCustom();
      setDialogOpen(false);
      setSaveError(null);
    } catch (err: any) {
      const msg = err?.response?.data?.detail ?? err?.message ?? "Something went wrong. Please try again.";
      setSaveError(typeof msg === "string" ? msg : JSON.stringify(msg));
    } finally {
      setSaving(false);
    }
  };

  // Clone opens the edit dialog pre-populated so the user can review/adjust before saving.
  const handleClone = (cfg: FieldConfig) => {
    const cloned: FieldConfig = {
      id: uid(),   // non-UUID → signals "new" to handleSave
      name: `${cfg.name} (Copy)`,
      description: cfg.description,
      doc_type: cfg.doc_type,
      visibility: cfg.isTemplate ? "org" : cfg.visibility,
      headerFields: cfg.headerFields.map((f) => ({ ...f, id: uid() })),
      lineItemColumns: cfg.lineItemColumns.map((f) => ({ ...f, id: uid() })),
      createdBy: "me",
      createdDate: new Date().toISOString().slice(0, 10),
      isTemplate: false,
    };
    setEditing(cloned);
    setDialogOpen(true);
  };

  const handleDelete = async (id: string) => {
    await deleteConfig(id);
  };

  return (
    <div className="flex flex-col h-full bg-background overflow-hidden">

      {/* ── Page header ── */}
      <div className="flex-shrink-0 border-b px-6 py-4 flex items-center justify-between bg-muted/5">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Field Configurations</h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Define named extraction schemas used by the Named Config extraction node.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => setGenOpen(true)} className="gap-1.5 rounded-lg">
            <Sparkles className="h-4 w-4" /> Generate with AI
          </Button>
          <Button onClick={openCreate} className="gap-1.5 bg-[#D04A02] hover:bg-[#B84000] text-white rounded-lg">
            <Plus className="h-4 w-4" /> New Configuration
          </Button>
        </div>
      </div>

      <Tabs defaultValue="custom" className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-shrink-0 px-6 pt-3 border-b">
          <TabsList className="h-9 bg-transparent p-0 gap-1">
            {([
              { value: "custom",    label: "Custom Configurations", count: configs.length    },
              { value: "templates", label: "Templates / Catalogue",  count: templates.length },
            ] as const).map(({ value, label, count }) => (
              <TabsTrigger
                key={value}
                value={value}
                className={cn(
                  "h-9 rounded-none border-b-2 border-transparent px-4 text-sm font-medium text-muted-foreground transition-all",
                  "data-[state=active]:border-[#D04A02] data-[state=active]:text-foreground data-[state=active]:shadow-none data-[state=active]:bg-transparent",
                  "hover:text-foreground",
                )}
              >
                {label}
                <span className={cn(
                  "ml-1.5 rounded-full px-1.5 py-0 text-[10px] font-semibold tabular-nums",
                  "bg-muted text-muted-foreground",
                )}>
                  {count}
                </span>
              </TabsTrigger>
            ))}
          </TabsList>
        </div>

        {/* ── Custom tab ── */}
        <TabsContent value="custom" className="flex-1 overflow-auto m-0">
          <div className="px-6 py-5">
            <div className="flex items-center gap-3 mb-5">
              <div className="relative flex-1 max-w-sm">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search configurations…" className="pl-9 rounded-lg" />
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => refetchCustom()}
                className="gap-1.5 rounded-lg text-xs"
              >
                ↻ Refresh
              </Button>
            </div>
            {customError && (
              <div className="mb-4 flex flex-col gap-1 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2 text-xs text-destructive">
                <div className="flex items-center gap-2">
                  <span className="font-medium">Failed to load configurations.</span>
                  <button onClick={() => refetchCustom()} className="underline font-medium ml-auto">Retry</button>
                </div>
                {(customErrorRaw as any)?.response?.data?.detail && (
                  <span className="text-destructive/70 font-mono">
                    {typeof (customErrorRaw as any).response.data.detail === "string"
                      ? (customErrorRaw as any).response.data.detail
                      : JSON.stringify((customErrorRaw as any).response.data.detail)}
                  </span>
                )}
                <span className="text-destructive/60">Restart the backend server if this persists — a database migration may be pending.</span>
              </div>
            )}
            <div className="rounded-xl border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableHead className="text-xs font-semibold uppercase tracking-wide">Name</TableHead>
                    <TableHead className="text-xs font-semibold uppercase tracking-wide">Description</TableHead>
                    <TableHead className="w-28 text-xs font-semibold uppercase tracking-wide">Doc Type</TableHead>
                    <TableHead className="w-24 text-center text-xs font-semibold uppercase tracking-wide">Header Fields</TableHead>
                    <TableHead className="w-24 text-center text-xs font-semibold uppercase tracking-wide">Line Columns</TableHead>
                    <TableHead className="w-24 text-xs font-semibold uppercase tracking-wide">Visibility</TableHead>
                    <TableHead className="w-28 text-xs font-semibold uppercase tracking-wide">Created</TableHead>
                    <TableHead className="w-32 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {loadingCustom && (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center text-sm text-muted-foreground py-14">
                        Loading…
                      </TableCell>
                    </TableRow>
                  )}
                  {!loadingCustom && !customError && filtered.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center text-sm text-muted-foreground py-14">
                        {search ? "No configurations match your search." : "No custom configurations yet — create one or clone a template."}
                      </TableCell>
                    </TableRow>
                  )}
                  {customError && !loadingCustom && (
                    <TableRow>
                      <TableCell colSpan={8} className="text-center text-sm text-destructive py-14">
                        Could not load configurations. Click Refresh to try again.
                      </TableCell>
                    </TableRow>
                  )}
                  {filtered.map((cfg) => (
                    <TableRow key={cfg.id} className="group hover:bg-muted/20 transition-colors">
                      <TableCell className="font-medium">{cfg.name}</TableCell>
                      <TableCell className="text-sm text-muted-foreground max-w-xs truncate">{cfg.description}</TableCell>
                      <TableCell>
                        {cfg.doc_type ? (
                          <Badge variant="outline" className="text-[10px] rounded-md border-[#D04A02]/40 text-[#D04A02] bg-[#D04A02]/5">{cfg.doc_type}</Badge>
                        ) : (
                          <span className="text-muted-foreground/40 text-xs">—</span>
                        )}
                      </TableCell>
                      <TableCell className="text-center"><Badge variant="outline" className="text-xs rounded-md">{cfg.headerFields.length}</Badge></TableCell>
                      <TableCell className="text-center"><Badge variant="outline" className="text-xs rounded-md">{cfg.lineItemColumns.length}</Badge></TableCell>
                      <TableCell>
                        <Badge
                          variant="secondary"
                          className={cn(
                            "text-[10px] rounded-md gap-1 px-1.5 py-0",
                            cfg.visibility === "private" && "bg-amber-50 text-amber-700 border border-amber-200",
                            cfg.visibility === "org"     && "bg-blue-50 text-blue-700 border border-blue-200",
                            cfg.visibility === "dept"    && "bg-purple-50 text-purple-700 border border-purple-200",
                          )}
                        >
                          {cfg.visibility === "private" && <Lock  className="h-2.5 w-2.5" />}
                          {cfg.visibility === "org"     && <Globe className="h-2.5 w-2.5" />}
                          {cfg.visibility === "dept"    && <Users className="h-2.5 w-2.5" />}
                          {cfg.visibility}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-sm text-muted-foreground tabular-nums">{cfg.createdDate}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button title="Edit"   onClick={() => openEdit(cfg)}         className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"><Pencil  className="h-3.5 w-3.5" /></button>
                          <button title="Clone"  onClick={() => handleClone(cfg)}      className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"><Copy    className="h-3.5 w-3.5" /></button>
                          <button title="Delete" onClick={() => handleDelete(cfg.id)}  className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-destructive transition-colors"><Trash2  className="h-3.5 w-3.5" /></button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </div>
        </TabsContent>

        {/* ── Templates tab ── */}
        <TabsContent value="templates" className="flex-1 overflow-auto m-0">
          <div className="px-6 py-5">
            <p className="text-sm text-muted-foreground mb-5">
              Ready-made extraction schemas. Preview a template to see its fields and prompts, then clone it to create an editable copy.
            </p>
            {loadingTemplates ? (
              <p className="text-sm text-muted-foreground">Loading templates…</p>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
                {templates.map((tpl) => (
                  <div
                    key={tpl.id}
                    className="group flex flex-col rounded-xl border bg-card p-4 gap-3 hover:border-[#D04A02]/30 hover:shadow-sm transition-all cursor-default"
                  >
                    <div className="flex items-center gap-2.5">
                      <div className="h-8 w-8 rounded-lg bg-[#D04A02]/10 flex items-center justify-center flex-shrink-0">
                        <FileText className="h-4 w-4 text-[#D04A02]" />
                      </div>
                      <div className="min-w-0">
                        <p className="font-semibold text-sm leading-tight">{tpl.name}</p>
                        <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
                          <p className="text-[10px] text-muted-foreground">
                            {tpl.headerFields.length} fields
                            {tpl.lineItemColumns.length > 0 && ` · ${tpl.lineItemColumns.length} line cols`}
                          </p>
                          {tpl.doc_type && (
                            <Badge variant="outline" className="text-[9px] px-1 py-0 rounded border-[#D04A02]/30 text-[#D04A02] bg-[#D04A02]/5">{tpl.doc_type}</Badge>
                          )}
                        </div>
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground flex-1 leading-relaxed">{tpl.description}</p>
                    <div className="flex items-center gap-2 pt-1">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPreviewing(tpl)}
                        className="flex-1 gap-1.5 text-xs rounded-lg hover:border-[#D04A02]/30 hover:text-[#D04A02]"
                      >
                        <Eye className="h-3.5 w-3.5" /> Preview
                      </Button>
                      <Button
                        size="sm"
                        onClick={() => handleClone(tpl)}
                        disabled={saving}
                        className="flex-1 gap-1.5 text-xs rounded-lg bg-[#D04A02] hover:bg-[#B84000] text-white"
                      >
                        <Copy className="h-3.5 w-3.5" /> Clone
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </TabsContent>
      </Tabs>

      <ConfigDialog
        key={editing?.id ?? "new"}
        open={dialogOpen}
        initial={editing}
        onSave={handleSave}
        onClose={() => { setDialogOpen(false); setSaveError(null); }}
        saving={saving}
        error={saveError}
        templates={templates}
      />

      <TemplatePreviewDialog
        template={previewing}
        onClone={handleClone}
        onClose={() => setPreviewing(null)}
      />

      <GenerateConfigDialog
        open={genOpen}
        onClose={() => setGenOpen(false)}
        onGenerated={(draft) => { setGenOpen(false); setEditing(draft); setDialogOpen(true); }}
      />
    </div>
  );
}
