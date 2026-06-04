import type { To } from "react-router-dom";
import { CustomBanner } from "@/customization/components/custom-banner";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { Button } from "../../ui/button";
import ForwardedIconComponent from "../genericIconComponent";

export default function PageLayout({
  title,
  description,
  children,
  button,
  betaIcon,
  backTo = "",
}: {
  title: string;
  description: string;
  children: React.ReactNode;
  button?: React.ReactNode;
  betaIcon?: boolean;
  backTo?: To;
}) {
  const navigate = useCustomNavigate();

  return (
    <div className="flex h-full w-full flex-col overflow-hidden">

      {/* ── Sticky page header ── */}
      <div className="flex-shrink-0 border-b bg-background px-6 py-5 shadow-sm">
        <CustomBanner />

        <div className="flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">

            {backTo && (
              <Button
                unstyled
                onClick={() => navigate(backTo)}
                data-testid="back_page_button"
                className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg border border-border bg-background shadow-sm hover:border-[#D04A02]/40 hover:text-[#D04A02] transition-colors"
              >
                <ForwardedIconComponent name="ChevronLeft" className="h-4 w-4" />
              </Button>
            )}

            {/* Orange accent bar + title */}
            <div className="flex items-center gap-3">
              <div
                className="h-9 w-[3px] flex-shrink-0 rounded-full"
                style={{ background: "linear-gradient(180deg, #D04A02 0%, #A83800 100%)" }}
              />
              <div>
                <h1 className="text-lg font-bold leading-tight text-foreground" data-testid="mainpage_title">
                  {title}
                  {betaIcon && <span className="store-beta-icon">Beta</span>}
                </h1>
                {description && (
                  <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
                )}
              </div>
            </div>
          </div>

          {button && <div className="flex-shrink-0">{button}</div>}
        </div>
      </div>

      {/* ── Scrollable body panel ── */}
      <div className="flex flex-1 flex-col overflow-auto">
        <div className="flex flex-1 flex-col p-6">
          <div className="flex flex-1 flex-col rounded-xl border border-border/50 bg-background shadow-sm overflow-hidden">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
