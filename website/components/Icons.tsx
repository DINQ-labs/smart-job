import type { FeatureId } from "@/lib/dictionary";

type IconProps = { className?: string };

const base = {
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

export function Logo({ className }: IconProps) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <defs>
        <linearGradient id="logo-g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#818cf8" />
          <stop offset="1" stopColor="#6366f1" />
        </linearGradient>
      </defs>
      <rect width="64" height="64" rx="14" fill="url(#logo-g)" />
      <path d="M20 26h24M20 34h24M20 42h14" stroke="#0a0e1a" strokeWidth="5" strokeLinecap="round" />
      <circle cx="44" cy="42" r="4" fill="#0a0e1a" />
    </svg>
  );
}

export function GithubIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48l-.01-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.99 1.03-2.69-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.03a9.5 9.5 0 0 1 5 0c1.91-1.3 2.75-1.03 2.75-1.03.55 1.38.2 2.4.1 2.65.64.7 1.03 1.6 1.03 2.69 0 3.84-2.34 4.69-4.57 4.94.36.31.68.92.68 1.85l-.01 2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2Z"
      />
    </svg>
  );
}

export function ArrowIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path {...base} d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

export function CheckIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <path {...base} d="M4 12.5l5 5 11-11" />
    </svg>
  );
}

export function GlobeIcon({ className }: IconProps) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <circle {...base} cx="12" cy="12" r="9" />
      <path {...base} d="M3 12h18M12 3c2.5 2.5 3.8 5.6 3.8 9S14.5 18.5 12 21c-2.5-2.5-3.8-5.6-3.8-9S9.5 5.5 12 3Z" />
    </svg>
  );
}

const featurePaths: Record<FeatureId, React.ReactNode> = {
  agent: <path {...base} d="M12 3a5 5 0 0 1 5 5v1a5 5 0 0 1-2 4v3H9v-3a5 5 0 0 1-2-4V8a5 5 0 0 1 5-5ZM9 21h6M10 12.5h.01M14 12.5h.01" />,
  multiplatform: (
    <>
      <rect {...base} x="3" y="4" width="18" height="13" rx="2" />
      <path {...base} d="M8 21h8M12 17v4M3 13h18" />
    </>
  ),
  capture: (
    <>
      <circle {...base} cx="11" cy="11" r="7" />
      <path {...base} d="M16 16l5 5M8 11h6M11 8v6" />
    </>
  ),
  autofill: (
    <>
      <rect {...base} x="4" y="3" width="16" height="18" rx="2" />
      <path {...base} d="M8 8h8M8 12h8M8 16h4" />
    </>
  ),
  automation: (
    <>
      <circle {...base} cx="12" cy="12" r="3" />
      <path {...base} d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
    </>
  ),
  admin: (
    <>
      <path {...base} d="M4 19V5M4 19h16" />
      <path {...base} d="M8 19v-6M13 19V8M18 19v-9" />
    </>
  ),
};

export function FeatureIcon({ id, className }: { id: FeatureId; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      {featurePaths[id]}
    </svg>
  );
}
