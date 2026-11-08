import {readFile, mkdir, writeFile} from 'node:fs/promises';
const packages = ['react', 'react-dom', 'scheduler', 'cytoscape', 'katex'];
const notices = [];
for (const name of packages) {
  const root = new URL(`../node_modules/${name}/`, import.meta.url);
  const pkg = JSON.parse(await readFile(new URL('package.json', root), 'utf8'));
  const license = await readFile(new URL('LICENSE', root), 'utf8');
  notices.push(`${name} ${pkg.version}\n${'='.repeat(60)}\n${license}`);
}
await mkdir(new URL('../public/', import.meta.url), {recursive: true});
await writeFile(new URL('../public/THIRD-PARTY-LICENSES.txt', import.meta.url), notices.join('\n\n'));
