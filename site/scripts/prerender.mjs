import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {resolve} from 'node:path';
import {register} from 'tsx/esm/api';
register();
const {createElement}=await import('react');
const {renderToString}=await import('react-dom/server');
const {App}=await import('../src/App.tsx');
const root=resolve('dist');
const data=JSON.parse(await readFile(resolve('public/data/catalog.json'),'utf8'));
if(data.schema_version!=='1.0'||!Array.isArray(data.papers))throw Error('Unsupported catalogue schema');
const uuid=/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
if(data.papers.some(p=>!uuid.test(p.work_id)))throw Error('Invalid work UUID');
// Browser retrieval view: the scientific catalogue remains the canonical export.
const index={...data,view:'browser-index',papers:data.papers.map(p=>{
 const {extraction:e,classification,...metadata}=p;
 return {...metadata,search_text:[...(e?.keywords||[]),...(e?.sections?.mechanism||[]).map(c=>c.text)].join(' '),
  extraction:e?{summary:e.summary,completeness:e.completeness,artifacts:e.artifacts,
   quality:e.quality?{score:e.quality.score,coverage:e.quality.coverage,rubric:e.quality.rubric}:undefined}:null};
})};
await writeFile(resolve(root,'catalog-index.json'),JSON.stringify(index));
const template=await readFile(resolve(root,'index.html'),'utf8');
for(const path of ['/', '/methodology/', ...data.papers.map(p=>`/papers/${p.work_id}/`)]) {
 const html=renderToString(createElement(App,{data,path}));
 const title=path.startsWith('/papers/')?data.papers.find(p=>path===`/papers/${p.work_id}/`).title:path==='/methodology/'?'Methodology':'Research catalogue';
 const escape=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('\"','&quot;');
 const pageTemplate=template.replace('<title>JEPA research landscape</title>',()=>`<title>${escape(title)} · JEPA landscape</title><link rel="canonical" href="https://jepa.toni.foo${path}">`);
 const dir=resolve(root,'.'+path);await mkdir(dir,{recursive:true});
 await writeFile(resolve(dir,'index.html'),pageTemplate.replace('<div id="root"></div>',()=>`<div id="root">${html}</div>`));
}
await writeFile(resolve(root,'robots.txt'),'User-agent: *\nAllow: /\nSitemap: https://jepa.toni.foo/sitemap.xml\n');
const urls=['/', '/methodology/', ...data.papers.map(p=>`/papers/${p.work_id}/`)];
await writeFile(resolve(root,'sitemap.xml'),'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'+urls.map(path=>`<url><loc>https://jepa.toni.foo${path}</loc></url>`).join('')+'</urlset>\n');
console.log(`Prerendered ${data.papers.length} paper pages, catalogue and methodology`);
