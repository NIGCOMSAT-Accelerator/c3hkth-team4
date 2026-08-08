/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Palette drawn from the subject, not from a dashboard template:
        // tarmac (the road), laterite (Abuja's red earth), floodwater.
        tarmac: {
          950: "#0B0E11",
          900: "#11151A",
          850: "#161B21",
          800: "#1C232B",
          700: "#252E38",
          600: "#33404D",
          500: "#465563",
        },
        silt: "#5B6B7A",
        ash: "#8C9AA8",
        bone: "#E9E5DE",
        laterite: {
          600: "#8C3A22",
          500: "#B04A2C",
          400: "#CB6238",
          300: "#DF8B54",
        },
        flood: {
          600: "#2E5E5B",
          500: "#3E7C76",
          400: "#59A197",
        },
        signal: "#D9903F",
      },
      fontFamily: {
        // Monospace for every number. This is an instrument, and instruments
        // use tabular figures so digits do not dance as values update.
        data: ["ui-monospace", "SFMono-Regular", "SF Mono", "Menlo", "monospace"],
        ui: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "system-ui", "sans-serif"],
      },
      letterSpacing: { widest: "0.18em" },
    },
  },
  plugins: [],
};
