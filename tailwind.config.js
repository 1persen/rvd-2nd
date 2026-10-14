// Production Tailwind build (scripts/build_css.sh). Keep `theme` in sync with the
// inline CDN config in app/templates/base.html, which is still used in dev.
module.exports = {
  darkMode: 'class',
  content: [
    './app/templates/**/*.html',
    './app/static/js/**/!(alpine*).js',
  ],
  safelist: [
    'backdrop-blur-xl',
    'bg-white/70',
    'bg-navy-800/70',
    'border-gray-200/70',
    'dark:border-white/[0.08]',
    'shadow-gray-900/[0.06]',
    'dark:shadow-black/30',
  ],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: '#0B1D3A', 800: '#122A52', 700: '#173568' },
        brand: { DEFAULT: '#1D4ED8', bright: '#173568', 50: '#EEF3FF' },
        medal: { gold: '#D4AF37', silver: '#94A3B8', bronze: '#B26339' },
      },
      fontFamily: {
        display: ['"Plus Jakarta Sans"', 'sans-serif'],
        score: ['"Plus Jakarta Sans"', 'sans-serif'],
        body: ['Inter', 'sans-serif'],
      },
    },
  },
};
