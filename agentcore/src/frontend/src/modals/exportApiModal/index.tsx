import { Copy, Check, Key, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import {
  oneDark,
  oneLight,
} from "react-syntax-highlighter/dist/cjs/styles/prism";
import IconComponent from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useDarkStore } from "@/stores/darkStore";
import { api } from "@/controllers/API/api";
import { customGetHostProtocol } from "@/customization/utils/custom-get-host-protocol";

interface ExportApiModalProps {
  open: boolean;
  setOpen: (open: boolean) => void;
  agentId: string;
  agentName: string;
  version: string;
  environment: "uat" | "prod";
  deployId: string;
  /** IDP agents take a base64 document attachment instead of an `input_value`, and return the
   *  extraction synchronously. Their snippets are completely different from a chat agent's. */
  isIdp?: boolean;
}

type TabType = "cURL" | "Python" | "JavaScript";

export default function ExportApiModal({
  open,
  setOpen,
  agentId,
  agentName,
  version,
  environment,
  deployId,
  isIdp = false,
}: ExportApiModalProps) {
  const { t } = useTranslation();
  const dark = useDarkStore((state) => state.dark);
  const [selectedTab, setSelectedTab] = useState<TabType>("cURL");
  const [apiKey, setApiKey] = useState<string | null>(null);
  const [apiKeyPrefix, setApiKeyPrefix] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const { protocol, host } = customGetHostProtocol();
  const baseUrl = `${protocol}//${host}`;
  const envCode = environment === "uat" ? 1 : 2;
  const runUrl = `${baseUrl}/api/run/${agentId}?env=${envCode}&version=${version}`;

  useEffect(() => {
    if (!open) {
      setApiKey(null);
      setApiKeyPrefix(null);
    }
  }, [open]);

  const handleRotateKey = async () => {
    setLoading(true);
    try {
      const response = await api.post(
        `/api/api_key/agent/${agentId}/rotate?environment=${environment}&version=${version}`,
      );
      setApiKey(response.data.api_key);
      setApiKeyPrefix(response.data.key_prefix);
    } catch (error) {
      console.error("Failed to rotate API key:", error);
    } finally {
      setLoading(false);
    }
  };

  const copyToClipboard = (text: string, field: string) => {
    navigator.clipboard.writeText(text).then(() => {
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 2000);
    });
  };

  const displayKey = apiKey || "<YOUR_API_KEY>";

  // ── Chat agents: an input_value + a session_id reused across turns. ──
  const chatCurl = `curl --request POST \\
  '${runUrl}' \\
  --header 'Content-Type: application/json' \\
  --header 'x-api-key: ${displayKey}' \\
  --data '{
    "input_value": "Hello!",
    "session_id": "YOUR_SESSION_ID_HERE"
  }'`;

  const chatPython = `import requests
import uuid

url = "${runUrl}"
headers = {
    "Content-Type": "application/json",
    "x-api-key": "${displayKey}"
}
# Use one session_id per conversation and reuse it for follow-up calls.
session_id = str(uuid.uuid4())
payload = {
    "input_value": "Hello!",
    "session_id": session_id
}

response = requests.post(url, json=payload, headers=headers)
print("session_id:", response.headers.get("X-Session-Id", session_id))
print(response.json())`;

  const chatJs = `// Use one session_id per conversation and reuse it for follow-up calls.
const sessionId = crypto.randomUUID();

const response = await fetch("${runUrl}", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "x-api-key": "${displayKey}"
  },
  body: JSON.stringify({
    input_value: "Hello!",
    session_id: sessionId
  })
});

const data = await response.json();
console.log("session_id:", response.headers.get("X-Session-Id") || sessionId);
console.log(data);`;

  // ── IDP agents: POST the raw file as multipart. The call blocks until the pipeline finishes and
  //    returns the extraction. `document_id` is chosen by the CALLER so the result stays reachable via
  //    GET {runUrl}/documents/{document_id} even if the connection drops mid-run.
  //    (A base64 JSON body on ${runUrl} is also accepted — see the comment in each snippet.)
  const idpFileUrl = `${baseUrl}/api/run/${agentId}/document?env=${envCode}&version=${version}`;

  const idpCurl = `# Runs the published ${version} snapshot and returns the extraction.
# Any file the canvas accepts works: pdf, png, jpg, tiff, xlsx, docx, txt.
# 200 = finished (even a failed extraction). 202 = still running after 5 min -> use poll_url.

curl --request POST \\
  '${idpFileUrl}' \\
  --header 'x-api-key: ${displayKey}' \\
  --form 'file=@invoice.pdf'

# 'document_id' is OPTIONAL — the server mints one and echoes it in X-Idp-Document-Id.
# Send your own only if you want a retry to be idempotent, or want to poll before the run ends:
#   --form "document_id=$(uuidgen | tr 'A-Z' 'a-z')"

# The pipeline is never cancelled, so the result is always collectable afterwards:
curl '${baseUrl}/api/run/${agentId}/documents/<document_id>' \\
  --header 'x-api-key: ${displayKey}'

# JSON alternative (base64 body) — POST ${runUrl}
#   {"document": {"filename": "invoice.pdf", "content_base64": "<base64>"}}`;

  const idpPython = `import requests

url = "${idpFileUrl}"
headers = {"x-api-key": "${displayKey}"}

# Any file the canvas accepts: pdf, png, jpg, tiff, xlsx, docx, txt.
# 'document_id' is optional — omit it and the server mints one for you.
with open("invoice.pdf", "rb") as fh:
    response = requests.post(url, headers=headers, files={"file": fh}, timeout=None)

if response.status_code >= 400:
    raise SystemExit(f"HTTP {response.status_code}: {response.text}")

result = response.json()
document_id = result["document_id"]   # also in the X-Idp-Document-Id response header

# 202 = still running after the server's 5-minute wait. It was NOT cancelled — collect it later.
if response.status_code == 202:
    print("still processing; poll", result["poll_url"])
    raise SystemExit(0)

# NOTE: a FAILED extraction is still a 200 — the diagnostic is in the body, not the status code.
print(result["status"], result["overall_confidence"])   # auto_approved|pending_review|skipped|failed
for header in result["headers"]:
    print(f"  {header['field_name']}: {header['extracted_value']}")

if result["status"] == "failed":
    print("error:", result["error_message"])

# Collect a 202 (or a dropped connection) later:
# requests.get(f"${baseUrl}/api/run/${agentId}/documents/{document_id}", headers=headers)`;

  const idpJs = `// Any file the canvas accepts: pdf, png, jpg, tiff, xlsx, docx, txt.
// 'document_id' is optional — omit it and the server mints one (also in X-Idp-Document-Id).
const form = new FormData();
form.append("file", file, "invoice.pdf");   // e.g. from an <input type="file">

const response = await fetch("${idpFileUrl}", {
  method: "POST",
  headers: { "x-api-key": "${displayKey}" },   // do NOT set Content-Type; the browser adds the boundary
  body: form
});

const result = await response.json();
const documentId = result.document_id;

// 202 = still running after the server's 5-minute wait. Nothing was cancelled or lost.
if (response.status === 202) {
  console.log("still processing; poll", result.poll_url);
} else {
  // A failed extraction is still HTTP 200 — check result.status, not the status code.
  console.log(result.status, result.overall_confidence);
  result.headers.forEach((h) => console.log(h.field_name, h.extracted_value));
}

// Collect it later:
// await fetch(\`${baseUrl}/api/run/${agentId}/documents/\${documentId}\`, {
//   headers: { "x-api-key": "${displayKey}" }
// });`;

  const curlCode = isIdp ? idpCurl : chatCurl;
  const pythonCode = isIdp ? idpPython : chatPython;
  const jsCode = isIdp ? idpJs : chatJs;

  const tabs: { title: TabType; icon: string; language: string; code: string }[] = [
    { title: "cURL", icon: "TerminalSquare", language: "bash", code: curlCode },
    { title: "Python", icon: "BWPython", language: "python", code: pythonCode },
    { title: "JavaScript", icon: "javascript", language: "javascript", code: jsCode },
  ];

  const currentTab = tabs.find((tab) => tab.title === selectedTab);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <IconComponent name="Code2" className="h-5 w-5" />
            {t("API Access")} — {agentName} ({version}, {environment.toUpperCase()})
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4">
          {isIdp && (
            <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-xs space-y-1">
              <p className="font-medium">{t("This is a document (IDP) agent.")}</p>
              <p className="text-muted-foreground">
                {t(
                  "POST the file as multipart/form-data (pdf, png, jpg, tiff, xlsx, docx, txt) — the call " +
                    "returns 400 without one. It waits up to 5 minutes and returns the extracted fields; " +
                    "if the pipeline is still running you get 202 + a poll_url, and nothing is lost. " +
                    "A failed extraction is still HTTP 200 with status=\"failed\". Streaming is not supported.",
                )}
              </p>
            </div>
          )}

          {/* API Key Section */}
          <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Key className="h-4 w-4" />
                {t("API Key")}
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={handleRotateKey}
                disabled={loading}
                className="gap-1.5"
              >
                <RotateCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                {apiKey ? t("Rotate Key") : t("Generate Key")}
              </Button>
            </div>

            {apiKey ? (
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <code className="flex-1 rounded bg-background px-3 py-2 text-xs font-mono break-all border">
                    {apiKey}
                  </code>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="shrink-0"
                    onClick={() => copyToClipboard(apiKey, "apikey")}
                  >
                    {copiedField === "apikey" ? (
                      <Check className="h-4 w-4 text-green-500" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
                <p className="text-xs text-amber-600 dark:text-amber-400">
                  {t("Save this key now — it won't be shown again after you close this dialog.")}
                </p>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t("Click \"Generate Key\" to create an API key for this deployment. If a key already exists, it will be rotated.")}
              </p>
            )}
          </div>

          {/* Endpoint URLs */}
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-muted-foreground w-16 shrink-0">{t("Run")}</span>
              <code className="flex-1 rounded bg-muted px-2 py-1 text-xs font-mono truncate">
                {isIdp ? idpFileUrl : runUrl}
              </code>
              <Button
                variant="ghost"
                size="icon"
                className="shrink-0 h-7 w-7"
                onClick={() => copyToClipboard(isIdp ? idpFileUrl : runUrl, "runurl")}
              >
                {copiedField === "runurl" ? (
                  <Check className="h-3.5 w-3.5 text-green-500" />
                ) : (
                  <Copy className="h-3.5 w-3.5" />
                )}
              </Button>
            </div>
          </div>

          {/* Code Tabs */}
          <div className="rounded-lg border overflow-hidden">
            {/* Tab headers */}
            <div className="flex border-b bg-muted/30">
              {tabs.map((tab) => (
                <button
                  key={tab.title}
                  type="button"
                  className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                    selectedTab === tab.title
                      ? "border-foreground text-foreground"
                      : "border-transparent text-muted-foreground hover:text-foreground"
                  }`}
                  onClick={() => setSelectedTab(tab.title)}
                >
                  <IconComponent name={tab.icon} className="h-3.5 w-3.5" />
                  {tab.title}
                </button>
              ))}

              {/* Copy button */}
              <div className="ml-auto flex items-center pr-2">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={() =>
                    currentTab && copyToClipboard(currentTab.code, "code")
                  }
                >
                  {copiedField === "code" ? (
                    <Check className="h-3.5 w-3.5 text-green-500" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            </div>

            {/* Code content */}
            <div className="max-h-64 overflow-auto">
              {currentTab && (
                <SyntaxHighlighter
                  language={currentTab.language}
                  style={dark ? oneDark : oneLight}
                  customStyle={{
                    margin: 0,
                    borderRadius: 0,
                    fontSize: "12px",
                  }}
                  wrapLongLines
                >
                  {currentTab.code}
                </SyntaxHighlighter>
              )}
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
