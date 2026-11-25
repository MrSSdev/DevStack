import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
const source=readFileSync(new URL('../src/lib/installer-options.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const {resolveInstallerFields,frameworkWebroot}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))
const field=(id,choices,kits=[],when={})=>({id,label:id,default:choices[0],choices:choices.map(id=>({id,label:id})),kits,when})
const schema=[
  field('auth-provider',['laravel','none','workos'],['livewire','react']),
  field('livewire-components',['single-file','class'],['livewire'],{'auth-provider':['laravel']}),
  field('teams',['no','yes'],['livewire','react'],{'auth-provider':['laravel'],'livewire-components':['single-file','']}),
  field('testing',['pest','phpunit']),
]
test('default and conditional installer choices',()=>{
  assert.deepEqual(resolveInstallerFields(schema,'livewire',{}).values,{'auth-provider':'laravel','livewire-components':'single-file',teams:'no',testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema,'livewire',{'livewire-components':'class',teams:'yes'}).values,{'auth-provider':'laravel','livewire-components':'class',testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema,'livewire',{'auth-provider':'workos','livewire-components':'class',teams:'yes'}).values,{'auth-provider':'workos',testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema,'react',{teams:'yes'}).values,{'auth-provider':'laravel',teams:'yes',testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema,'livewire',{'auth-provider':'none',teams:'yes','livewire-components':'class',testing:'phpunit'}).values,{'auth-provider':'none',testing:'phpunit'})
  assert.deepEqual(resolveInstallerFields(schema,'react',{'auth-provider':'none',teams:'yes'}).values,{'auth-provider':'none',testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema,'none',{'auth-provider':'none',teams:'yes'}).values,{testing:'pest'})
  const withoutAuth=resolveInstallerFields(schema,'livewire',{'auth-provider':'none',teams:'yes'}).values
  assert.deepEqual(resolveInstallerFields(schema,'livewire',{...withoutAuth,'auth-provider':'laravel'}).values,{'auth-provider':'laravel','livewire-components':'single-file',teams:'no',testing:'pest'})
})
test('removed options and invalid stale selections cannot be submitted',()=>{
  assert.deepEqual(resolveInstallerFields(schema,'none',{testing:'--force',arbitrary:'yes'}).values,{testing:'pest'})
  assert.deepEqual(resolveInstallerFields(schema.slice(-1),'livewire',{teams:'yes'}).values,{testing:'pest'})
})
test('framework dictates public path; manual path is restored for a blank project',()=>{
  assert.equal(frameworkWebroot({webroot:'public'},'htdocs'),'public')
  assert.equal(frameworkWebroot({webroot:'web'},'htdocs'),'web')
  assert.equal(frameworkWebroot(undefined,'htdocs'),'htdocs')
})
