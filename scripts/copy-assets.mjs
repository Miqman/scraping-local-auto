// Dev-only: salin aset UI lokal (S5 — nol request eksternal saat runtime).
import { copyFileSync, mkdirSync, existsSync, readdirSync } from 'node:fs';

mkdirSync('static/fonts', { recursive: true });

const fonts = [
  ['@fontsource/inter', 'inter', [400, 500, 600, 700]],
  ['@fontsource/jetbrains-mono', 'jetbrains-mono', [400, 500, 600, 700]],
];
for (const [pkg, name, weights] of fonts) {
  for (const w of weights) {
    const src = `node_modules/${pkg}/files/${name}-latin-${w}-normal.woff2`;
    if (existsSync(src)) copyFileSync(src, `static/fonts/${name}-latin-${w}-normal.woff2`);
    else console.warn('missing', src);
  }
}
copyFileSync('node_modules/htmx.org/dist/htmx.min.js', 'static/htmx.min.js');
copyFileSync('node_modules/lucide/dist/umd/lucide.min.js', 'static/lucide.min.js');
console.log('assets ok:', readdirSync('static/fonts').length, 'fonts');