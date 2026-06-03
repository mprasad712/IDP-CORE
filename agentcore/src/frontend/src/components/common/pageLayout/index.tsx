import type { To } from "react-router-dom";
import { CustomBanner } from "@/customization/components/custom-banner";
import { useCustomNavigate } from "@/customization/hooks/use-custom-navigate";
import { Button } from "../../ui/button";
import { Separator } from "../../ui/separator";
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
    <div className="flex w-full flex-1 flex-col justify-between overflow-auto overflow-x-hidden bg-background">
      <div className="mx-auto flex w-full  flex-1 flex-col">
        <div className="flex flex-col gap-4 px-4 py-6 pt-0 sm:px-6">
          <CustomBanner />
          <div className="flex w-full items-center justify-between gap-4 space-y-0.5 pb-2 pt-10">
            <div className="flex w-full items-center gap-4">
              {backTo && (
                <Button
                  unstyled
                  onClick={() => navigate(backTo)}
                  data-testid="back_page_button"
                  className="flex-shrink-0"
                >
                  <ForwardedIconComponent
                    name="ChevronLeft"
                    className="flex cursor-pointer"
                  />
                </Button>
              )}
              <div className="flex items-center gap-3">
                <div
                  className="h-8 w-[3px] flex-shrink-0 rounded-full"
                  style={{ background: "#D04A02" }}
                />
                <div className="flex flex-col">
                  <h2
                    className="text-xl font-bold tracking-tight md:text-2xl"
                    data-testid="mainpage_title"
                  >
                    {title}
                    {betaIcon && <span className="store-beta-icon">Beta</span>}
                  </h2>
                  <p className="text-sm text-muted-foreground mt-0.5">{description}</p>
                </div>
              </div>
            </div>
            <div className="flex-shrink-0">{button && button}</div>
          </div>
        </div>
        <div className="flex shrink-0 px-4 sm:px-6">
          <Separator className="flex" />
        </div>
        <div className="flex flex-1 px-4 py-6 pt-7 sm:px-6">{children}</div>
      </div>
    </div>
  );
}