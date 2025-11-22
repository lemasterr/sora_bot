import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      fontFamily: {
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
        sans: ['Inter', 'ui-sans-serif', 'system-ui'],
      },
      colors: {
        zinc: {
          950: '#09090b',
          900: '#18181b',
        },
        neon: {
          blue: '#3b82f6',
          emerald: '#10b981',
          red: '#ef4444',
        },
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(59, 130, 246, 0.4), 0 0 25px rgba(59, 130, 246, 0.15)',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
};

export default config;
