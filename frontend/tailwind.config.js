/** @type {import('tailwindcss').Config} */
export default { content: ['./index.html', './src/**/*.{ts,tsx}'], theme: { extend: { fontFamily: { sans: ['Inter', 'ui-sans-serif', 'system-ui'] }, colors: { ink: '#f4f7fb', panel: '#ffffff', line: '#dfe7f0', telegram: '#168acd' }, boxShadow: { panel: '0 1px 2px rgba(20, 45, 75, .04), 0 12px 30px rgba(35, 72, 110, .06)' } } }, plugins: [] }
