import { memo } from "react";

import { ForwardedIconComponent } from "@/components/common/genericIconComponent";
import { Button } from "@/components/ui/button";
import {
  Disclosure,
} from "@/components/ui/disclosure";
import { SidebarHeader, SidebarTrigger } from "@/components/ui/sidebar";
import type { SidebarHeaderComponentProps } from "../../types";
import { SearchInput } from "../searchInput";
import { SidebarFilterComponent } from "../sidebarFilterComponent";
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
    <SidebarHeader className="flex w-full flex-col gap-3 p-4 pb-2">
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
      <Disclosure open={showConfig} onOpenChange={setShowConfig}>
        <div className="flex w-full items-center gap-2">
          <SidebarTrigger className="text-muted-foreground">
            <ForwardedIconComponent name="PanelLeftClose" />
          </SidebarTrigger>
          <h3 className="flex-1 cursor-default text-sm font-semibold">
            {t("Components")}
          </h3>
        </div>
      </Disclosure>
      <SearchInput
        searchInputRef={searchInputRef}
        isInputFocused={isInputFocused}
        search={search}
        handleInputFocus={handleInputFocus}
        handleInputBlur={handleInputBlur}
        handleInputChange={handleInputChange}
      />
      {filterName && filterDescription && (
        <SidebarFilterComponent
          name={filterName}
          description={filterDescription}
          resetFilters={resetFilters}
        />
      )}
    </SidebarHeader>
  );
});

SidebarHeaderComponent.displayName = "SidebarHeaderComponent";
