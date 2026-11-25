// Only this small allowlist is ever rendered. Never attach parsed message DOM
// nodes or attributes to the live page. In particular, no active URLs, images,
// inline styles, forms, event handlers, SVG, scripts or iframes survive.
const tags = new Set(['div','p','span','h1','h2','h3','h4','h5','h6','ul','ol','li','strong','b','em','i','u','s','small','sub','sup','blockquote','br','hr','pre','code','table','thead','tbody','tfoot','tr','td','th','dl','dt','dd'])
const ignored = new Set(['script','style','head','title','meta','link','base','svg','math','iframe','frame','object','embed','form','input','button','select','textarea','template','noscript','video','audio','source'])
export type PreviewNode = string | { tag:string; children:PreviewNode[] }

export function safeMailNodes(nodes: ArrayLike<Node>): PreviewNode[] {
  let remaining=5000
  function visit(items:ArrayLike<Node>,depth:number):PreviewNode[]{
    const output:PreviewNode[]=[]
    if(depth>35)return output
    for(const item of Array.from(items)){
      if(--remaining<0)break
      if(item.nodeType===3){output.push(item.textContent??'');continue}
      if(item.nodeType!==1)continue
      const element=item as Element
      if(element.namespaceURI!=='http://www.w3.org/1999/xhtml')continue
      const tag=element.localName.toLowerCase()
      if(ignored.has(tag))continue
      if(tag==='img'){
        output.push('[obraz pominięty]')
        continue
      }
      const children=visit(element.childNodes,depth+1)
      if(tag==='a'){
        // Show link text and, if useful, a literal address, never a link.
        const href=element.getAttribute('href')??''
        if(/^https?:\/\/[^\s\u0000-\u001f]{1,2048}$/i.test(href))children.push(' ('+href+')')
        output.push({tag:'span',children})
      }else if(tags.has(tag)){
        output.push({tag,children:tag==='br'||tag==='hr'?[]:children})
      }else{
        output.push(...children)
      }
    }
    return output
  }
  return visit(nodes,0)
}

export function parseMailPreview(html:string):PreviewNode[]{
  // A detached template is inert: its contents never enter the active DOM.
  const template=document.createElement('template')
  template.innerHTML=html.slice(0,250_000)
  return safeMailNodes(template.content.childNodes)
}
