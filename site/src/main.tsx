import 'katex/dist/katex.min.css';
import React from 'react';
import {hydrateRoot,createRoot} from 'react-dom/client';
import {App} from './App';
import './style.css';
const root=document.getElementById('root')!;
const id=window.location.pathname.match(/^\/papers\/([^/]+)\/?$/)?.[1];
async function load(){
 const [response,detail]=await Promise.all([
  fetch('/catalog-index.json'),
  id?fetch(`/data/papers/${encodeURIComponent(id)}.json`):Promise.resolve(null)
 ]);
 if(!response.ok)throw Error('Public catalogue is unavailable');
 const data=await response.json();
 if(data.schema_version!=='1.0'||!Array.isArray(data.papers))throw Error('Unsupported public dataset');
 if(id&&detail){
  const position=data.papers.findIndex((p:{work_id:string})=>p.work_id===id);
  if(position>=0){
   if(!detail.ok)throw Error('Paper record unavailable. Reload to get the current catalogue.');
   const paper=await detail.json();
   if(paper.work_id!==id||paper.version!==data.papers[position].version)throw Error('Catalogue changed while loading. Please reload.');
   data.papers[position]=paper;
  }
 }
 const app=<App data={data}/>;
 if(root.hasChildNodes())hydrateRoot(root,app);else createRoot(root).render(app);
}
load().catch(error=>{root.textContent=error.message;});
