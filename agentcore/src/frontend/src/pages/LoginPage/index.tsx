import * as Form from "@radix-ui/react-form";
import { useContext, useState } from "react";
import { useLoginUser } from "@/controllers/API/queries/auth";
import { useRegisterUser } from "@/controllers/API/queries/auth";
import { Button } from "../../components/ui/button";
import { SIGNIN_ERROR_ALERT } from "../../constants/alerts_constants";
import { CONTROL_LOGIN_STATE } from "../../constants/constants";
import { AuthContext } from "../../contexts/authContext";
import useAlertStore from "../../stores/alertStore";
import type { LoginType } from "../../types/api";
import type { inputHandlerEventType, loginInputStateType } from "../../types/components";
import { useMsal } from "@azure/msal-react";
import { loginRequest, msalConfig } from "@/authConfig";
import { useTranslation } from "react-i18next";
import useAuthStore from "@/stores/authStore";
import useTheme from "@/customization/hooks/use-custom-theme";
import MothersonLogo from "@/assets/MiCore.svg";

/* PwC brand tokens */
const PwC = {
  orange:     "#D04A02",
  orangeHover:"#B84002",
  orangeLight:"#F5E6DE",
  charcoal:   "#2D2D2D",
  darkBg:     "#1C1B1F",
  cardDark:   "#26252A",
};

/* ── Document-stack watermark ── */
function DocWatermark() {
  return (
    <svg
      viewBox="0 0 120 140"
      fill="none"
      stroke="white"
      strokeWidth="1.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="absolute right-[-30px] bottom-[-20px] w-[380px] h-[380px] opacity-[0.06] pointer-events-none select-none"
      aria-hidden="true"
    >
      <rect x="18" y="10" width="80" height="104" rx="3" />
      <rect x="12" y="16" width="80" height="104" rx="3" />
      <rect x="6" y="22" width="80" height="104" rx="3" />
      <path d="M62 22 L86 22 L86 46 Z" />
      <line x1="18" y1="56" x2="70" y2="56" />
      <line x1="18" y1="68" x2="70" y2="68" />
      <line x1="18" y1="80" x2="70" y2="80" />
      <line x1="18" y1="92" x2="52" y2="92" />
      <line x1="6"  y1="86" x2="86" y2="86" strokeWidth="2" strokeDasharray="4 3" stroke="#D04A02" />
    </svg>
  );
}

/* ── Subtle ruled-line paper pattern ── */
function PaperPattern() {
  return (
    <svg
      className="absolute inset-0 w-full h-full opacity-[0.055]"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <pattern id="ruled" x="0" y="0" width="100%" height="36" patternUnits="userSpaceOnUse">
          <line x1="0" y1="35" x2="100%" y2="35" stroke="white" strokeWidth="0.6" />
        </pattern>
      </defs>
      <rect width="100%" height="100%" fill="url(#ruled)" />
    </svg>
  );
}

const inputClass =
  "w-full px-4 py-2.5 text-sm rounded-md transition-all " +
  "border border-slate-200 dark:border-slate-600 " +
  "bg-white dark:bg-slate-800/60 " +
  "text-slate-900 dark:text-white " +
  "placeholder-slate-400 dark:placeholder-slate-500 " +
  "focus:outline-none focus:border-[#D04A02] focus:ring-2 focus:ring-[#D04A02]/20";

type ActiveTab = "signin" | "register" | "check-email";

