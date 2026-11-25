import { createElement, useEffect, useMemo, useState, type FormEvent, type ReactNode } from 'react'
import { ArrowLeft, ArrowRight, Copy, Download, ExternalLink, Mail, RefreshCw, Search, Trash2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { parseMailPreview, type PreviewNode } from '@/lib/safe-mail-preview'

type Address={Name?:string; Address?:string}
type Attachment={PartID:string; FileName?:string; ContentType?:string; Size?:number}
type Summary={ID:string; Subject?:string; From?:Address|null; To?:Address[]|null; Created?:string; Read?:boolean; Snippet?:string; Attachments?:number}
type Message={ID:string; Subject?:string; From?:Address|null; To?:Address[]|null; Cc?:Address[]|null; Bcc?:Address[]|null; Date?:string; Text?:string; HTML?:string; Attachments?:Attachment[]|null; Inline?:Attachment[]|null}
type Page={total:number; unread:number; messages_count:number; start:number; messages:Summary[]|null}
type Request=<T>(method:'GET'|'POST',path:string,csrf?:string,data?:unknown)=>Promise<T>
type Props={status?:string; csrf:string; request:Request; refreshStatus:()=>void}

const idPattern=/^[A-Za-z0-9]{22}$/
const partPattern=/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const pageSize=40
const installCommand='cd ~/projects/devstack-source && sudo bash scripts/install-mailpit.sh'
const manualConfig=['MAIL_MAILER=smtp','MAIL_SCHEME=null','MAIL_URL=null','MAIL_HOST=127.0.0.1','MAIL_PORT=1025','MAIL_USERNAME=null','MAIL_PASSWORD=null','MAIL_ENCRYPTION=null','MAIL_FROM_ADDRESS="hello@twoj-projekt.test"'].join('\n')
const messageDate=(s?:string)=>s&&!Number.isNaN(Date.parse(s))?new Date(s).toLocaleString('pl-PL'):''
function address(a?:Address|null):string{return a?(a.Name&&a.Address?a.Name+' <'+a.Address+'>':a.Address||a.Name||'—'):'—'}
function addresses(a?:Address[]|null):string{return Array.isArray(a)?a.map(address).join(', '):'—'}
function renderPreview(nodes:PreviewNode[]):ReactNode[]{
  return nodes.map((node,index)=>typeof node==='string'?node:createElement(node.tag,{key:index},...renderPreview(node.children)))
}

export function MailInbox({status,csrf,request,refreshStatus}:Props){
  const [query,setQuery]=useState('')
  const [appliedQuery,setAppliedQuery]=useState('')
  const [start,setStart]=useState(0)
  const [revision,setRevision]=useState(0)
  const [page,setPage]=useState<Page|null>(null)
  const [selected,setSelected]=useState('')
  const [message,setMessage]=useState<Message|null>(null)
  const [format,setFormat]=useState<'html'|'text'|'source'>('html')
  const [loading,setLoading]=useState(false)
  const [opening,setOpening]=useState(false)
  const [deleting,setDeleting]=useState(false)
  const [confirmDelete,setConfirmDelete]=useState(false)
  const [listError,setListError]=useState('')
  const [detailError,setDetailError]=useState('')
  const [copied,setCopied]=useState('')
  const active=status==='active'

  useEffect(()=>{
    if(!active){setPage(null);setSelected('');setMessage(null);setLoading(false);return}
    let disposed=false
    setLoading(true);setListError('')
    const params=new URLSearchParams({start:String(start),query:appliedQuery})
    void request<Page>('GET','/api/mail/messages?'+params.toString()).then(result=>{
      if(disposed)return
      setPage({...result,messages:Array.isArray(result.messages)?result.messages.filter(m=>typeof m.ID==='string'&&idPattern.test(m.ID)):[]})
    }).catch(e=>{if(!disposed){setListError(e instanceof Error?e.message:'Nie można pobrać wiadomości.');setPage(null)}}).finally(()=>{if(!disposed)setLoading(false)})
    return ()=>{disposed=true}
  },[active,appliedQuery,start,revision,request])

  useEffect(()=>{
    if(!active||!selected){setMessage(null);setOpening(false);setDetailError('');return}
    let disposed=false
    setMessage(null);setConfirmDelete(false);setOpening(true);setDetailError('')
    void request<Message>('GET','/api/mail/message/'+encodeURIComponent(selected)).then(result=>{
      if(disposed)return
      if(result.ID!==selected)throw new Error('Nieprawidłowa odpowiedź skrzynki.')
      setMessage(result);setFormat(result.HTML?'html':'text')
      setPage(p=>p?{...p,messages:(p.messages??[]).map(m=>m.ID===selected?{...m,Read:true}:m)}:p)
    }).catch(e=>{if(!disposed)setDetailError(e instanceof Error?e.message:'Nie można otworzyć wiadomości.')}).finally(()=>{if(!disposed)setOpening(false)})
    return ()=>{disposed=true}
  },[active,selected,request])

  const preview=useMemo(()=>message?.HTML?parseMailPreview(message.HTML):[],[message])
  const messages=page?.messages??[]
  const count=page?.messages_count??page?.total??0
  const attachments=[...(Array.isArray(message?.Attachments)?message.Attachments:[]),...(Array.isArray(message?.Inline)?message.Inline:[])].filter(a=>typeof a.PartID==='string'&&partPattern.test(a.PartID))
  const refresh=()=>{setRevision(v=>v+1);refreshStatus()}
  function search(e:FormEvent){e.preventDefault();setSelected('');setStart(0);setAppliedQuery(query.trim());setRevision(v=>v+1)}
  async function remove(){
    if(!message||!idPattern.test(message.ID)||deleting||!confirmDelete)return
    setDeleting(true);setDetailError('')
    try{
      await request('POST','/api/mail/delete',csrf,{id:message.ID})
      setSelected('');setMessage(null);setConfirmDelete(false)
      if(messages.length===1&&start>0)setStart(Math.max(0,start-pageSize))
      setRevision(v=>v+1)
    }catch(e){setDetailError(e instanceof Error?e.message:'Nie udało się usunąć wiadomości.')}
    finally{setDeleting(false)}
  }
  async function copy(value:string,label:string){
    try{await navigator.clipboard.writeText(value);setCopied(label+' — skopiowano.')}
    catch{setCopied('Nie udało się skopiować. Zaznacz tekst ręcznie.')}
  }

  return <div className="space-y-5">
    <Card><CardHeader><div className="flex flex-wrap items-center justify-between gap-3"><div><CardTitle className="flex items-center gap-2"><Mail className="size-5"/>Lokalna skrzynka testowa</CardTitle><CardDescription>Mailpit · wspólna skrzynka wiadomości przechwyconych z lokalnych projektów.</CardDescription></div><div className="flex flex-wrap items-center gap-2"><Badge>{active?'Działa':status==='not-installed'?'Niezainstalowana':status==='failed'?'Błąd usługi':status==='inactive'?'Zatrzymana':'Nieznany stan'}</Badge><Button size="sm" variant="outline" onClick={refresh} disabled={loading}><RefreshCw/>Odśwież</Button>{active&&<Button size="sm" variant="outline" onClick={()=>window.open('http://127.0.0.1:8025/','_blank','noopener,noreferrer')}><ExternalLink/>Pełny Mailpit</Button>}</div></div></CardHeader>
      <CardContent className="space-y-3 text-sm">
        {active?<div className="flex flex-wrap gap-x-6 gap-y-2 text-muted-foreground"><span>SMTP <code className="text-foreground">127.0.0.1:1025</code></span><span>Bez TLS i logowania SMTP</span><span>Maks. 500 wiadomości · 10 MB/wiadomość</span></div>:<><p className="text-muted-foreground">{status==='not-installed'?'Zainstaluj opcjonalną usługę Mailpit w terminalu Ubuntu. Instalacja nie zmienia istniejących projektów.':'Sprawdź stan opcjonalnej usługi w Ubuntu, a potem odśwież panel.'}</p><div className="flex items-start gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-md bg-secondary p-3 text-xs">{status==='not-installed'?installCommand:'sudo systemctl start devstack-mailpit.service'}</code><Button size="icon" variant="outline" aria-label="Kopiuj polecenie" onClick={()=>{void copy(status==='not-installed'?installCommand:'sudo systemctl start devstack-mailpit.service','Polecenie')}}><Copy/></Button></div></>}
        <p className="text-xs text-muted-foreground">Tylko poczta przekazana do Mailpit jest przechwytywana. To skrzynka testowa, nie serwer wysyłający pocztę do internetu.</p>
        {copied&&<p role="status" className="text-xs">{copied}</p>}
      </CardContent>
    </Card>
    {active&&<div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(280px,0.8fr)_minmax(0,1.2fr)]">
      <Card className="min-w-0"><CardHeader><CardTitle>Odebrane</CardTitle><CardDescription>{page?String(count)+' wiadomości'+(appliedQuery?' pasujących':''):loading?'Wczytywanie…':'Skrzynka'}</CardDescription><form onSubmit={search} className="mt-2 flex gap-2"><Input aria-label="Szukaj wiadomości" placeholder="Temat, nadawca, treść…" maxLength={200} value={query} onChange={e=>setQuery(e.target.value)}/><Button type="submit" variant="outline" size="icon" aria-label="Szukaj"><Search/></Button></form>{appliedQuery&&<Button className="self-start" size="sm" variant="ghost" onClick={()=>{setQuery('');setAppliedQuery('');setStart(0);setSelected('')}}>Wyczyść wyszukiwanie</Button>}</CardHeader>
        <CardContent className="min-w-0 space-y-3">
          {listError&&<p role="alert" className="text-sm text-destructive">{listError}</p>}
          {!messages.length&&!listError&&<p className="py-10 text-center text-sm text-muted-foreground">{loading?'Pobieranie wiadomości…':appliedQuery?'Nie znaleziono wiadomości.':'Brak wiadomości. Wyślij e-mail z aplikacji skonfigurowanej do lokalnego SMTP.'}</p>}
          <div className="divide-y">{messages.map(m=><button key={m.ID} type="button" disabled={deleting} onClick={()=>setSelected(m.ID)} aria-pressed={selected===m.ID} className={'block w-full min-w-0 rounded-md px-3 py-3 text-left hover:bg-secondary/60 '+(selected===m.ID?'bg-secondary':'')}><div className="flex items-center justify-between gap-2"><span className="truncate text-xs text-muted-foreground">{address(m.From)}</span><span className="shrink-0 text-[10px] text-muted-foreground">{messageDate(m.Created)}</span></div><div className={'mt-1 break-words text-sm '+(!m.Read?'font-semibold':'font-medium')}>{!m.Read&&<span className="mr-1.5 inline-block size-2 rounded-full bg-blue-500"/>}{m.Subject||'(bez tematu)'}</div><div className="mt-1 truncate text-xs text-muted-foreground">{m.Snippet??''}</div></button>)}</div>
          {page&&count>pageSize&&<div className="flex items-center justify-between gap-2"><Button size="sm" variant="outline" disabled={start===0||loading||deleting} onClick={()=>setStart(Math.max(0,start-pageSize))}><ArrowLeft/>Wstecz</Button><span className="text-xs text-muted-foreground">{start+1}–{start+messages.length} / {count}</span><Button size="sm" variant="outline" disabled={start+pageSize>=count||loading||deleting} onClick={()=>setStart(start+pageSize)}>Dalej<ArrowRight/></Button></div>}
        </CardContent>
      </Card>
      <Card className="min-w-0"><CardHeader><CardTitle className="break-words">{message?.Subject||'Podgląd wiadomości'}</CardTitle><CardDescription>Podgląd nie ładuje zdalnych obrazów, stylów ani aktywnych linków.</CardDescription></CardHeader>
        <CardContent className="min-w-0 space-y-4">
          {detailError&&<p role="alert" className="text-sm text-destructive">{detailError}</p>}
          {opening&&<p className="py-8 text-center text-sm text-muted-foreground">Otwieranie wiadomości…</p>}
          {!selected&&!opening&&<p className="py-12 text-center text-sm text-muted-foreground">Wybierz wiadomość z listy.</p>}
          {message&&<><dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm break-words"><dt className="text-muted-foreground">Od</dt><dd>{address(message.From)}</dd><dt className="text-muted-foreground">Do</dt><dd>{addresses(message.To)}</dd>{message.Cc?.length?<><dt className="text-muted-foreground">DW</dt><dd>{addresses(message.Cc)}</dd></>:null}{message.Bcc?.length?<><dt className="text-muted-foreground">UDW</dt><dd>{addresses(message.Bcc)}</dd></>:null}<dt className="text-muted-foreground">Data</dt><dd>{messageDate(message.Date)}</dd></dl>
            <div className="flex flex-wrap gap-2">{message.HTML&&<Button size="sm" variant={format==='html'?'default':'outline'} onClick={()=>setFormat('html')}>HTML</Button>}<Button size="sm" variant={format==='text'?'default':'outline'} onClick={()=>setFormat('text')}>Tekst</Button>{message.HTML&&<Button size="sm" variant={format==='source'?'default':'outline'} onClick={()=>setFormat('source')}>Źródło HTML</Button>}<Button size="sm" variant="outline" aria-label="Kopiuj treść" onClick={()=>{void copy(format==='text'?message.Text??'':message.HTML??'','Treść')}}><Copy/></Button></div>
            {format==='html'?<div className="overflow-x-auto break-words rounded-md border bg-background p-4 text-sm [&_h1]:text-xl [&_h2]:text-lg [&_h3]:font-semibold [&_p]:my-2 [&_ul]:list-disc [&_ol]:list-decimal [&_ul]:pl-6 [&_ol]:pl-6 [&_pre]:whitespace-pre-wrap [&_td]:border [&_td]:p-2 [&_th]:border [&_th]:p-2 [&_hr]:my-3">{renderPreview(preview)}{(message.HTML?.length??0)>250000&&<p className="text-xs text-muted-foreground">Podgląd skrócono. Pełna treść dostępna w Mailpit.</p>}</div>:<pre className="max-h-[65vh] overflow-auto whitespace-pre-wrap break-words rounded-md border bg-background p-4 text-sm">{(format==='source'?message.HTML:message.Text)||'(brak treści w tym formacie)'}</pre>}
            {attachments.length>0&&<div className="space-y-2"><h3 className="text-sm font-semibold">Załączniki</h3>{attachments.map((a,i)=><a key={a.PartID+'-'+i} href={'/api/mail/message/'+message.ID+'/part/'+encodeURIComponent(a.PartID)} download={a.FileName||'mailpit-attachment'} className="flex items-center gap-2 rounded-md border p-2 text-sm hover:bg-secondary"><Download className="size-4 shrink-0"/><span className="min-w-0 flex-1 break-all">{a.FileName||a.ContentType||'Załącznik'}</span><span className="shrink-0 text-xs text-muted-foreground">{typeof a.Size==='number'?Math.ceil(a.Size/1024)+' KB':''}</span></a>)}</div>}
            <div className="flex flex-wrap gap-2 border-t pt-4">{confirmDelete?<><span className="self-center text-sm text-muted-foreground">Usunąć tę wiadomość?</span><Button variant="destructive" disabled={deleting} onClick={()=>{void remove()}}>{deleting?'Usuwanie…':'Usuń'}</Button><Button variant="outline" disabled={deleting} onClick={()=>setConfirmDelete(false)}>Anuluj</Button></>:<Button variant="outline" onClick={()=>setConfirmDelete(true)}><Trash2/>Usuń wiadomość</Button>}</div>
          </>}
        </CardContent>
      </Card>
    </div>}
    <Card><CardHeader><CardTitle>Konfiguracja poczty w projektach</CardTitle><CardDescription>Nowe projekty: zaznacz „Lokalna poczta testowa” w formularzu.</CardDescription></CardHeader><CardContent className="space-y-3 text-sm text-muted-foreground"><p>Laravel otrzyma ustawienia SMTP i domyślny adres nadawcy we własnej domenie .test. W zwykłym projekcie PHP przechwytywane jest mail() z indywidualnej puli PHP-FPM. Istniejące projekty pozostają bez zmian.</p><details><summary className="cursor-pointer font-medium text-foreground">Ręczna konfiguracja istniejącego projektu Laravel</summary><p className="my-2">Zastąp odpowiednie zmienne MAIL_* w lokalnym .env projektu, ustaw właściwy adres nadawcy i wykonaj <code>php artisan config:clear</code>. Nie kopiuj tej konfiguracji do środowiska produkcyjnego.</p><pre className="overflow-x-auto rounded-md bg-secondary p-3 text-xs text-foreground">{manualConfig}</pre><Button className="mt-2" size="sm" variant="outline" onClick={()=>{void copy(manualConfig,'Konfiguracja SMTP')}}><Copy/>Kopiuj ustawienia</Button></details></CardContent></Card>
  </div>
}
