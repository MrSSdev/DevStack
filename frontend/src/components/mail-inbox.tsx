import { useEffect, useRef, useState, type FormEvent } from 'react'
import { ArrowLeft, ArrowRight, Copy, Download, ExternalLink, Paperclip, RefreshCw, Search, Settings, Trash2, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { toast, beginNotice, notify, fail } from '@/components/ui/toast'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { PageHeader } from '@/components/page-header'
import { notificationRouteHash } from '@/lib/notification-client'

type Address={Name?:string; Address?:string}
type Attachment={PartID:string; FileName?:string; ContentType?:string; Size?:number}
type Summary={ID:string; Subject?:string; From?:Address|null; To?:Address[]|null; Created?:string; Read?:boolean; Snippet?:string; Attachments?:number}
type Message={ID:string; Subject?:string; From?:Address|null; To?:Address[]|null; Cc?:Address[]|null; Bcc?:Address[]|null; Date?:string; Text?:string; HTML?:string; Attachments?:Attachment[]|null; Inline?:Attachment[]|null}
type Page={total:number; unread:number; messages_count:number; start:number; messages:Summary[]|null}
type Request=<T>(method:'GET'|'POST',path:string,csrf?:string,data?:unknown,operationToastId?:string)=>Promise<T>
type Props={initialMessage?:string;status?:string; csrf:string; request:Request; refreshStatus:()=>void}

const idPattern=/^[A-Za-z0-9]{22}$/
const partPattern=/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/
const pageSize=40
const installCommand='cd ~/projects/devstack-source && sudo bash scripts/install-mailpit.sh'
const manualConfig=['MAIL_MAILER=smtp','MAIL_SCHEME=null','MAIL_URL=null','MAIL_HOST=127.0.0.1','MAIL_PORT=1025','MAIL_USERNAME=null','MAIL_PASSWORD=null','MAIL_ENCRYPTION=null','MAIL_FROM_ADDRESS="hello@twoj-projekt.test"'].join('\n')
const messageDate=(s?:string)=>s&&!Number.isNaN(Date.parse(s))?new Date(s).toLocaleString('pl-PL'):''
function address(a?:Address|null):string{return a?(a.Name&&a.Address?a.Name+' <'+a.Address+'>':a.Address||a.Name||'—'):'—'}
function addresses(a?:Address[]|null):string{return Array.isArray(a)?a.map(address).join(', '):'—'}

export function MailInbox({initialMessage='',status,csrf,request,refreshStatus}:Props){
  const [query,setQuery]=useState('')
  const [appliedQuery,setAppliedQuery]=useState('')
  const [start,setStart]=useState(0)
  const [revision,setRevision]=useState(0)
  const [page,setPage]=useState<Page|null>(null)
  const [selected,setSelected]=useState(idPattern.test(initialMessage)?initialMessage:'')
  const [message,setMessage]=useState<Message|null>(null)
  const [format,setFormat]=useState<'html'|'text'|'source'>('html')
  const [externalImagesFor,setExternalImagesFor]=useState('')
  const [loading,setLoading]=useState(false)
  const [opening,setOpening]=useState(false)
  const [deleting,setDeleting]=useState(false)
  const [confirmDelete,setConfirmDelete]=useState<'one'|'many'|'all'|null>(null)
  const [checked,setChecked]=useState<string[]>([])
  const settingsDialog=useRef<HTMLDialogElement>(null)
  const readerHeading=useRef<HTMLHeadingElement>(null)
  const [listError,setListError]=useState('')
  const active=status==='active'

  useEffect(()=>{
    if(!active){setPage(null);if(status!==undefined)setSelected('');setMessage(null);setLoading(false);return}
    let disposed=false
    setLoading(true);setListError('')
    const params=new URLSearchParams({start:String(start),query:appliedQuery})
    void request<Page>('GET','/api/mail/messages?'+params.toString()).then(result=>{
      if(disposed)return
      const valid=Array.isArray(result.messages)?result.messages.filter(m=>typeof m.ID==='string'&&idPattern.test(m.ID)):[]
      toast.close('mail-list-error');setPage({...result,messages:valid})
      setChecked(ids=>ids.filter(id=>valid.some(m=>m.ID===id)))
      if(!valid.length&&start>0)setStart(s=>Math.max(0,s-pageSize))
    }).catch(e=>{if(!disposed){setListError('Nie można pobrać wiadomości.');fail(e,'mail-list-error','Nie można pobrać wiadomości.');setPage(null)}}).finally(()=>{if(!disposed)setLoading(false)})
    return ()=>{disposed=true}
  },[active,appliedQuery,start,revision,request])

  useEffect(()=>{
    if(!active||!selected){setMessage(null);setOpening(false);return}
    let disposed=false
    setMessage(null);setConfirmDelete(null);setOpening(true);setExternalImagesFor('')
    void request<Message>('GET','/api/mail/message/'+encodeURIComponent(selected)).then(result=>{
      if(disposed)return
      if(result.ID!==selected)throw new Error('Nieprawidłowa odpowiedź skrzynki.')
      toast.close('mail-detail-error');setMessage(result);setFormat(result.HTML?'html':'text')
      setPage(p=>p?{...p,unread:Math.max(0,p.unread-((p.messages??[]).some(m=>m.ID===selected&&!m.Read)?1:0)),messages:(p.messages??[]).map(m=>m.ID===selected?{...m,Read:true}:m)}:p)
    }).catch(e=>{if(!disposed)fail(e,'mail-detail-error','Nie można otworzyć wiadomości.')}).finally(()=>{if(!disposed)setOpening(false)})
    return ()=>{disposed=true}
  },[active,selected,request])

  useEffect(()=>{
    if(!active||selected||deleting||confirmDelete||checked.length)return
    const timer=window.setInterval(()=>{if(document.visibilityState==='visible')setRevision(v=>v+1)},15000)
    return ()=>window.clearInterval(timer)
  },[active,selected,deleting,confirmDelete,checked.length])
  useEffect(()=>{if(selected)readerHeading.current?.focus()},[selected])
  const externalImages=!!message&&externalImagesFor===message.ID
  const messages=page?.messages??[]
  const count=page?.messages_count??page?.total??0
  const attachments=[...(Array.isArray(message?.Attachments)?message.Attachments:[]),...(Array.isArray(message?.Inline)?message.Inline:[])].filter(a=>typeof a.PartID==='string'&&partPattern.test(a.PartID))
  const refresh=()=>{setRevision(v=>v+1);refreshStatus()}
  function search(e:FormEvent){e.preventDefault();setChecked([]);setStart(0);setAppliedQuery(query.trim());setRevision(v=>v+1)}
  function changePage(next:number){setChecked([]);setStart(next)}
  function back(){if(/^#\/mail\/[A-Za-z0-9]{22}$/.test(notificationRouteHash()))window.location.hash='/mail';setSelected('');setMessage(null);setConfirmDelete(null);setRevision(v=>v+1)}
  async function remove(){
    if(deleting||!confirmDelete)return
    const mode=confirmDelete
    const ids=mode==='one'&&selected?[selected]:checked
    if(mode!=='all'&&(!ids.length||ids.some(id=>!idPattern.test(id))))return
    setDeleting(true)
    const pending=beginNotice(mode==='all'?'Opróżnianie skrzynki…':'Usuwanie wiadomości…')
    try{
      if(mode==='all')await request('POST','/api/mail/clear',csrf,{confirmation:'delete-all'},pending)
      else if(mode==='one')await request('POST','/api/mail/delete',csrf,{id:ids[0]},pending)
      else await request('POST','/api/mail/delete-many',csrf,{ids},pending)
      if(/^#\/mail\/[A-Za-z0-9]{22}$/.test(notificationRouteHash()))window.location.hash='/mail'
      setSelected('');setMessage(null);setConfirmDelete(null);setChecked([])
      if(mode==='all'){setQuery('');setAppliedQuery('');setStart(0)}
      else if(messages.every(m=>ids.includes(m.ID))&&start>0)setStart(Math.max(0,start-pageSize))
      notify(mode==='all'?'Opróżniono skrzynkę.':mode==='one'?'Usunięto wiadomość.':'Usunięto wiadomości.',pending)
      setRevision(v=>v+1)
    }catch(e){fail(e,pending,'Nie udało się usunąć wiadomości.')}
    finally{setDeleting(false)}
  }
  async function copy(value:string,label:string){
    try{await navigator.clipboard.writeText(value);notify(label+' — skopiowano.')}
    catch{fail('Nie udało się skopiować. Zaznacz tekst ręcznie.')}
  }

  return <div className="min-w-0 space-y-3">
    <PageHeader title="E-mail" actions={<>
      {selected&&<Button size="sm" variant="outline" disabled={deleting} onClick={back}><ArrowLeft/>Odebrane</Button>}
      {!selected&&<Button size="sm" variant="outline" onClick={refresh} disabled={loading||deleting||!!confirmDelete}><RefreshCw className={loading?'animate-spin':''}/>Odśwież</Button>}
            {active&&selected&&<Button size="sm" variant="outline" disabled={opening||deleting||!message||!!confirmDelete} onClick={()=>setConfirmDelete('one')}><Trash2/>Usuń</Button>}
            {active&&!selected&&checked.length>0&&<Button size="sm" variant="outline" disabled={loading||deleting||!!confirmDelete} onClick={()=>setConfirmDelete('many')}><Trash2/>Usuń ({checked.length})</Button>}
            {active&&!selected&&<Button size="sm" variant="outline" disabled={loading||deleting||!!confirmDelete||!page||page.total===0} onClick={()=>setConfirmDelete('all')}><Trash2/>Opróżnij</Button>}
            {active&&<Button size="icon" variant="outline" title="Otwórz Mailpit" aria-label="Otwórz Mailpit" onClick={()=>window.open('http://127.0.0.1:8025/'+(selected?'view/'+encodeURIComponent(selected):''),'_blank','noopener,noreferrer')}><ExternalLink/></Button>}
            <Button size="icon" variant="outline" aria-label="Konfiguracja poczty" title="Konfiguracja poczty" disabled={deleting||!!confirmDelete} onClick={()=>settingsDialog.current?.showModal()}><Settings/></Button>
    </>}/>
    <Card className="min-w-0">
      {!selected&&<CardHeader><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold tracking-tight">Odebrane</h2>{page&&<Badge>Wiadomości: {page.total} · Nieprzeczytane: {page.unread}</Badge>}</div></CardHeader>}
      <CardContent className={selected?'space-y-3 p-3':'space-y-3'}>
        {confirmDelete&&<div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 p-2 text-sm" role="alert">
          <span className="flex-1">{confirmDelete==='all'?'Trwale usunąć wszystkie wiadomości ze wszystkich projektów, także poza aktualnym wyszukiwaniem?':confirmDelete==='many'?'Usunąć zaznaczone wiadomości ('+checked.length+')?':'Trwale usunąć tę wiadomość?'}</span>
          <Button size="sm" variant="destructive" disabled={deleting} onClick={()=>{void remove()}}>{deleting?'Usuwanie…':'Potwierdź'}</Button><Button size="sm" variant="outline" disabled={deleting} onClick={()=>{setConfirmDelete(null)}}>Anuluj</Button>
        </div>}
        {!active&&<div className="rounded-md border p-4 text-sm"><p className="font-medium">{status==='not-installed'?'Mailpit nie jest zainstalowany':status==='inactive'?'Mailpit jest zatrzymany':status==='failed'?'Błąd Mailpit':'Brak połączenia z Mailpit'}</p><p className="mt-1 text-muted-foreground">Otwórz konfigurację, aby sprawdzić polecenia.</p><Button className="mt-3" size="sm" variant="outline" onClick={()=>settingsDialog.current?.showModal()}><Settings/>Konfiguracja</Button></div>}
        {active&&!selected&&<>
          <form onSubmit={search} className="flex gap-2"><Input aria-label="Szukaj wiadomości" placeholder="Temat, nadawca, treść…" maxLength={200} value={query} onChange={e=>setQuery(e.target.value)} disabled={deleting||!!confirmDelete}/><Button type="submit" variant="outline" size="icon" aria-label="Szukaj" disabled={deleting||!!confirmDelete}><Search/></Button>{appliedQuery&&<Button type="button" size="icon" variant="ghost" aria-label="Wyczyść wyszukiwanie" disabled={deleting||!!confirmDelete} onClick={()=>{setQuery('');setAppliedQuery('');changePage(0)}}><X/></Button>}</form>
          {listError&&<p className="py-8 text-center text-sm text-muted-foreground">Skrzynka niedostępna. Odśwież dane.</p>}
          {!messages.length&&!listError&&<p className="py-8 text-center text-sm text-muted-foreground">{loading?'Pobieranie wiadomości…':appliedQuery?'Brak wyników.':'Skrzynka jest pusta.'}</p>}
          {messages.length>0&&<div className="overflow-hidden rounded-md border">
            <div className="flex items-center gap-2 border-b bg-secondary/40 px-2 py-1.5 text-xs text-muted-foreground"><input type="checkbox" ref={node=>{if(node)node.indeterminate=checked.length>0&&!messages.every(m=>checked.includes(m.ID))}} className="size-4 accent-primary" aria-label="Zaznacz wszystkie wiadomości na stronie" checked={messages.every(m=>checked.includes(m.ID))} disabled={deleting||!!confirmDelete||loading} onChange={e=>setChecked(e.target.checked?messages.map(m=>m.ID):[])}/><span className="flex-1">{checked.length?'Zaznaczono: '+checked.length:'Wiadomości'}</span><span>{count} {appliedQuery?'wyników':'łącznie'}</span></div>
            <div className="max-h-[max(16rem,calc(100dvh-20rem))] divide-y overflow-y-auto">{messages.map(m=><div key={m.ID} className="flex min-w-0 items-center gap-2 px-2 hover:bg-secondary/50">
              <input type="checkbox" className="size-4 shrink-0 accent-primary" aria-label={'Zaznacz: '+(m.Subject||'bez tematu')} checked={checked.includes(m.ID)} disabled={deleting||!!confirmDelete||loading} onChange={e=>setChecked(ids=>e.target.checked?[...ids,m.ID]:ids.filter(id=>id!==m.ID))}/>
              <button type="button" disabled={deleting||!!confirmDelete} onClick={()=>{setChecked([]);setSelected(m.ID)}} className="grid min-w-0 flex-1 gap-0.5 py-2 text-left focus-visible:outline-2 focus-visible:outline-ring sm:grid-cols-[minmax(7rem,0.8fr)_minmax(0,1.4fr)_auto] sm:items-center sm:gap-2">
                <span className={'min-w-0 truncate text-xs '+(!m.Read?'font-semibold':'text-muted-foreground')}>{address(m.From)}</span>
                <span className="min-w-0"><span className={'block truncate text-sm '+(!m.Read?'font-semibold':'font-medium')}>{!m.Read&&<span className="mr-1.5 inline-block size-2 rounded-full bg-blue-500"/>}{m.Subject||'(bez tematu)'}</span><span className="block truncate text-xs text-muted-foreground">{m.Snippet??''}</span></span>
                <span className="flex items-center gap-1 text-xs text-muted-foreground sm:justify-end">{!!m.Attachments&&<Paperclip className="size-3"/>}<time dateTime={m.Created}>{messageDate(m.Created)}</time></span>
              </button>
            </div>)}</div>
          </div>}
          {page&&count>pageSize&&<div className="flex items-center justify-between gap-2"><Button size="sm" variant="outline" disabled={start===0||loading||deleting||!!confirmDelete} onClick={()=>changePage(Math.max(0,start-pageSize))}><ArrowLeft/>Wstecz</Button><span className="text-xs text-muted-foreground">{start+1}–{start+messages.length} / {count}</span><Button size="sm" variant="outline" disabled={start+pageSize>=count||loading||deleting||!!confirmDelete} onClick={()=>changePage(start+pageSize)}>Dalej<ArrowRight/></Button></div>}
        </>}
        {active&&selected&&<article className="min-w-0 space-y-3">
          <h2 ref={readerHeading} tabIndex={-1} className="break-words text-xl font-semibold outline-none">{message?.Subject|| (opening?'Otwieranie wiadomości…':'Wiadomość')}</h2>
          {!opening&&!message&&<p className="py-6 text-center text-sm text-muted-foreground">Wiadomość niedostępna.</p>}
          {opening&&<p className="py-6 text-center text-sm text-muted-foreground">Wczytywanie…</p>}
          {message&&<>
            <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 break-words text-sm"><dt className="text-muted-foreground">Od</dt><dd className="font-medium">{address(message.From)}</dd><dt className="text-muted-foreground">Do</dt><dd>{addresses(message.To)}</dd>{message.Cc?.length?<><dt className="text-muted-foreground">DW</dt><dd>{addresses(message.Cc)}</dd></>:null}{message.Bcc?.length?<><dt className="text-muted-foreground">UDW</dt><dd>{addresses(message.Bcc)}</dd></>:null}<dt className="text-muted-foreground">Data</dt><dd>{messageDate(message.Date)}</dd></dl>
            <div className="flex flex-wrap items-center gap-1.5 border-y py-2">{message.HTML&&<Button size="sm" variant={format==='html'?'default':'ghost'} onClick={()=>setFormat('html')}>HTML</Button>}<Button size="sm" variant={format==='text'?'default':'ghost'} onClick={()=>setFormat('text')}>Tekst</Button>{message.HTML&&<Button size="sm" variant={format==='source'?'default':'ghost'} onClick={()=>setFormat('source')}>Źródło HTML</Button>}<Button size="icon" variant="ghost" aria-label="Kopiuj treść" title="Kopiuj treść" onClick={()=>{void copy(format==='text'?message.Text??'':message.HTML??'','Treść')}}><Copy/></Button></div>
            
            {attachments.length>0&&<div className="space-y-1.5"><h3 className="text-xs font-semibold text-muted-foreground">Załączniki ({attachments.length})</h3><div className="flex flex-wrap gap-2">{attachments.map((a,i)=><a key={a.PartID+'-'+i} href={'/api/mail/message/'+message.ID+'/part/'+encodeURIComponent(a.PartID)} download={a.FileName||'mailpit-attachment'} className="flex min-w-0 max-w-full items-center gap-2 rounded-md border px-2 py-1.5 text-sm hover:bg-secondary"><Download className="size-4 shrink-0"/><span className="min-w-0 break-all">{a.FileName||a.ContentType||'Załącznik'}</span><span className="shrink-0 text-xs text-muted-foreground">{typeof a.Size==='number'?Math.ceil(a.Size/1024)+' KB':''}</span></a>)}</div></div>}
            {format==='html'?<div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="min-w-0 flex-1 text-xs text-muted-foreground">Oryginalne style HTML. Linki, skrypty i formularze nieaktywne.</p>
                <Button size="sm" variant="outline" aria-pressed={externalImages} onClick={()=>setExternalImagesFor(externalImages?'':message.ID)}>{externalImages?'Ukryj obrazy zewnętrzne':'Wczytaj obrazy zewnętrzne'}</Button>
              </div>
              <p className="text-xs text-muted-foreground">{externalImages?'Obrazy HTTPS włączone dla tej wiadomości. Mogą kontaktować się z serwerami nadawcy.':'Obrazy zewnętrzne zablokowane. Wczytanie może ujawnić otwarcie wiadomości nadawcy.'}</p>
              <iframe key={message.ID} title="Podgląd wiadomości HTML" sandbox="" referrerPolicy="no-referrer" src={'/api/mail/message/'+message.ID+'/preview'+(externalImages?'?images=1':'')} className="block h-[max(24rem,calc(100dvh-13rem))] w-full min-w-0 rounded-md border bg-white"/>
            </div>:<pre className="overflow-x-auto whitespace-pre-wrap break-words rounded-md border bg-background p-3 text-sm">{(format==='source'?message.HTML:message.Text)||'(brak treści w tym formacie)'}</pre>}
          </>}
        </article>}
      </CardContent>
    </Card>
    <dialog ref={settingsDialog} aria-labelledby="mail-settings-title" className="fixed inset-0 m-auto max-h-[90vh] w-[min(94vw,42rem)] overflow-y-auto rounded-xl border bg-card p-4 text-foreground shadow-xl backdrop:bg-black/50">
      <div className="flex items-center justify-between gap-2"><h2 id="mail-settings-title" className="text-lg font-semibold">Konfiguracja poczty</h2><Button size="icon" variant="ghost" aria-label="Zamknij konfigurację" onClick={()=>settingsDialog.current?.close()}><X/></Button></div>
      <div className="mt-3 space-y-3 text-sm">
        <div className="flex flex-wrap items-center gap-2"><Badge>{active?'Działa':status==='not-installed'?'Niezainstalowany':status==='failed'?'Błąd':status==='inactive'?'Zatrzymany':'Brak danych'}</Badge><Button size="sm" variant="outline" onClick={refresh}><RefreshCw/>Odśwież stan</Button>{active&&<Button size="sm" variant="outline" onClick={()=>window.open('http://127.0.0.1:8025/','_blank','noopener,noreferrer')}><ExternalLink/>Mailpit</Button>}</div>
        {!active&&<div className="flex items-start gap-2"><code className="min-w-0 flex-1 select-all break-all rounded-md bg-secondary p-2 text-xs">{status==='not-installed'?installCommand:'sudo systemctl start devstack-mailpit.service'}</code><Button size="icon" variant="outline" aria-label="Kopiuj polecenie" onClick={()=>{void copy(status==='not-installed'?installCommand:'sudo systemctl start devstack-mailpit.service','Polecenie')}}><Copy/></Button></div>}
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1"><dt className="text-muted-foreground">SMTP</dt><dd><code>127.0.0.1:1025</code></dd><dt className="text-muted-foreground">TLS / logowanie</dt><dd>Wyłączone</dd><dt className="text-muted-foreground">Limit</dt><dd>500 wiadomości · 10 MB/wiadomość</dd></dl>
        <p className="text-xs text-muted-foreground">Mailpit przechwytuje pocztę testową; nie wysyła jej do internetu.</p>
        <div className="border-t pt-3"><h3 className="font-semibold">Konfiguracja projektu</h3><p className="mt-1 text-muted-foreground">Nowy projekt: zaznacz „Lokalna poczta testowa”. Laravel otrzyma ustawienia SMTP, a PHP — przechwytywanie mail().</p></div>
        <details><summary className="cursor-pointer font-medium">Istniejący projekt Laravel (.env)</summary><p className="my-2 text-muted-foreground">Zmień MAIL_* lokalnie, ustaw adres nadawcy i wykonaj <code>php artisan config:clear</code>. Nie używaj tej konfiguracji na produkcji.</p><pre className="overflow-x-auto rounded-md bg-secondary p-2 text-xs">{manualConfig}</pre><Button className="mt-2" size="sm" variant="outline" onClick={()=>{void copy(manualConfig,'Konfiguracja SMTP')}}><Copy/>Kopiuj ustawienia</Button></details>
        
      </div>
    </dialog>
  </div>
}
