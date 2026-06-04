import {
  Plus,
  Pencil,
  Trash2,
  Copy,
  Eye,
  GripVertical,
  Search,
  X,
  FileText,
  CheckCircle2,
} from "lucide-react";
import { useState } from "react";
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

// ─── Types ────────────────────────────────────────────────────────────────────

type FieldDataType = "text" | "number" | "date" | "boolean";

interface ConfigField {
  id: string;
  name: string;
  dataType: FieldDataType;
  required: boolean;
  displayOrder: number;
}

interface FieldConfig {
  id: string;
  name: string;
  description: string;
  headerFields: ConfigField[];
  lineItemColumns: ConfigField[];
  createdBy: string;
  createdDate: string;
  usedByAgents: number;
}

interface TemplateEntry {
  name: string;
  description: string;
  icon: string;
  headerFields: ConfigField[];
  lineItemColumns: ConfigField[];
}

// ─── Template catalogue with full schemas ─────────────────────────────────────

function tf(id: string, name: string, dataType: FieldDataType, required: boolean, order: number): ConfigField {
  return { id, name, dataType, required, displayOrder: order };
}

const TEMPLATE_CATALOGUE: TemplateEntry[] = [
  {
    name: "Invoice",
    description: "Standard invoice with vendor, date, line items and totals.",
    icon: "FileText",
    headerFields: [
      tf("h1", "invoice_number",  "text",    true,  1),
      tf("h2", "invoice_date",    "date",    true,  2),
      tf("h3", "vendor_name",     "text",    true,  3),
      tf("h4", "vendor_address",  "text",    false, 4),
      tf("h5", "buyer_name",      "text",    false, 5),
      tf("h6", "due_date",        "date",    false, 6),
      tf("h7", "subtotal",        "number",  true,  7),
      tf("h8", "tax_amount",      "number",  false, 8),
      tf("h9", "total_amount",    "number",  true,  9),
      tf("h10","currency",        "text",    false, 10),
    ],
    lineItemColumns: [
      tf("l1", "description",  "text",   true,  1),
      tf("l2", "quantity",     "number", true,  2),
      tf("l3", "unit_price",   "number", true,  3),
      tf("l4", "tax_rate",     "number", false, 4),
      tf("l5", "line_total",   "number", true,  5),
    ],
  },
  {
    name: "PAN Card",
    description: "Indian PAN card with name, DOB and PAN number.",
    icon: "CreditCard",
    headerFields: [
      tf("h1", "pan_number",       "text", true,  1),
      tf("h2", "full_name",        "text", true,  2),
      tf("h3", "father_name",      "text", false, 3),
      tf("h4", "date_of_birth",    "date", true,  4),
      tf("h5", "signature_present","boolean", false, 5),
    ],
    lineItemColumns: [],
  },
  {
    name: "Aadhaar Card",
    description: "Indian Aadhaar card with UID, name and address.",
    icon: "IdCard",
    headerFields: [
      tf("h1", "aadhaar_number", "text",    true,  1),
      tf("h2", "full_name",      "text",    true,  2),
      tf("h3", "date_of_birth",  "date",    true,  3),
      tf("h4", "gender",         "text",    false, 4),
      tf("h5", "address",        "text",    true,  5),
      tf("h6", "pincode",        "text",    false, 6),
      tf("h7", "qr_data",        "text",    false, 7),
    ],
    lineItemColumns: [],
  },
  {
    name: "Purchase Order",
    description: "PO with buyer, supplier, items and delivery terms.",
    icon: "ShoppingCart",
    headerFields: [
      tf("h1", "po_number",       "text",   true,  1),
      tf("h2", "po_date",         "date",   true,  2),
      tf("h3", "buyer_name",      "text",   true,  3),
      tf("h4", "supplier_name",   "text",   true,  4),
      tf("h5", "delivery_date",   "date",   false, 5),
      tf("h6", "delivery_terms",  "text",   false, 6),
      tf("h7", "total_amount",    "number", true,  7),
      tf("h8", "currency",        "text",   false, 8),
    ],
    lineItemColumns: [
      tf("l1", "item_code",   "text",   false, 1),
      tf("l2", "description", "text",   true,  2),
      tf("l3", "quantity",    "number", true,  3),
      tf("l4", "unit",        "text",   false, 4),
      tf("l5", "unit_price",  "number", true,  5),
      tf("l6", "line_total",  "number", true,  6),
    ],
  },
  {
    name: "Receipt",
    description: "Point-of-sale or payment receipt.",
    icon: "Receipt",
    headerFields: [
      tf("h1", "merchant_name",     "text",   true,  1),
      tf("h2", "merchant_address",  "text",   false, 2),
      tf("h3", "transaction_date",  "date",   true,  3),
      tf("h4", "transaction_time",  "text",   false, 4),
      tf("h5", "total_amount",      "number", true,  5),
      tf("h6", "payment_method",    "text",   false, 6),
      tf("h7", "receipt_number",    "text",   false, 7),
    ],
    lineItemColumns: [
      tf("l1", "item",     "text",   true,  1),
      tf("l2", "quantity", "number", false, 2),
      tf("l3", "price",    "number", true,  3),
    ],
  },
  {
    name: "Contract",
    description: "Legal contract with parties, dates and clauses.",
    icon: "Scroll",
    headerFields: [
      tf("h1", "contract_title",   "text", true,  1),
      tf("h2", "party_one",        "text", true,  2),
      tf("h3", "party_two",        "text", true,  3),
      tf("h4", "effective_date",   "date", true,  4),
      tf("h5", "expiry_date",      "date", false, 5),
      tf("h6", "governing_law",    "text", false, 6),
      tf("h7", "contract_value",   "number", false, 7),
      tf("h8", "signature_present","boolean", false, 8),
    ],
    lineItemColumns: [],
  },
  {
    name: "Bank Statement",
    description: "Bank account transactions for a given period.",
    icon: "Building2",
    headerFields: [
      tf("h1", "bank_name",       "text",   true,  1),
      tf("h2", "account_number",  "text",   true,  2),
      tf("h3", "account_holder",  "text",   true,  3),
      tf("h4", "statement_from",  "date",   true,  4),
      tf("h5", "statement_to",    "date",   true,  5),
      tf("h6", "opening_balance", "number", false, 6),
      tf("h7", "closing_balance", "number", true,  7),
      tf("h8", "currency",        "text",   false, 8),
    ],
    lineItemColumns: [
      tf("l1", "date",        "date",   true,  1),
      tf("l2", "description", "text",   true,  2),
      tf("l3", "debit",       "number", false, 3),
      tf("l4", "credit",      "number", false, 4),
      tf("l5", "balance",     "number", true,  5),
    ],
  },
  {
    name: "Pay Slip",
    description: "Employee salary slip with earnings and deductions.",
    icon: "Wallet",
    headerFields: [
      tf("h1", "employee_name",   "text",   true,  1),
      tf("h2", "employee_id",     "text",   false, 2),
      tf("h3", "designation",     "text",   false, 3),
      tf("h4", "pay_period",      "text",   true,  4),
      tf("h5", "basic_salary",    "number", true,  5),
      tf("h6", "gross_salary",    "number", true,  6),
      tf("h7", "total_deductions","number", false, 7),
      tf("h8", "net_salary",      "number", true,  8),
    ],
    lineItemColumns: [
      tf("l1", "component",  "text",   true,  1),
      tf("l2", "type",       "text",   false, 2),
      tf("l3", "amount",     "number", true,  3),
    ],
  },
  {
    name: "Passport",
    description: "Passport with MRZ, name, nationality and expiry.",
    icon: "Globe",
    headerFields: [
      tf("h1", "passport_number", "text", true,  1),
      tf("h2", "surname",         "text", true,  2),
      tf("h3", "given_names",     "text", true,  3),
      tf("h4", "nationality",     "text", true,  4),
      tf("h5", "date_of_birth",   "date", true,  5),
      tf("h6", "gender",          "text", false, 6),
      tf("h7", "issue_date",      "date", true,  7),
      tf("h8", "expiry_date",     "date", true,  8),
      tf("h9", "mrz_line1",       "text", false, 9),
      tf("h10","mrz_line2",       "text", false, 10),
    ],
    lineItemColumns: [],
  },
  {
    name: "Driving Licence",
    description: "Driving licence with number, class and expiry.",
    icon: "Car",
    headerFields: [
      tf("h1", "licence_number",  "text", true,  1),
      tf("h2", "full_name",       "text", true,  2),
      tf("h3", "date_of_birth",   "date", true,  3),
      tf("h4", "address",         "text", false, 4),
      tf("h5", "issue_date",      "date", true,  5),
      tf("h6", "expiry_date",     "date", true,  6),
      tf("h7", "vehicle_class",   "text", false, 7),
      tf("h8", "issuing_authority","text", false, 8),
    ],
    lineItemColumns: [],
  },
  {
    name: "Utility Bill",
    description: "Electricity / water / gas bill with usage and amount.",
    icon: "Zap",
    headerFields: [
      tf("h1", "provider_name",   "text",   true,  1),
      tf("h2", "customer_name",   "text",   true,  2),
      tf("h3", "account_number",  "text",   false, 3),
      tf("h4", "bill_date",       "date",   true,  4),
      tf("h5", "due_date",        "date",   false, 5),
      tf("h6", "billing_period",  "text",   false, 6),
      tf("h7", "units_consumed",  "number", false, 7),
      tf("h8", "total_amount",    "number", true,  8),
    ],
    lineItemColumns: [],
  },
  {
    name: "Medical Report",
    description: "Lab / diagnostic report with patient and findings.",
    icon: "Stethoscope",
    headerFields: [
      tf("h1", "patient_name",    "text", true,  1),
      tf("h2", "patient_id",      "text", false, 2),
      tf("h3", "date_of_birth",   "date", false, 3),
      tf("h4", "report_date",     "date", true,  4),
      tf("h5", "report_type",     "text", true,  5),
      tf("h6", "referring_doctor","text", false, 6),
      tf("h7", "lab_name",        "text", false, 7),
    ],
    lineItemColumns: [
      tf("l1", "test_name",   "text", true,  1),
      tf("l2", "result",      "text", true,  2),
      tf("l3", "reference",   "text", false, 3),
      tf("l4", "unit",        "text", false, 4),
      tf("l5", "status",      "text", false, 5),
    ],
  },
];

