import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./content/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        ink: {
          900: "#0a0e1a",
          800: "#0f1525",
          700: "#161d33",
          600: "#1f2942",
        },
        accent: {
          DEFAULT: "#6366f1",
          soft: "#818cf8",
        },
      },
      fontFamily: {
        sans: ["ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
      boxShadow: {
        glow: "0 10px 40px -10px rgba(99, 102, 241, 0.55)",
      },
    },
  },
  plugins: [],
};

export default config;
