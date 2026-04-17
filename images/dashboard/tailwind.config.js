/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/templates/**/*.html"],
  darkTheme: "class",
  theme: {
    extend: {
      colors: {
        /* Shared SOC palette — used across all pages */
        socbg:    "#08111f",
        socpanel: "#0d1b2a",
        socline:  "#17324d",
        socblue:  "#38bdf8",
        socgreen: "#22c55e",
        socamber: "#f59e0b",
        socred:   "#ef4444",
        /* Legacy aliases kept for backward compat */
        dark:     "#1a1a2e",
        card:     "#16213e",
        danger:   "#ef4444",
        warning:  "#f59e0b",
        success:  "#22c55e",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [require("daisyui")],
  daisyui: {
    themes: [
      {
        iot: {
          "primary":   "#38bdf8",
          "secondary": "#8b5cf6",
          "accent":    "#22c55e",
          "neutral":   "#0d1b2a",
          "base-100":  "#08111f",
          "info":      "#38bdf8",
          "success":   "#22c55e",
          "warning":   "#f59e0b",
          "error":     "#ef4444",
        },
      },
    ],
    darkTheme: "iot",
  },
};
