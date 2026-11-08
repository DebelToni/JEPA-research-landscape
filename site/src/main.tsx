import 'katex/dist/katex.min.css';
import React from 'react';
import {hydrateRoot,createRoot} from 'react-dom/client';
import {App} from './App';
import './style.css';
const root=document.getElementById('root')!;
fetch('/data/catalog.json').then(r=>{if(!r.ok)throw Error('Public catalogue is unavailable');return r.json();}).then(data=>{if(data.schema_version!=='1.0'||!Array.isArray(data.papers))throw Error('Unsupported public dataset');const app=<App data={data}/>;if(root.hasChildNodes())hydrateRoot(root,app);else createRoot(root).render(app);}).catch(error=>{root.textContent=error.message;});
