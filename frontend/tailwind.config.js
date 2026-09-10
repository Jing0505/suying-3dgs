/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        // 黑金主色调（新配色方案）
        gold: {
          50: "#fbf6e6",
          100: "#f5ebc4",
          200: "#ebd88c",
          300: "#e0c455",
          400: "#d4a017", // 金色强调 #D4A017
          500: "#b8871a",
          600: "#946d1c",
          700: "#7a5719",
          800: "#654819",
          900: "#573d1a",
        },
        ink: {
          50: "#1f1f1f",
          100: "#1c1c1c",
          200: "#1a1a1a",
          300: "#191919",
          400: "#181818",
          500: "#171717", // 主背景 #171717
          600: "#141414",
          700: "#111111",
          800: "#0d0d0d",
          900: "#0a0a0a",
        },
        // 青铜辅助色
        bronze: {
          300: "#a88556",
          400: "#8c6b3e", // #8C6B3E
          500: "#6f5430",
          600: "#584226",
        },
        // 科技蓝点缀（仅用于实时/数据流语义指示）
        tech: {
          300: "#7dd3fc",
          400: "#38bdf8", // #38BDF8
          500: "#0ea5e9",
          600: "#0284c7",
        },
        // 暖白文字
        warm: {
          DEFAULT: "#fff7e6", // #FFF7E6
          200: "#fff7e6",
          400: "#e6d9b8",
          600: "#b8a888",
        },
      },
      fontFamily: {
        sans: [
          "Geist",
          "PingFang SC",
          "Microsoft YaHei",
          "system-ui",
          "sans-serif",
        ],
        mono: [
          "JetBrains Mono",
          "Geist Mono",
          "Menlo",
          "Consolas",
          "monospace",
        ],
      },
      boxShadow: {
        // brutalist: 仅保留极克制、无虚化的硬阴影
        gold: "0 0 0 1px rgba(212, 175, 55, 0.25)",
        "gold-lg": "0 0 0 1px rgba(212, 175, 55, 0.45)",
        "ink": "0 0 0 1px rgba(255, 255, 255, 0.04)",
      },
      backgroundImage: {
        "gold-gradient":
          "linear-gradient(135deg, #d4af37 0%, #f0d264 50%, #c9a227 100%)",
        "dark-gradient":
          "linear-gradient(180deg, #0a0a0a 0%, #121212 50%, #0a0a0a 100%)",
        "gold-radial":
          "radial-gradient(circle at 50% 0%, rgba(212,175,55,0.15) 0%, transparent 60%)",
      },
      animation: {
        "pulse-gold": "pulseGold 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        "spin-slow": "spin 3s linear infinite",
        shimmer: "shimmer 2s linear infinite",
      },
      keyframes: {
        pulseGold: {
          "0%, 100%": { opacity: "1", boxShadow: "0 0 20px rgba(212,175,55,0.3)" },
          "50%": { opacity: "0.7", boxShadow: "0 0 30px rgba(212,175,55,0.5)" },
        },
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
      },
    },
  },
  plugins: [],
};
