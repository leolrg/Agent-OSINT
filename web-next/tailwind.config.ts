import type { Config } from 'tailwindcss';

export default {
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // Brutalist E palette
        cream: '#f5f4f1',
        ink: '#0a0a0a',
        muted: '#525252',
        muted2: '#737373',
        border: '#d4d3d0',
        dashed: '#b4b3b0',
        accent: '#c2410c',     // running orange
        spotlight: '#facc15',  // yellow on inverted blocks
        amber: '#fef3c7',
        amber2: '#a16207',
        danger: '#7f1d1d',
        sidebar: '#efeae2',    // selected scan row
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      letterSpacing: {
        widest2: '0.18em',
      },
    },
  },
  plugins: [],
} satisfies Config;
