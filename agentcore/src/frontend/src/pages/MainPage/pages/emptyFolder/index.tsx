import ForwardedIconComponent from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import { useFolderStore } from "@/stores/foldersStore";

type EmptyFolderProps = {
  setOpenModal: (open: boolean) => void;
  allowCreateInProject: boolean;
};

export const EmptyFolder = ({ setOpenModal, allowCreateInProject }: EmptyFolderProps) => {
  const folders = useFolderStore((state) => state.folders);
  const isFirstTime = (folders?.length ?? 0) <= 1;

  const features = [
    "Extract and classify data from any document format",
    "Build multi-step intelligent processing pipelines",
    "Deploy and monitor agents at enterprise scale",
  ];

  return (
    <div className="flex h-full w-full flex-1 items-center justify-center px-6 py-12">
      <div className="flex w-full max-w-lg flex-col items-center gap-8 text-center">

        {/* ── Illustration ── */}
        <div className="relative flex-shrink-0">
          {/* Outer glow ring */}
          <div
            className="absolute inset-0 rounded-3xl blur-2xl"
            style={{ background: "rgba(208,74,2,0.12)" }}
          />
          {/* Icon container */}
          <div
            className="relative flex h-28 w-28 items-center justify-center rounded-3xl"
            style={{
              background: "linear-gradient(145deg,rgba(208,74,2,0.12) 0%,rgba(208,74,2,0.04) 100%)",
              border: "1px solid rgba(208,74,2,0.15)",
            }}
          >
            {/* dot texture */}
            <div
              className="pointer-events-none absolute inset-0 rounded-3xl opacity-[0.06]"
              style={{
                backgroundImage: "radial-gradient(circle,#D04A02 1px,transparent 1px)",
                backgroundSize: "16px 16px",
              }}
            />
            <svg
              className="relative z-10 h-14 w-14"
              viewBox="0 0 64 64"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              {/* Document stack */}
              <rect x="10" y="16" width="38" height="46" rx="4" fill="rgba(208,74,2,0.08)" stroke="#D04A02" strokeWidth="1.5" />
              <rect x="6"  y="12" width="38" height="46" rx="4" fill="rgba(208,74,2,0.06)" stroke="#D04A02" strokeWidth="1.5" strokeOpacity="0.5" />
              <rect x="14" y="20" width="38" height="46" rx="4" fill="rgba(208,74,2,0.10)" stroke="#D04A02" strokeWidth="1.5" />
              {/* Lines */}
              <line x1="22" y1="34" x2="44" y2="34" stroke="#D04A02" strokeWidth="1.5" strokeLinecap="round" strokeOpacity="0.7" />
              <line x1="22" y1="41" x2="44" y2="41" stroke="#D04A02" strokeWidth="1.5" strokeLinecap="round" strokeOpacity="0.7" />
              <line x1="22" y1="48" x2="36" y2="48" stroke="#D04A02" strokeWidth="1.5" strokeLinecap="round" strokeOpacity="0.7" />
              {/* Plus badge */}
              <circle cx="48" cy="20" r="10" fill="#D04A02" />
              <line x1="48" y1="15" x2="48" y2="25" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
              <line x1="43" y1="20" x2="53" y2="20" stroke="white" strokeWidth="2.5" strokeLinecap="round" />
            </svg>
          </div>

          {/* Decorative corner dots */}
          {["-top-2 -left-2", "-top-2 -right-2", "-bottom-2 -left-2", "-bottom-2 -right-2"].map((pos) => (
            <div
              key={pos}
              className={`absolute ${pos} h-3 w-3 rounded-full border-2 border-background`}
              style={{ background: "rgba(208,74,2,0.4)" }}
            />
          ))}
        </div>

        {/* ── Copy ── */}
        <div className="flex flex-col gap-2">
          <h2 className="text-2xl font-bold text-foreground" data-testid="mainpage_title">
            {isFirstTime ? "Start building" : "Empty project"}
          </h2>
          <p className="text-sm leading-relaxed text-muted-foreground">
            {isFirstTime
              ? "Create your first AI agent to start automating intelligent document processing workflows."
              : "This project has no agents yet. Add one to get started."}
          </p>
        </div>

        {/* ── Feature list (first-time only) ── */}
        {isFirstTime && (
          <div className="w-full rounded-xl border border-border/50 bg-background/60 p-4 text-left shadow-sm">
            <p className="mb-3 text-[11px] font-bold uppercase tracking-wider text-muted-foreground/50">
              What you can do
            </p>
            <div className="flex flex-col gap-2.5">
              {features.map((f) => (
                <div key={f} className="flex items-start gap-2.5">
                  <div
                    className="mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded"
                    style={{ background: "rgba(208,74,2,0.12)" }}
                  >
                    <svg className="h-2.5 w-2.5 text-[#D04A02]" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M2 6l3 3 5-5" />
                    </svg>
                  </div>
                  <span className="text-xs leading-relaxed text-muted-foreground">{f}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* ── CTA ── */}
        {allowCreateInProject && (
          <Button
            variant="default"
            onClick={() => setOpenModal(true)}
            id="new-project-btn"
            data-testid="new_project_btn_empty_page"
            className="gap-2 px-6"
          >
            <ForwardedIconComponent name="Plus" aria-hidden="true" className="h-4 w-4" />
            <span className="font-semibold">New Agent</span>
          </Button>
        )}
      </div>
    </div>
  );
};

export default EmptyFolder;
