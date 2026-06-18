import { useContext, useState } from "react";
import { useTranslation } from "react-i18next";
import IconComponent from "@/components/common/genericIconComponent";
import { AuthContext } from "@/contexts/authContext";
import { ObservabilityDashboardSection } from "../DashboardPage/components/observabilityDashboardSection";

export default function ObservabilityPage() {
  const { t } = useTranslation();
  const { userData, role } = useContext(AuthContext);
  const normalizedRole = (role ?? "").toLowerCase().trim().replace(/\s+/g, "_");
  const isDepartmentAdmin = normalizedRole === "department_admin";
  const isRootAdmin = normalizedRole === "root";
  const isSuperAdmin = normalizedRole === "super_admin";
  const isLeaderExecutive = normalizedRole === "idp_auditor";

  const [refreshTick] = useState(0);

  return (
    <>
      {userData && (
        <div className="admin-page-panel flex h-full flex-col pb-8">
          <div className="main-page-nav-arrangement">
            <span className="main-page-nav-title">
              <IconComponent name="Activity" className="w-6 text-orange-600" />
              {t("Observability")}
            </span>
          </div>
          <span className="admin-page-description-text mb-6">
            {t("Monitor real-time LLM trace logs, execution latency, model cost structures, and token metrics across all workflow pipelines.")}
          </span>

          <div className="px-4">
            <ObservabilityDashboardSection
              isRootAdmin={isRootAdmin}
              isSuperAdmin={isSuperAdmin}
              isLeaderExecutive={isLeaderExecutive}
              isDepartmentAdmin={isDepartmentAdmin}
              userData={userData}
              refreshTick={refreshTick}
              accentColor="#D04A02"
              viewMode="tabs"
            />
          </div>
        </div>
      )}
    </>
  );
}
