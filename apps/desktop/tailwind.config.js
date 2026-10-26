export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: { extend: {
    colors: { bg: "#0B1120", surface: "#151F32", line: "#26334d", primary: "#3B82F6", primary2: "#60A5FA",
              ink: "#F8FAFC", mute: "#94A3B8", ok: "#22C55E", warn: "#F59E0B", err: "#EF4444" },
    fontFamily: { sans: ["Inter", "system-ui", "sans-serif"] } } },
};