// ─── Seed custom configs ──────────────────────────────────────────────────────

const INITIAL_CONFIGS: FieldConfig[] = [
  {
    id: "1",
    name: "Standard Invoice",
    description: "Extracts all key invoice fields plus line items.",
    headerFields: [
      { id: "h1", name: "invoice_number", dataType: "text",   required: true,  displayOrder: 1 },
      { id: "h2", name: "invoice_date",   dataType: "date",   required: true,  displayOrder: 2 },
      { id: "h3", name: "vendor_name",    dataType: "text",   required: true,  displayOrder: 3 },
      { id: "h4", name: "total_amount",   dataType: "number", required: true,  displayOrder: 4 },
      { id: "h5", name: "tax_amount",     dataType: "number", required: false, displayOrder: 5 },
    ],
    lineItemColumns: [
      { id: "l1", name: "description", dataType: "text",   required: true,  displayOrder: 1 },
      { id: "l2", name: "quantity",    dataType: "number", required: true,  displayOrder: 2 },
      { id: "l3", name: "unit_price",  dataType: "number", required: true,  displayOrder: 3 },
      { id: "l4", name: "line_total",  dataType: "number", required: true,  displayOrder: 4 },
    ],
    createdBy: "admin",
    createdDate: "2026-05-10",
    usedByAgents: 2,
  },
];

