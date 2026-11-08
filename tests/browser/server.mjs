import {mkdir,mkdtemp,cp,rm} from 'node:fs/promises';
import {homedir} from 'node:os';
import {resolve} from 'node:path';
import {rmSync} from 'node:fs';
import {spawn,spawnSync} from 'node:child_process';
import {writeFixture} from './fixture.mjs';
const temp=process.env.TMPDIR||resolve(homedir(),'tmp');await mkdir(temp,{recursive:true});
const root=await mkdtemp(resolve(temp,'jepa-browser-'));
process.on('exit',()=>rmSync(root,{recursive:true,force:true}));
await writeFixture(`${root}/public/data`);
// Disposable static build; never writes to the real public export.
await cp(resolve('src'),`${root}/src`,{recursive:true});
await cp(resolve('index.html'),`${root}/index.html`);
await cp(resolve('tsconfig.json'),`${root}/tsconfig.json`);
await cp(resolve('package.json'),`${root}/package.json`);
const {symlink}=await import('node:fs/promises');await symlink(resolve('node_modules'),`${root}/node_modules`,'dir');
await cp(resolve('scripts'),`${root}/scripts`,{recursive:true});
const build=spawnSync('npm',['run','build'],{cwd:root,stdio:'inherit'});
if(build.status!==0){await rm(root,{recursive:true,force:true});process.exit(build.status||1);}
const child=spawn(process.execPath,[resolve('node_modules/vite/bin/vite.js'),'preview',root,'--host','127.0.0.1','--port','4179','--strictPort'],{stdio:'inherit'});
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,()=>{child.kill(signal);rmSync(root,{recursive:true,force:true});process.exit(0);});
child.on('exit',async code=>{await rm(root,{recursive:true,force:true});process.exit(code||0);});
