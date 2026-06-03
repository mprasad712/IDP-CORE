import React from "react";
import ReactDOM from "react-dom/client";
import reportWebVitals from "./reportWebVitals";
import { I18nextProvider } from "react-i18next";
import i18n from "./i18n";

import "./style/classes.css";
// @ts-ignore
import "./style/index.css";
// @ts-ignore
import "./App.css";
import "./style/applies.css";

// @ts-ignore
import App from "./customization/custom-App";

import { PublicClientApplication, EventType } from "@azure/msal-browser";
import type { AuthenticationResult } from "@azure/msal-browser";
import { MsalProvider } from "@azure/msal-react";
import { msalConfig } from "./authConfig";

// MsalProvider must always be in the tree — useMsal() is called throughout the app
const msalInstance = new PublicClientApplication(msalConfig);

msalInstance.addEventCallback((event) => {
  if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
    const payload = event.payload as AuthenticationResult;
    if (payload.account) {
      msalInstance.setActiveAccount(payload.account);
    }
  }
});

const root = ReactDOM.createRoot(
  document.getElementById("root") as HTMLElement,
);

// Render React immediately — don't block on MSAL network calls
root.render(
  <React.StrictMode>
    <I18nextProvider i18n={i18n}>
      <MsalProvider instance={msalInstance}>
        <App />
      </MsalProvider>
    </I18nextProvider>
  </React.StrictMode>,
);

// Initialize MSAL in the background after React is already painting
const isMsalConfigured =
  !!msalConfig?.auth?.clientId && !!msalConfig?.auth?.authority;

if (isMsalConfigured) {
  msalInstance
    .initialize()
    .then(() => msalInstance.handleRedirectPromise())
    .then(() => {
      const accounts = msalInstance.getAllAccounts();
      if (!msalInstance.getActiveAccount() && accounts.length > 0) {
        msalInstance.setActiveAccount(accounts[0]);
      }
    })
    .catch((err) => {
      console.error("[MSAL] Initialization failed:", err);
    });
}

reportWebVitals();
