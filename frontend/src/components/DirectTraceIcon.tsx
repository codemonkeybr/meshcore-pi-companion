interface DirectTraceIconProps {
  className?: string;
}

export function DirectTraceIcon({ className }: DirectTraceIconProps) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
    >
      <path d="M3 12h12" />
      <circle cx="18" cy="12" r="3" />
    </svg>
  );
}
