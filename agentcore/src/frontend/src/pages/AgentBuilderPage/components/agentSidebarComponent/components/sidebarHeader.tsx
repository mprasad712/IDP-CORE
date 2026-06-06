import { memo } from "react";

import { ForwardedIconComponent } from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import {
  Disclosure,
  DisclosureContent,
  DisclosureTrigger,
} from "@/components/ui/disclosure";
import { SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";
import { ENABLE_NEW_SIDEBAR } from "@/customization/feature-flags";
import type { SidebarHeaderComponentProps } from "../types";
import FeatureToggles from "./featureTogglesComponent";
import { SearchInput } from "./searchInput";
import { SidebarFilterComponent } from "./sidebarFilterComponent";
import { useTranslation } from 'react-i18next';

export const SidebarHeaderComponent = memo(function SidebarHeaderComponent({
  showConfig,
  setShowConfig,
  showBeta,
  setShowBeta,
  showLegacy,
  setShowLegacy,
  searchInputRef,
  isInputFocused,
  search,
  handleInputFocus,
  handleInputBlur,
  handleInputChange,
  filterName,
  filterDescription,
  resetFilters,
  customComponent,
  addComponent,
  isLoading = false,
  readOnly = false,
  onBack,
}: SidebarHeaderComponentProps) {
  const { t } = useTranslation();
  return (
    <SidebarHeader className="flex w-full flex-col gap-2 group-data-[collapsible=icon]:hidden border-b">
      {!readOnly && onBack && (
        <Button
          variant="primary"
          size="sm"
          className="flex w-full items-center justify-start !gap-1.5 shadow-sm"
          onClick={onBack}
          data-testid="sidebar-back-button"
        >
          <ForwardedIconComponent name="ArrowLeft" className="text-primary" />
          <span className="text-mmd font-normal">{t("Back")}</span>
        </Button>
      )}
      {!ENABLE_NEW_SIDEBAR && (
        <Disclosure open={showConfig} onOpenChange={setShowConfig}>
          <div className="flex w-full items-center gap-2">
            <SidebarTrigger className="text-muted-foreground">
              <ForwardedIconComponent name="PanelLeftClose" />
            </SidebarTrigger>
            <h3 className="flex-1 cursor-default text-sm font-semibold">
              {t("Components")}
            </h3>
            <DisclosureTrigger>
              
            </DisclosureTrigger>
          </div>
          <DisclosureContent>
            <FeatureToggles
              showBeta={showBeta}
              setShowBeta={setShowBeta}
              showLegacy={showLegacy}
              setShowLegacy={setShowLegacy}
            />
          </DisclosureContent>
        </Disclosure>
      )}
      <SearchInput
        searchInputRef={searchInputRef}
        isInputFocused={isInputFocused}
        search={search}
        handleInputFocus={handleInputFocus}
        handleInputBlur={handleInputBlur}
        handleInputChange={handleInputChange}
      />
      {filterName !== "" && filterDescription !== "" && (
        <SidebarFilterComponent
          name={filterName}
          description={filterDescription}
          resetFilters={resetFilters}
        />
      )}
      {ENABLE_NEW_SIDEBAR && (
        <Disclosure open={showConfig} onOpenChange={setShowConfig}>
          <DisclosureContent>
            <FeatureToggles
              showBeta={showBeta}
              setShowBeta={setShowBeta}
              showLegacy={showLegacy}
              setShowLegacy={setShowLegacy}
            />
          </DisclosureContent>
        </Disclosure>
      )}
    </SidebarHeader>
  );
});

SidebarHeaderComponent.displayName = "SidebarHeaderComponent";
