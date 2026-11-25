import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
const source=readFileSync(new URL('../src/lib/safe-mail-preview.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
const {safeMailNodes}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))
const text=value=>({nodeType:3,textContent:value})
const el=(localName,childNodes=[],attrs={},namespaceURI='http://www.w3.org/1999/xhtml')=>({nodeType:1,localName,childNodes,namespaceURI,getAttribute:key=>attrs[key]??null})

test('message formatting is reconstructed without forwarding attributes',()=>{
  assert.deepEqual(safeMailNodes([el('p',[text('Hello'),el('strong',[text('world')],{onclick:'evil()'})],{style:'background:url(https://evil.test)'})]),[{tag:'p',children:['Hello',{tag:'strong',children:['world']}]}])
})
test('active and foreign-namespace subtrees are discarded, images are inert',()=>{
  const tags=['script','style','svg','math','iframe','form','template','object','embed','link','meta','base','input','video']
  assert.deepEqual(safeMailNodes([...tags.map(tag=>el(tag,[text('bad')])),el('p',[text('ok')],{},'http://www.w3.org/2000/svg'),el('img',[],{src:'https://evil.test'}),el('p',[text('safe')])]),['[obraz pominięty]',{tag:'p',children:['safe']}])
})
test('links are literal text only and dangerous URI schemes are omitted',()=>{
  assert.deepEqual(safeMailNodes([el('a',[text('reset')],{href:'https://app.test/reset?token=fixture'}),el('a',[text('bad')],{href:'javascript:evil()'})]),[{tag:'span',children:['reset',' (https://app.test/reset?token=fixture)']},{tag:'span',children:['bad']}])
})
test('preview node count and recursion are bounded',()=>{
  assert.equal(safeMailNodes(Array.from({length:6000},()=>text('x'))).length,5000)
  let node=text('deep')
  for(let i=0;i<100;i++)node=el('div',[node])
  assert.equal(JSON.stringify(safeMailNodes([node])).includes('deep'),false)
})
