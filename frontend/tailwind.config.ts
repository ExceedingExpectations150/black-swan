import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ["var(--font-display)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      colors: {
        panel: "var(--panel)",
        hair: "var(--panel-border)",
        ink: "var(--text)",
        ink1: "var(--text)",
        ink2: "var(--text-2)",
        ink3: "var(--text-3)",
        up: "var(--up)",
        down: "var(--down)",
        accent: "var(--accent)",
        warn: "var(--warn)",
      },
    },
  },
  plugins: [],
};
export default config;