const DATA_TYPES: FieldDataType[] = ["text", "number", "date", "boolean"];

function uid() { return Math.random().toString(36).slice(2, 9); }
function emptyField(order: number): ConfigField {
  return { id: uid(), name: "", dataType: "text", required: false, displayOrder: order };
}

// ─── Shared field table (for edit dialog) ─────────────────────────────────────

function FieldsTable({ fields, onChange }: { fields: ConfigField[]; onChange: (f: ConfigField[]) => void }) {
  const addRow    = () => onChange([...fields, emptyField(fields.length + 1)]);
  const removeRow = (id: string) => onChange(fields.filter((f) => f.id !== id));
  const update    = (id: string, key: keyof ConfigField, value: any) =>
    onChange(fields.map((f) => (f.id === id ? { ...f, [key]: value } : f)));

  return (
    <div className="space-y-2">
      <div className="rounded-xl border overflow-hidden">
        <Table>
          <TableHeader>
            <TableRow className="bg-muted/30 hover:bg-muted/30">
              <TableHead className="w-6" />
              <TableHead className="text-xs font-semibold uppercase tracking-wide">Field Name</TableHead>
              <TableHead className="w-32 text-xs font-semibold uppercase tracking-wide">Data Type</TableHead>
              <TableHead className="w-20 text-xs font-semibold uppercase tracking-wide text-center">Required</TableHead>
              <TableHead className="w-8" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {fields.length === 0 && (
              <TableRow>
                <TableCell colSpan={5} className="text-center text-sm text-muted-foreground py-8 italic">
                  No fields — click Add below.
                </TableCell>
              </TableRow>
            )}
            {fields.map((field) => (
              <TableRow key={field.id} className="hover:bg-muted/10">
                <TableCell className="px-2 cursor-grab text-muted-foreground/30"><GripVertical className="h-3.5 w-3.5" /></TableCell>
                <TableCell>
                  <Input value={field.name} onChange={(e) => update(field.id, "name", e.target.value)} placeholder="field_name" className="h-7 text-sm font-mono" />
                </TableCell>
                <TableCell>
                  <Select value={field.dataType} onValueChange={(v) => update(field.id, "dataType", v)}>
                    <SelectTrigger className="h-7 text-sm"><SelectValue /></SelectTrigger>
                    <SelectContent>{DATA_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}</SelectContent>
                  </Select>
                </TableCell>
                <TableCell className="text-center">
                  <Checkbox checked={field.required} onCheckedChange={(v) => update(field.id, "required", Boolean(v))} />
                </TableCell>
                <TableCell>
                  <button onClick={() => removeRow(field.id)} className="text-muted-foreground/30 hover:text-destructive transition-colors">
                    <X className="h-3.5 w-3.5" />
                  </button>
                </TableCell>
              </TableRow>
            ))}
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

function ConfigDialog({ open, initial, onSave, onClose }: {
  open: boolean;
  initial: Partial<FieldConfig> | null;
  onSave: (cfg: FieldConfig) => void;
  onClose: () => void;
}) {
  const isNew = !initial?.id;
  const [name,           setName]           = useState(initial?.name ?? "");
  const [description,    setDescription]    = useState(initial?.description ?? "");
  const [headerFields,   setHeaderFields]   = useState<ConfigField[]>(initial?.headerFields ?? []);
  const [lineItemColumns,setLineItemColumns] = useState<ConfigField[]>(initial?.lineItemColumns ?? []);

  const handleSave = () => {
    if (!name.trim()) return;
    onSave({
      id: initial?.id ?? uid(),
      name: name.trim(),
      description: description.trim(),
      headerFields,
      lineItemColumns,
      createdBy:    initial?.createdBy   ?? "me",
      createdDate:  initial?.createdDate ?? new Date().toISOString().slice(0, 10),
      usedByAgents: initial?.usedByAgents ?? 0,
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
            <div className="space-y-2">
              <p className="text-sm font-semibold">Header Fields</p>
              <p className="text-xs text-muted-foreground">Top-level fields extracted once per document.</p>
              <FieldsTable fields={headerFields} onChange={setHeaderFields} />
            </div>
            <div className="space-y-2">
              <p className="text-sm font-semibold">Line Item Columns</p>
              <p className="text-xs text-muted-foreground">Columns in the repeating line items table.</p>
              <FieldsTable fields={lineItemColumns} onChange={setLineItemColumns} />
            </div>
          </div>
          <div className="w-64 shrink-0 overflow-y-auto px-5 py-5 bg-muted/10">
            <p className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground/50 mb-4">Schema Preview</p>
            <SchemaPreview config={{ headerFields, lineItemColumns }} />
          </div>
        </div>
        <DialogFooter className="px-6 py-4 border-t flex-shrink-0">
          <Button variant="outline" onClick={onClose} className="rounded-lg">Cancel</Button>
          <Button onClick={handleSave} disabled={!name.trim()} className="bg-[#D04A02] hover:bg-[#B84000] text-white rounded-lg">
            {isNew ? "Create Configuration" : "Save Changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Template Preview Dialog ──────────────────────────────────────────────────

function TemplatePreviewDialog({ template, onClone, onClose }: {
  template: TemplateEntry | null;
  onClone: (t: TemplateEntry) => void;
  onClose: () => void;
}) {
  if (!template) return null;
  const totalFields = template.headerFields.length + template.lineItemColumns.length;

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
              </tr>
            </thead>
            <tbody className="divide-y">
              {fields.map((f) => (
                <tr key={f.id} className="hover:bg-muted/10 transition-colors">
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
      <DialogContent className="max-w-2xl h-[80vh] flex flex-col p-0 gap-0">
        {/* Header */}
        <DialogHeader className="px-6 pt-5 pb-4 border-b flex-shrink-0">
          <div className="flex items-start gap-3">
            <div className="h-10 w-10 rounded-xl bg-[#D04A02]/10 flex items-center justify-center flex-shrink-0 mt-0.5">
              <FileText className="h-5 w-5 text-[#D04A02]" />
            </div>
            <div className="min-w-0">
              <DialogTitle className="text-lg leading-tight">{template.name}</DialogTitle>
              <p className="text-sm text-muted-foreground mt-0.5">{template.description}</p>
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
                  <span className="font-semibold text-foreground">{template.headerFields.filter(f => f.required).length + template.lineItemColumns.filter(f => f.required).length}</span> required
                </span>
              </div>
            </div>
          </div>
        </DialogHeader>

        {/* Scrollable schema */}
        <div className="flex-1 overflow-y-auto px-6 py-5 space-y-6 min-h-0">
          <FieldSection
            title="Header Fields"
            fields={template.headerFields}
            accent="text-[#D04A02]"
          />
          <FieldSection
            title="Line Item Columns"
            fields={template.lineItemColumns}
            accent="text-blue-500"
          />
        </div>

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t flex-shrink-0 bg-muted/5">
          <p className="text-xs text-muted-foreground mr-auto self-center">
            Cloning creates an editable copy in your organisation.
          </p>
          <Button variant="outline" onClick={onClose} className="rounded-lg">Close</Button>
          <Button
            onClick={() => { onClone(template); onClose(); }}
            className="bg-[#D04A02] hover:bg-[#B84000] text-white rounded-lg gap-1.5"
          >
            <Copy className="h-4 w-4" /> Clone & Edit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function FieldConfigurationsPage() {
  const [configs,     setConfigs]     = useState<FieldConfig[]>(INITIAL_CONFIGS);
  const [search,      setSearch]      = useState("");
  const [dialogOpen,  setDialogOpen]  = useState(false);
  const [editing,     setEditing]     = useState<Partial<FieldConfig> | null>(null);
  const [previewing,  setPreviewing]  = useState<TemplateEntry | null>(null);

  const filtered = configs.filter(
    (c) =>
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.description.toLowerCase().includes(search.toLowerCase()),
  );

  const openCreate = () => { setEditing(null); setDialogOpen(true); };
  const openEdit   = (cfg: FieldConfig) => { setEditing(cfg); setDialogOpen(true); };

  const handleClone = (cfg: FieldConfig) =>
    setConfigs((prev) => [...prev, { ...cfg, id: uid(), name: `${cfg.name} (Copy)`, createdDate: new Date().toISOString().slice(0, 10), usedByAgents: 0 }]);

  const handleDelete = (id: string) => setConfigs((prev) => prev.filter((c) => c.id !== id));

  const cloneTemplate = (tpl: TemplateEntry) => {
    setConfigs((prev) => [
      ...prev,
      {
        id: uid(),
        name: tpl.name,
        description: tpl.description,
        headerFields: tpl.headerFields.map((f) => ({ ...f, id: uid() })),
        lineItemColumns: tpl.lineItemColumns.map((f) => ({ ...f, id: uid() })),
        createdBy: "me",
        createdDate: new Date().toISOString().slice(0, 10),
        usedByAgents: 0,
      },
    ]);
  };

  const handleSave = (cfg: FieldConfig) => {
    setConfigs((prev) => {
      const idx = prev.findIndex((c) => c.id === cfg.id);
      if (idx >= 0) { const next = [...prev]; next[idx] = cfg; return next; }
      return [...prev, cfg];
    });
    setDialogOpen(false);
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
        <Button onClick={openCreate} className="gap-1.5 bg-[#D04A02] hover:bg-[#B84000] text-white rounded-lg">
          <Plus className="h-4 w-4" /> New Configuration
        </Button>
      </div>

      <Tabs defaultValue="custom" className="flex-1 flex flex-col overflow-hidden">
        <div className="flex-shrink-0 px-6 pt-3 border-b">
          <TabsList className="h-9 bg-transparent p-0 gap-1">
            {([
              { value: "custom",    label: "Custom Configurations", count: configs.length           },
              { value: "templates", label: "Templates / Catalogue",  count: TEMPLATE_CATALOGUE.length },
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
            </div>
            <div className="rounded-xl border overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow className="bg-muted/30 hover:bg-muted/30">
                    <TableHead className="text-xs font-semibold uppercase tracking-wide">Name</TableHead>
                    <TableHead className="text-xs font-semibold uppercase tracking-wide">Description</TableHead>
                    <TableHead className="w-28 text-center text-xs font-semibold uppercase tracking-wide">Header Fields</TableHead>
                    <TableHead className="w-28 text-center text-xs font-semibold uppercase tracking-wide">Line Columns</TableHead>
                    <TableHead className="w-28 text-xs font-semibold uppercase tracking-wide">Created</TableHead>
                    <TableHead className="w-32 text-right" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filtered.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-center text-sm text-muted-foreground py-14">
                        {search ? "No configurations match your search." : "No custom configurations yet — create one or clone a template."}
                      </TableCell>
                    </TableRow>
                  )}
                  {filtered.map((cfg) => (
                    <TableRow key={cfg.id} className="group hover:bg-muted/20 transition-colors">
                      <TableCell className="font-medium">{cfg.name}</TableCell>
                      <TableCell className="text-sm text-muted-foreground max-w-xs truncate">{cfg.description}</TableCell>
                      <TableCell className="text-center"><Badge variant="outline" className="text-xs rounded-md">{cfg.headerFields.length}</Badge></TableCell>
                      <TableCell className="text-center"><Badge variant="outline" className="text-xs rounded-md">{cfg.lineItemColumns.length}</Badge></TableCell>
                      <TableCell className="text-sm text-muted-foreground tabular-nums">{cfg.createdDate}</TableCell>
                      <TableCell>
                        <div className="flex items-center justify-end gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                          {cfg.usedByAgents > 0 && (
                            <Badge variant="secondary" className="text-[10px] mr-1.5 rounded-md">{cfg.usedByAgents} agents</Badge>
                          )}
                          <button title="Edit"   onClick={() => openEdit(cfg)}     className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"><Pencil  className="h-3.5 w-3.5" /></button>
                          <button title="Clone"  onClick={() => handleClone(cfg)}  className="p-1.5 rounded-lg hover:bg-accent text-muted-foreground hover:text-foreground transition-colors"><Copy    className="h-3.5 w-3.5" /></button>
                          <button title="Delete" onClick={() => handleDelete(cfg.id)} className={cn("p-1.5 rounded-lg hover:bg-accent transition-colors", cfg.usedByAgents > 0 ? "text-amber-500" : "text-muted-foreground hover:text-destructive")}><Trash2  className="h-3.5 w-3.5" /></button>
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
              Ready-made extraction schemas. Preview a template to see its fields, then clone it to create an editable copy.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
              {TEMPLATE_CATALOGUE.map((tpl) => (
                <div
                  key={tpl.name}
                  className="group flex flex-col rounded-xl border bg-card p-4 gap-3 hover:border-[#D04A02]/30 hover:shadow-sm transition-all cursor-default"
                >
                  <div className="flex items-center gap-2.5">
                    <div className="h-8 w-8 rounded-lg bg-[#D04A02]/10 flex items-center justify-center flex-shrink-0">
                      <FileText className="h-4 w-4 text-[#D04A02]" />
                    </div>
                    <div className="min-w-0">
                      <p className="font-semibold text-sm leading-tight">{tpl.name}</p>
                      <p className="text-[10px] text-muted-foreground mt-0.5">
                        {tpl.headerFields.length} fields
                        {tpl.lineItemColumns.length > 0 && ` · ${tpl.lineItemColumns.length} line cols`}
                      </p>
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
                      onClick={() => cloneTemplate(tpl)}
                      className="flex-1 gap-1.5 text-xs rounded-lg bg-[#D04A02] hover:bg-[#B84000] text-white"
                    >
                      <Copy className="h-3.5 w-3.5" /> Clone
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </TabsContent>
      </Tabs>

      <ConfigDialog
        open={dialogOpen}
        initial={editing}
        onSave={handleSave}
        onClose={() => setDialogOpen(false)}
      />

      <TemplatePreviewDialog
        template={previewing}
        onClone={cloneTemplate}
        onClose={() => setPreviewing(null)}
      />
    </div>
  );
}
