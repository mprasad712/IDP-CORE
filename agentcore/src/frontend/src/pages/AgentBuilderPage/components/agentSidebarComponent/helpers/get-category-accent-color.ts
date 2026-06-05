const SIDEBAR_CATEGORY_ACCENTS: Record<string, string> = {
  input_output: "#2563eb",
  agents: "#16a34a",
  mcp: "#0ea5e9",
  models: "#c026d3",
  vectorstores: "#ca8a04",
  processing: "#475569",
  logic: "#64748b",
  tools: "#06b6d4",
  Guardrails: "#6b7280",
  HumanInTheLoop: "#6b7280",
  outputs: "#dc2626",
  prompts: "#7c3aed",
  chains: "#f97316",
  helpers: "#0ea5e9",
  // IDP categories
  idp_input:          "#D04A02",
  idp_preprocessing:  "#E07020",
  idp_ocr:            "#2563EB",
  idp_extraction:     "#7C3AED",
  idp_rules:          "#059669",
  idp_output:         "#DC2626",
  idp_classification: "#0891B2",
  idp_detection:      "#9333EA",
  idp_validation:     "#16A34A",
  idp_flow_control:   "#D97706",
};

export const getCategoryAccentColor = (
  categoryName: string,
  nodeColors: Record<string, string>,
) => {
  return SIDEBAR_CATEGORY_ACCENTS[categoryName] ?? nodeColors[categoryName] ?? "#2563eb";
};

