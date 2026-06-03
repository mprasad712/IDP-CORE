import { useState, useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "@/controllers/API/api";
import { getURL } from "@/controllers/API/helpers/constants";
import MothersonLogo from "@/assets/micore.svg";

const PwC = {
  orange: "#D04A02",
  orangeHover: "#B84002",
};

const inputClass =
  "w-full px-4 py-2.5 text-sm rounded-md transition-all " +
  "border border-slate-200 dark:border-slate-600 " +
  "bg-white dark:bg-slate-800/60 " +
  "text-slate-900 dark:text-white " +
  "placeholder-slate-400 dark:placeholder-slate-500 " +
  "focus:outline-none focus:border-[#D04A02] focus:ring-2 focus:ring-[#D04A02]/20";

export default function SetPasswordPage(): JSX.Element {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const token = searchParams.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) {
      setError("Invalid or missing token. Please use the link from your email.");
    }
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError("");

    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match.");
      return;
    }

    setIsSubmitting(true);
    try {
      await api.post(`${getURL("SET_PASSWORD")}`, { token, password });
      setDone(true);
      setTimeout(() => navigate("/login"), 3000);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? "Failed to set password. Please request a new link.";
      setError(detail);
    } finally {
      setIsSubmitting(false);
    }
  }

  function EyeIcon({ open }: { open: boolean }) {
    return open ? (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.98 8.223A10.477 10.477 0 001.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.45 10.45 0 0112 4.5c4.756 0 8.773 3.162 10.065 7.498a10.523 10.523 0 01-4.293 5.774M6.228 6.228L3 3m3.228 3.228l3.65 3.65m7.894 7.894L21 21m-3.228-3.228l-3.65-3.65m0 0a3 3 0 10-4.243-4.243m4.242 4.242L9.88 9.88" />
      </svg>
    ) : (
      <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
        <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
      </svg>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center px-6 bg-[#F7F7F7] dark:bg-[#111115]">
      <div className="w-full max-w-[400px]">
        <div className="flex justify-center mb-8">
          <img src={MothersonLogo} alt="MiCore" className="h-9 w-auto dark:invert" />
        </div>

        <div
          className="bg-white dark:bg-[#1e1e24] rounded-lg shadow-xl dark:shadow-black/60 overflow-hidden"
          style={{ border: "1px solid #e5e5e5" }}
        >
          <div className="h-1 w-full" style={{ background: PwC.orange }} />

          <div className="px-8 py-8">
            {done ? (
              <div className="text-center py-4">
                <div
                  className="mx-auto mb-5 w-14 h-14 rounded-full flex items-center justify-center"
                  style={{ background: `${PwC.orange}18` }}
                >
                  <svg className="w-7 h-7" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke={PwC.orange}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
                  </svg>
                </div>
                <h2 className="text-lg font-bold text-slate-900 dark:text-white mb-2">Password set!</h2>
                <p className="text-sm text-slate-500 dark:text-slate-400">
                  Redirecting to sign in…
                </p>
              </div>
            ) : (
              <>
                <div className="mb-6">
                  <div className="flex items-center gap-2 mb-2">
                    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke={PwC.orange} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
                    </svg>
                    <span className="text-xs font-semibold tracking-widest uppercase text-slate-400 dark:text-slate-500">
                      MiCore IDP
                    </span>
                  </div>
                  <h2 className="text-xl font-bold text-slate-900 dark:text-white">Set your password</h2>
                  <p className="text-xs text-slate-500 dark:text-slate-400 mt-1">
                    Choose a password to activate your account.
                  </p>
                </div>

                <form onSubmit={handleSubmit} className="space-y-4">
                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                      New Password
                    </label>
                    <div className="relative">
                      <input
                        type={showPassword ? "text" : "password"}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="Min. 8 characters"
                        autoComplete="new-password"
                        className={inputClass + " pr-10"}
                      />
                      <button
                        type="button"
                        tabIndex={-1}
                        onClick={() => setShowPassword((v) => !v)}
                        className="absolute inset-y-0 right-0 px-3 flex items-center text-slate-400 hover:text-slate-600 dark:hover:text-slate-300 transition-colors"
                      >
                        <EyeIcon open={showPassword} />
                      </button>
                    </div>
                  </div>

                  <div className="space-y-1.5">
                    <label className="block text-xs font-semibold text-slate-500 dark:text-slate-400 uppercase tracking-wider">
                      Confirm Password
                    </label>
                    <input
                      type="password"
                      value={confirm}
                      onChange={(e) => setConfirm(e.target.value)}
                      placeholder="Re-enter your password"
                      autoComplete="new-password"
                      className={inputClass}
                    />
                  </div>

                  {error && (
                    <p className="text-xs text-red-500 dark:text-red-400 py-1">{error}</p>
                  )}

                  <button
                    type="submit"
                    disabled={isSubmitting || !token}
                    className="w-full h-10 rounded-md text-sm font-semibold text-white transition-all shadow-sm mt-1 disabled:opacity-40 disabled:cursor-not-allowed"
                    style={{ background: isSubmitting || !token ? `${PwC.orange}66` : PwC.orange }}
                    onMouseEnter={(e) => { if (!isSubmitting && token) (e.currentTarget as HTMLButtonElement).style.background = PwC.orangeHover; }}
                    onMouseLeave={(e) => { if (!isSubmitting && token) (e.currentTarget as HTMLButtonElement).style.background = PwC.orange; }}
                  >
                    {isSubmitting ? "Setting password…" : "Set Password"}
                  </button>
                </form>
              </>
            )}
          </div>
        </div>

        <p className="mt-5 text-center text-xs text-slate-400 dark:text-slate-600">
          Already have a password?{" "}
          <button
            type="button"
            onClick={() => navigate("/login")}
            className="underline underline-offset-2 cursor-pointer hover:text-slate-600 dark:hover:text-slate-400 transition-colors"
          >
            Sign in
          </button>
        </p>
      </div>
    </div>
  );
}
