export function CustomLoadingPage() {
  return (
    <div className="fixed inset-0 flex items-center justify-center bg-background">
      <svg
        className="h-10 w-10 animate-spin"
        viewBox="0 0 24 24"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" className="opacity-20" />
        <path
          d="M12 2a10 10 0 0 1 10 10"
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
          className="text-primary"
        />
      </svg>
    </div>
  );
}
