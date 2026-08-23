import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{js,ts,jsx,tsx}",
    "./components/**/*.{js,ts,jsx,tsx}",
    "./lib/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "var(--font-inter)",
          "ui-sans-serif",
          "system-ui",
          "Segoe UI",
          "Helvetica",
          "Arial",
          "sans-serif",
        ],
        mono: [
          "ui-monospace",
          "SFMono-Regular",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      colors: {
        ink: {
          950: "#08090b",
          900: "#0c0e12",
          850: "#11141a",
          800: "#171b22",
          700: "#1e242d",
          600: "#2a323d",
          500: "#3d4754",
        },
        mist: {
          100: "#e8eaed",
          200: "#c5ccd4",
          400: "#8b95a1",
          500: "#6b7582",
        },
        signal: {
          run: "#3dba7a",
          warn: "#d4a017",
          fail: "#d4534c",
          info: "#5b8def",
          idle: "#6b7582",
        },
      },
      boxShadow: {
        panel: "0 0 0 1px #252b34",
      },
    },
  },
  plugins: [],
};

export default config;