export default function LoginPage(): JSX.Element {
  const [inputState, setInputState] = useState<loginInputStateType>(CONTROL_LOGIN_STATE);
  const [showPassword, setShowPassword] = useState(false);
  const [showRegPassword, setShowRegPassword] = useState(false);
  const [isSigningIn, setIsSigningIn] = useState(false);
  const [activeTab, setActiveTab] = useState<ActiveTab>("signin");

  const [regUsername, setRegUsername] = useState("");
  const [regEmail, setRegEmail] = useState("");
  const [regPassword, setRegPassword] = useState("");
  const [regConfirm, setRegConfirm] = useState("");
  const [regError, setRegError] = useState("");
  const [isRegistering, setIsRegistering] = useState(false);

  const { password, username } = inputState;
  const { login } = useContext(AuthContext);
  const setErrorData = useAlertStore((s) => s.setErrorData);
  const { instance } = useMsal();
  const { t } = useTranslation();
  const setAuthContext = useAuthStore((s) => s.setAuthContext);
  const setIsAuthenticated = useAuthStore((s) => s.setIsAuthenticated);
  const { mutate: loginMutate } = useLoginUser();
  const { mutate: registerMutate } = useRegisterUser();

  useTheme();

  function handleInput({ target: { name, value } }: inputHandlerEventType) {
    setInputState((p) => ({ ...p, [name]: value }));
  }

  async function handleAzureSSO() {
    try {
      const { clientId, authority, redirectUri } = msalConfig?.auth ?? {};
      const validUrl = (v?: string) => {
        try { const u = new URL(v ?? ""); return u.protocol === "http:" || u.protocol === "https:"; }
        catch { return false; }
      };
      if (!clientId || !validUrl(authority) || !validUrl(redirectUri)) {
        setErrorData({ title: "Microsoft SSO configuration is invalid", list: ["Check AZURE_CLIENT_ID, AZURE_TENANT_ID, and MSAL_REDIRECT_URI in .env."] });
        return;
      }
      let response;
      try { response = await instance.loginPopup(loginRequest); }
      catch (e: any) {
        if (e?.errorCode === "popup_window_error" || e?.errorCode === "empty_window_error") {
          await instance.loginRedirect(loginRequest); return;
        }
        throw e;
      }
      const res = await fetch("/api/azure/sso", {
        method: "POST", credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken: response.idToken }),
      });
      if (!res.ok) {
        let detail = "Backend SSO failed";
        try { const p = await res.json(); if (p?.detail) detail = p.detail; }
        catch { const txt = await res.text(); if (txt) detail = txt; }
        setErrorData({ title: SIGNIN_ERROR_ALERT, list: [detail] }); return;
      }
      const data = await res.json();
      login(data.access_token, data.role, data.permissions, data.refresh_token);
    } catch {
      setErrorData({ title: SIGNIN_ERROR_ALERT, list: ["Microsoft SSO failed. Please try again or contact your admin."] });
    }
  }

  function signIn() {
    if (!username || !password) return;
    setIsSigningIn(true);
    loginMutate({ username: username.trim(), password: password.trim() } as LoginType, {
      onSuccess: (data) => {
        login(data.access_token, data.role, data.permissions, data.refresh_token);
        setAuthContext({ role: data.role, permissions: data.permissions });
        setIsAuthenticated(true);
      },
      onError: (error) => {
        setIsSigningIn(false);
        setErrorData({ title: SIGNIN_ERROR_ALERT, list: [error["response"]["data"]["detail"]] });
      },
    });
  }

  function handleRegister(e: React.FormEvent) {
    e.preventDefault();
    setRegError("");

    if (!regUsername.trim() || !regEmail.trim() || !regPassword) {
      setRegError("All fields are required.");
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(regEmail.trim())) {
      setRegError("Please enter a valid email address.");
      return;
    }
    if (regPassword.length < 8) {
      setRegError("Password must be at least 8 characters.");
      return;
    }
    if (regPassword !== regConfirm) {
      setRegError("Passwords do not match.");
      return;
    }

    setIsRegistering(true);
    registerMutate(
      { username: regUsername.trim(), email: regEmail.trim().toLowerCase(), password: regPassword },
      {
        onSuccess: () => {
          setIsRegistering(false);
          setActiveTab("check-email");
        },
        onError: (error: any) => {
          setIsRegistering(false);
          const detail = error?.response?.data?.detail ?? "Registration failed. Please try again.";
          setRegError(detail);
        },
      },
    );
  }

  return (
    <div className="min-h-screen flex">

      {/* LEFT brand panel */}
      <div
        className="hidden lg:flex lg:w-[46%] xl:w-5/12 relative overflow-hidden flex-col"
        style={{ background: `linear-gradient(160deg, ${PwC.charcoal} 0%, #1a1a1f 60%, #111115 100%)` }}
      >
        <PaperPattern />
        <DocWatermark />
        <div className="absolute top-0 left-0 right-0 h-1" style={{ background: PwC.orange }} />
        <div
          className="absolute bottom-0 right-0 w-72 h-72 rounded-full blur-[100px] pointer-events-none opacity-30"
          style={{ background: PwC.orange }}
        />
        <div className="relative z-10 flex flex-col h-full px-10 xl:px-14 py-12 justify-between">
          <img src={MothersonLogo} alt="Digital Workmate" className="h-9 w-auto brightness-0 invert" />
          <div className="max-w-[300px]">
            <div
              className="inline-flex items-center gap-2 px-3 py-1.5 rounded-sm mb-6"
              style={{ background: `${PwC.orange}22`, border: `1px solid ${PwC.orange}55` }}
            >
              <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke={PwC.orange} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                <polyline points="14 2 14 8 20 8" />
                <line x1="16" y1="13" x2="8" y2="13" />
                <line x1="16" y1="17" x2="8" y2="17" />
                <polyline points="10 9 9 9 8 9" />
              </svg>
              <span className="text-[10px] font-bold tracking-[0.12em] uppercase" style={{ color: PwC.orange }}>
                Intelligent Document Processing
              </span>
            </div>
            <h1 className="text-[2rem] xl:text-[2.25rem] font-bold text-white leading-tight mb-3">
              {t("Extract. Classify.")}<br />
              <span style={{ color: PwC.orange }}>{t("Transform.")}</span>
            </h1>
            <p className="text-sm text-white/45 leading-relaxed">
              {t("AI-powered document intelligence that reads, understands, and processes your documents at enterprise scale.")}
            </p>
            <ul className="mt-7 space-y-3">
              {[
                t("Automated data extraction & classification"),
                t("Multi-format document support"),
                t("End-to-end audit trail & compliance"),
              ].map((item) => (
                <li key={item} className="flex items-start gap-3 text-sm text-white/50">
                  <span
                    className="mt-0.5 w-4 h-4 rounded-sm flex items-center justify-center flex-shrink-0"
                    style={{ background: `${PwC.orange}20`, border: `1px solid ${PwC.orange}40` }}
                  >
                    <svg className="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke={PwC.orange} strokeWidth={3} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M4.5 12.75l6 6 9-13.5" />
                    </svg>
                  </span>
                  {item}
                </li>
              ))}
            </ul>
          </div>
          <p className="text-[11px] text-white/20">
            © {new Date().getFullYear()} Digital Workmate · Powered by PwC IDP
          </p>
        </div>
      </div>

      {/* RIGHT form panel */}
      <div className="flex-1 flex items-center justify-center px-6 sm:px-10 py-12 bg-[#F7F7F7] dark:bg-[#111115]">
        <div className="w-full max-w-[400px]">

          {/* Mobile logo */}
          <div className="lg:hidden flex justify-center mb-8">
            <img src={MothersonLogo} alt="Digital Workmate" className="h-8 w-auto dark:invert" />
          </div>

          {/* Card */}
          <div
            className="bg-white dark:bg-[#1e1e24] rounded-lg shadow-xl dark:shadow-black/60 overflow-hidden"
            style={{ border: "1px solid #e5e5e5" }}
          >
            <div className="h-1 w-full" style={{ background: PwC.orange }} />

            {/* ── Check-email success state ── */}
            {activeTab === "check-email" && (
              <div className="px-8 py-10 text-center">
                <div
                  className="mx-auto mb-5 w-14 h-14 rounded-full flex items-center justify-center"
                  style={{ background: `${PwC.orange}18` }}
                >
                  <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke={PwC.orange}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M21.75 6.75v10.5a2.25 2.25 0 01-2.25 2.25h-15a2.25 2.25 0 01-2.25-2.25V6.75m19.5 0A2.25 2.25 0 0019.5 4.5h-15a2.25 2.25 0 00-2.25 2.25m19.5 0v.243a2.25 2.25 0 01-1.07 1.916l-7.5 4.615a2.25 2.25 0 01-2.36 0L3.32 8.91a2.25 2.25 0 01-1.07-1.916V6.75" />
                  </svg>
                </div>
                <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-2">
                  {t("Check your email")}
                </h2>
                <p className="text-sm text-slate-500 dark:text-slate-400 leading-relaxed mb-6">
                  {t("We've sent a verification link to your email address. Click the link to activate your account.")}
                </p>
                <button
                  type="button"
                  onClick={() => setActiveTab("signin")}
                  className="text-sm font-medium transition-colors"
                  style={{ color: PwC.orange }}
                >
                  {t("Back to Sign In")}
                </button>
              </div>
            )}

            {/* ── Sign In / Register tabs ── */}
            {activeTab !== "check-email" && (
              <div className="px-8 py-8">

                {/* Tab switcher */}
                <div className="flex border-b border-slate-200 dark:border-slate-700 mb-6">
                  {(["signin", "register"] as const).map((tab) => (
                    <button
                      key={tab}
                      type="button"
                      onClick={() => { setActiveTab(tab); setRegError(""); }}
                      className="flex-1 pb-2.5 text-sm font-semibold transition-colors"
                      style={
                        activeTab === tab
                          ? { color: PwC.orange, borderBottom: `2px solid ${PwC.orange}` }
                          : { color: "#94a3b8", borderBottom: "2px solid transparent" }
                      }
                    >
                      {tab === "signin" ? t("Sign In") : t("Register")}
                    </button>
                  ))}
                </div>

                {/* Header */}
                <div className="mb-6">
                  <div className="flex items-center gap-2 mb-2">
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke={PwC.orange} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                      <polyline points="14 2 14 8 20 8" />
                      <line x1="16" y1="13" x2="8" y2="13" />
                      <line x1="16" y1="17" x2="8" y2="17" />
                    </svg>
                    <span className="text-xs font-semibold tracking-widest uppercase text-slate-400 dark:text-slate-500">
                      Digital Workmate IDP
                    </span>
                  </div>
                  <h2 className="text-xl font-bold text-slate-900 dark:text-white">
                    {activeTab === "signin" ? t("Sign in to your account") : t("Create an account")}
                  </h2>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    {activeTab === "signin"
                      ? t("Access the Intelligent Document Processing platform")
                      : t("Register to access the IDP platform")}
                  </p>
                </div>

                {/* ── Sign In Form ── */}
                {activeTab === "signin" && (
                  <>
                    <Form.Root onSubmit={(e) => { e.preventDefault(); signIn(); }} className="space-y-5">
                      <Form.Field name="username" className="space-y-1.5">
                        <Form.Label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                          {t("Username or Email")}
                        </Form.Label>
                        <Form.Control asChild>
                          <input
                            type="text"
                            name="username"
                            value={username}
                            onChange={handleInput}
                            placeholder={t("Enter your username or email")}
                            autoComplete="username"
                            className={inputClass}
                          />
                        </Form.Control>
                      </Form.Field>

                      <Form.Field name="password" className="space-y-1.5">
                        <Form.Label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                          {t("Password")}
                        </Form.Label>
                        <div className="relative">
                          <Form.Control asChild>
                            <input
                              type={showPassword ? "text" : "password"}
                              name="password"
                              value={password}
                              onChange={handleInput}
                              placeholder={t("Enter your password")}
                              autoComplete="current-password"
                              className={inputClass + " pr-10"}
                            />
                          </Form.Control>
                          <button
                            type="button"
                            tabIndex={-1}
                            onClick={() => setShowPassword((v) => !v)}
                            className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                          >
                            {showPassword ? (
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                              </svg>
                            ) : (
                              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                                <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                              </svg>
                            )}
                          </button>
                        </div>
                      </Form.Field>

                      <button
                        type="submit"
                        disabled={!username || !password || isSigningIn}
                        className="w-full h-10 rounded-md text-sm font-semibold text-white transition-all shadow-sm mt-1 disabled:opacity-40 disabled:cursor-not-allowed"
                        style={{
                          background: (!username || !password || isSigningIn) ? `${PwC.orange}66` : PwC.orange,
                        }}
                        onMouseEnter={(e) => { if (username && password && !isSigningIn) (e.currentTarget as HTMLButtonElement).style.background = PwC.orangeHover; }}
                        onMouseLeave={(e) => { if (username && password && !isSigningIn) (e.currentTarget as HTMLButtonElement).style.background = PwC.orange; }}
                      >
                        {isSigningIn ? t("Signing in…") : t("Sign In")}
                      </button>
                    </Form.Root>

                    <div className="relative my-5">
                      <div className="absolute inset-0 flex items-center">
                        <div className="w-full border-t border-slate-200 dark:border-slate-700" />
                      </div>
                      <div className="relative flex justify-center">
                        <span className="px-3 text-xs text-slate-400 dark:text-slate-500 bg-white dark:bg-[#1e1e24]">
                          {t("or sign in with")}
                        </span>
                      </div>
                    </div>

                    <button
                      type="button"
                      onClick={handleAzureSSO}
                      className="w-full flex items-center justify-center gap-3 h-10 px-4 rounded-md
                                 border border-slate-200 dark:border-slate-600
                                 bg-white dark:bg-slate-800/50
                                 text-slate-700 dark:text-slate-200 text-sm font-medium
                                 hover:bg-slate-50 dark:hover:bg-slate-800
                                 hover:border-slate-300 dark:hover:border-slate-500
                                 transition-all"
                    >
                      <svg className="w-4 h-4 flex-shrink-0" viewBox="0 0 23 23">
                        <path fill="#f25022" d="M1 1h10v10H1z" />
                        <path fill="#7fba00" d="M12 1h10v10H12z" />
                        <path fill="#00a4ef" d="M1 12h10v10H1z" />
                        <path fill="#ffb900" d="M12 12h10v10H12z" />
                      </svg>
                      {t("Continue with Microsoft SSO")}
                    </button>
                  </>
                )}

                {/* ── Register Form ── */}
                {activeTab === "register" && (
                  <form onSubmit={handleRegister} className="space-y-4">
                    <div className="space-y-1.5">
                      <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        {t("Username")}
                      </label>
                      <input
                        type="text"
                        value={regUsername}
                        onChange={(e) => setRegUsername(e.target.value)}
                        placeholder={t("Choose a username")}
                        autoComplete="username"
                        className={inputClass}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        {t("Email")}
                      </label>
                      <input
                        type="email"
                        value={regEmail}
                        onChange={(e) => setRegEmail(e.target.value)}
                        placeholder={t("Enter your email")}
                        autoComplete="email"
                        className={inputClass}
                      />
                    </div>

                    <div className="space-y-1.5">
                      <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        {t("Password")}
                      </label>
                      <div className="relative">
                        <input
                          type={showRegPassword ? "text" : "password"}
                          value={regPassword}
                          onChange={(e) => setRegPassword(e.target.value)}
                          placeholder={t("Min. 8 characters")}
                          autoComplete="new-password"
                          className={inputClass + " pr-10"}
                        />
                        <button
                          type="button"
                          tabIndex={-1}
                          onClick={() => setShowRegPassword((v) => !v)}
                          className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                        >
                          {showRegPassword ? (
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
                            </svg>
                          ) : (
                            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
                              <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
                              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                          )}
                        </button>
                      </div>
                    </div>

                    <div className="space-y-1.5">
                      <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                        {t("Confirm Password")}
                      </label>
                      <input
                        type="password"
                        value={regConfirm}
                        onChange={(e) => setRegConfirm(e.target.value)}
                        placeholder={t("Re-enter your password")}
                        autoComplete="new-password"
                        className={inputClass}
                      />
                    </div>

                    {regError && (
                      <p className="text-xs text-red-500 dark:text-red-400 py-1">{regError}</p>
                    )}

                    <button
                      type="submit"
                      disabled={isRegistering}
                      className="w-full h-10 rounded-md text-sm font-semibold text-white transition-all shadow-sm mt-1 disabled:opacity-40 disabled:cursor-not-allowed"
                      style={{ background: isRegistering ? `${PwC.orange}66` : PwC.orange }}
                      onMouseEnter={(e) => { if (!isRegistering) (e.currentTarget as HTMLButtonElement).style.background = PwC.orangeHover; }}
                      onMouseLeave={(e) => { if (!isRegistering) (e.currentTarget as HTMLButtonElement).style.background = PwC.orange; }}
                    >
                      {isRegistering ? t("Creating account…") : t("Create Account")}
                    </button>
                  </form>
                )}
              </div>
            )}
          </div>

          {/* Terms */}
          <p className="mt-5 text-center text-xs text-slate-400 dark:text-slate-600">
            {t("By signing in, you agree to our")}{" "}
            <span className="underline underline-offset-2 cursor-pointer hover:text-slate-600 dark:hover:text-slate-400 transition-colors">
              {t("Terms of Service")}
            </span>{" "}
            {t("and")}{" "}
            <span className="underline underline-offset-2 cursor-pointer hover:text-slate-600 dark:hover:text-slate-400 transition-colors">
              {t("Privacy Policy")}
            </span>
          </p>
        </div>
      </div>
    </div>
  );
}
