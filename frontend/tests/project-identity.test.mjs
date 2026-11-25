import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
const source = readFileSync(new URL('../src/lib/project-identity.ts', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const {slugFromName,resolveProjectIdentity,projectIdentityPayload} = await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))

const blank = {name:'',slug:null,domain:null}

test('typing one character at a time produces the same complete identity as pasting',()=>{
  const name = 'Mój Projekt Laravel'
  let draft = {...blank}
  for (const char of name) {
    draft = {...draft,name:draft.name+char}
    const current = resolveProjectIdentity(draft)
    assert.equal(current.slug,slugFromName(draft.name))
    assert.equal(current.domain,current.slug+'.test')
  }
  assert.deepEqual(resolveProjectIdentity(draft),{slug:'moj-projekt-laravel',domain:'moj-projekt-laravel.test'})
  assert.deepEqual(resolveProjectIdentity(draft),resolveProjectIdentity({...blank,name}))
})

test('rename, delete, paste, and replace the name without retaining a previous prefix',()=>{
  for (const name of ['A','Aplikacja','Aplikac','Nowa Nazwa','']) {
    const identity = resolveProjectIdentity({...blank,name})
    assert.equal(identity.slug,slugFromName(name))
    assert.equal(identity.domain,identity.slug?identity.slug+'.test':'')
  }
  assert.deepEqual(resolveProjectIdentity({...blank,name:'Nowa Nazwa'}),{slug:'nowa-nazwa',domain:'nowa-nazwa.test'})
  assert.deepEqual(resolveProjectIdentity(blank),{slug:'',domain:''})
})

test('custom folder remains unchanged and automatic domain follows its edits',()=>{
  let draft = {...blank,name:'Pierwsza nazwa',slug:'wlasny-folder'}
  draft = {...draft,name:'Druga nazwa'}
  assert.deepEqual(resolveProjectIdentity(draft),{slug:'wlasny-folder',domain:'wlasny-folder.test'})
  draft = {...draft,slug:'nowy-folder'}
  assert.equal(resolveProjectIdentity(draft).domain,'nowy-folder.test')
  assert.deepEqual(resolveProjectIdentity({...draft,slug:null}),{slug:'druga-nazwa',domain:'druga-nazwa.test'})
})

test('custom domain remains unchanged when name or folder changes',()=>{
  let draft = {...blank,name:'Pierwszy',domain:'inna.test'}
  draft = {...draft,name:'Drugi',slug:'nowy-folder'}
  assert.deepEqual(resolveProjectIdentity(draft),{slug:'nowy-folder',domain:'inna.test'})
  assert.equal(resolveProjectIdentity({...draft,domain:null}).domain,'nowy-folder.test')
})

test('clearing a manual input is allowed until automatic generation is explicitly restored',()=>{
  const draft = {...blank,name:'Mój projekt',slug:'',domain:''}
  assert.deepEqual(resolveProjectIdentity(draft),{slug:'',domain:''})
  assert.deepEqual(resolveProjectIdentity({...draft,slug:null,domain:null}),{slug:'moj-projekt',domain:'moj-projekt.test'})
  assert.deepEqual(resolveProjectIdentity({...blank,name:'Mój projekt',slug:''}),{slug:'',domain:''})
})

test('Polish letters, separators, leading numbers and the backend length limit',()=>{
  assert.equal(slugFromName('Zażółć gęślą jaźń'),'zazolc-gesla-jazn')
  assert.equal(slugFromName(' ŁÓDŹ / API__v2  '),'lodz-api-v2')
  assert.equal(slugFromName('2026 Projekt'),'projekt-2026-projekt')
  assert.equal(slugFromName('...'),'')
  assert.equal(slugFromName('a'.repeat(39)+'-koniec'),'a'.repeat(39))
  const long = resolveProjectIdentity({...blank,name:'123 '+'a'.repeat(70)})
  assert.match(long.slug,/^[a-z][a-z0-9-]{1,39}$/)
  assert.match(long.domain,/^[a-z][a-z0-9-]{0,39}\.test$/)
})

test('submitted identity agrees with automatic values and normalizes manual domain',()=>{
  const draft = {...blank,name:'Nowy Projekt'}
  assert.deepEqual(projectIdentityPayload(draft),{name:'Nowy Projekt',...resolveProjectIdentity(draft)})
  assert.deepEqual(projectIdentityPayload({...draft,name:' Nowy Projekt ',slug:'moj-projekt',domain:'INNA.TEST'}),
    {name:'Nowy Projekt',slug:'moj-projekt',domain:'inna.test'})
  assert.deepEqual(projectIdentityPayload(blank),{name:'',slug:'',domain:''})
})
