import {mkdir,mkdtemp,cp,symlink,rm,readFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {homedir} from 'node:os';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';
import {writeFixture,first,second} from './fixture.mjs';
const temp=process.env.TMPDIR||resolve(homedir(),'tmp');await mkdir(temp,{recursive:true});
const dir=await mkdtemp(resolve(temp,'jepa-build-'));
try {
 for(const path of ['src','scripts','index.html','package.json','tsconfig.json'])await cp(resolve(path),resolve(dir,path),{recursive:true});
 await symlink(resolve('node_modules'),resolve(dir,'node_modules'),'dir');
 await writeFixture(resolve(dir,'public/data'));
 const run=spawnSync('npm',['run','build'],{cwd:dir,stdio:'inherit'});assert.equal(run.status,0);
 const paper=await readFile(resolve(dir,`dist/papers/${first}/index.html`),'utf8');assert.ok(paper.includes('Synthetic latent prediction'));assert.ok(paper.includes('&lt;img'));assert.ok(!paper.includes('href="javascript:'));assert.ok(paper.includes('Table 1'));
 assert.ok((await readFile(resolve(dir,`dist/papers/${second}/index.html`),'utf8')).includes('Not yet extracted'));
 assert.ok((await readFile(resolve(dir,'dist/methodology/index.html'),'utf8')).includes('Appraisal policy'));
 console.log('Synthetic static build assertions passed; temporary build removed.');
} finally {await rm(dir,{recursive:true,force:true});}
