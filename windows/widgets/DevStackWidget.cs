using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Windows.Widgets.Providers;

namespace DevStack.Widgets;

/// <summary>Isolated service-widget lifecycle. Never dispatches WSL Start/Stop.</summary>
internal static class DevStackWidget
{
    private sealed class Instance
    {
        internal required string Id; internal string Nonce=Token(),Client="",Message="";
        internal bool Active,Hidden,Ready,Authenticated,Published;
        internal long Seq,Revision; internal string? Confirmation,Service;
        internal DateTimeOffset Until;
        internal bool Visible=>Active&&!Hidden;
    }
    private static readonly object Gate=new();
    private static readonly Dictionary<string,Instance> Items=new();
    private static readonly JsonSerializerOptions Json=new(JsonSerializerDefaults.Web);
    private static readonly string Server=Guid.NewGuid().ToString("N");
    private static Timer? _timer;
    private static CancellationTokenSource? _visible;
    private static bool _recovered,_worker,_pending,_fresh;
    private static DevStackData.Result? _data;
    private static string? _busy;
    private static long _epoch;
    private static DateTimeOffset _last=DateTimeOffset.UtcNow;
    private static string Token()=>Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    internal static void Recover()
    {
        lock(Gate){if(_recovered)return;_recovered=true;}
        try
        {
            foreach(var info in WidgetManager.GetDefault().GetWidgetInfos())
            {
                var c=info.WidgetContext;if(c.DefinitionId!=WidgetCards.ServicesId)continue;
                var resume=DevStackWebContent.Resume(info.Template);
                lock(Gate)Items[c.Id]=new(){Id=c.Id,Nonce=resume.Nonce??Token(),Published=resume.Current,Active=c.IsActive};
            }
        }
        catch(Exception e){WidgetDiagnostics.Write("services-recover-error",error:e);}
    }
    internal static void Start(){lock(Gate)_timer??=new Timer(_=>Refresh(),null,Timeout.InfiniteTimeSpan,Timeout.InfiniteTimeSpan);Visibility();Refresh();}
    internal static void Stop(){CancellationTokenSource? old;lock(Gate){_timer?.Dispose();_timer=null;old=_visible;_visible=null;_fresh=false;_pending=false;}Cancel(old);}
    private static void Cancel(CancellationTokenSource? old){if(old is null)return;try{old.Cancel();}finally{old.Dispose();}}
    internal static bool CanExit(){lock(Gate)return _busy is null&&!Items.Values.Any(w=>w.Visible)&&DateTimeOffset.UtcNow-_last>TimeSpan.FromSeconds(90);}
    private static void Visibility()
    {
        CancellationTokenSource? old=null;
        lock(Gate)
        {
            bool any=Items.Values.Any(w=>w.Visible);
            if(any&&_visible is null)_visible=new();
            if(!any){old=_visible;_visible=null;_pending=false;_fresh=false;}
            _timer?.Change(any?TimeSpan.FromSeconds(20):Timeout.InfiniteTimeSpan,any?TimeSpan.FromSeconds(20):Timeout.InfiniteTimeSpan);
        }
        Cancel(old);WidgetProvider.Wake.Set();
    }
    internal static void Activate(WidgetContext c)
    {
        lock(Gate){if(!Items.TryGetValue(c.Id,out var w))Items[c.Id]=w=new(){Id=c.Id};w.Active=true;w.Hidden=false;_last=DateTimeOffset.UtcNow;}
        Visibility();Publish(c.Id);Refresh();
    }
    internal static bool Contains(string id){lock(Gate)return Items.ContainsKey(id);}
    internal static void Deactivate(string id)
    {
        lock(Gate){if(Items.TryGetValue(id,out var w)){w.Active=false;w.Confirmation=null;w.Service=null;}_last=DateTimeOffset.UtcNow;}
        Push(id);Visibility();
    }
    internal static void Delete(string id){lock(Gate){Items.Remove(id);_last=DateTimeOffset.UtcNow;}Visibility();}
    private static bool Live()=>_fresh&&_data is not null&&_data.CheckedAt>DateTimeOffset.Now.AddSeconds(-30);
    private static string State(string id,bool native)
    {
        lock(Gate)
        {
            if(!Items.TryGetValue(id,out var w))return "{}";
            bool live=Live();
            if(w.Until<=DateTimeOffset.UtcNow||_data?.Running!=true){w.Confirmation=null;w.Service=null;}
            bool controls=native&&w.Ready&&w.Visible&&w.Authenticated&&live&&_data?.Running==true&&_data.Integration=="ok";
            return JsonSerializer.Serialize(new{
                protocol=1,kind="state",session=w.Nonce,server=Server,client=w.Client,revision=++w.Revision,
                transport=native?"native":"resource",active=w.Visible,canControl=controls,cached=!live,
                canNavigate=native&&w.Ready&&w.Visible&&w.Authenticated&&live&&_data?.Running==true&&_data.Services.Where(s=>s.Id is "nginx" or "php").All(s=>s.Reachable==true),
                phase=_data is null?"checking":_data.Running switch{true=>"running",false=>"offline",_=>"error"},
                integration=_data?.Integration??"unavailable",services=_data?.Services,projectCount=_data?.ProjectCount,
                checkedAt=_data?.CheckedAt,message=w.Message,busy=_busy,
                confirmation=controls?w.Confirmation:null,confirmService=controls?w.Service:null,
                confirmUntil=controls&&w.Confirmation is not null?w.Until.ToUnixTimeMilliseconds():0
            },Json);
        }
    }
    private static void Publish(string id)
    {
        string? template=null;
        lock(Gate){if(!Items.TryGetValue(id,out var w)||!w.Visible)return;if(!w.Published)template=DevStackWebContent.Template(w.Nonce,null);}
        if(template is not null)try
        {
            WidgetManager.GetDefault().UpdateWidget(new(id){Template=template,Data="{}",IsPlaceholderContent=false});
            lock(Gate)if(Items.TryGetValue(id,out var w))w.Published=true;
        }
        catch(Exception e){WidgetDiagnostics.Write("services-publish-error",id,e);}
        Push(id);
    }
    private static void Push(string id)
    {
        lock(Gate)if(!Items.TryGetValue(id,out var w)||!w.Ready)return;
        try{WidgetManager.GetDefault().SendMessageToContent(id,State(id,true));}
        catch(Exception e){WidgetDiagnostics.Write("services-send-error",id,e);}
    }
    private static void PushAll(){string[] ids;lock(Gate)ids=Items.Values.Where(w=>w.Visible).Select(w=>w.Id).ToArray();foreach(var id in ids)Push(id);}
    internal static async void Resource(WidgetResourceRequestedArgs args)
    {
        var def=args.GetDeferral();var id=args.WidgetContext.Id;Uri? uri=null;bool allowed=false,read=false;
        try
        {
            if(args.WidgetContext.DefinitionId==WidgetCards.ServicesId&&args.Request.Method=="GET"&&Uri.TryCreate(args.Request.Uri,UriKind.Absolute,out uri)&&DevStackWebContent.IsLocal(uri))
            {
                lock(Gate)if(Items.TryGetValue(id,out var w))
                {
                    // Every request is scoped to the exact instance document and session.
                    var query=DevStackWebContent.Query(uri);
                    allowed=uri.AbsolutePath=="/services/index.html"&&query.GetValueOrDefault("session")==w.Nonce;
                    if(allowed)
                    {
                        w.Authenticated=string.Equals(query.GetValueOrDefault("auth"),"true",StringComparison.OrdinalIgnoreCase);
                        read=DevStackWebContent.IsStateRead(args);
                        if(!read){w.Ready=false;w.Client="";w.Seq=0;w.Confirmation=null;w.Service=null;}
                    }
                }
            }
            await DevStackWebContent.RespondAsync(args,uri,allowed,allowed?State(id,false):"{}");
            if(allowed){if(read)Push(id);Refresh();}
        }
        catch(Exception e){WidgetDiagnostics.Write("services-resource-error",id,e);try{DevStackWebContent.Deny(args);}catch{}}
        finally{def.Complete();}
    }
    internal static void Message(WidgetMessageReceivedArgs args)
    {
        if(args.WidgetContext.DefinitionId!=WidgetCards.ServicesId||args.Message.Length>4096)return;
        var id=args.WidgetContext.Id;string kind,verb="",service="",token="";
        try
        {
            using var parsed=JsonDocument.Parse(args.Message);
            using var unwrap=parsed.RootElement.ValueKind==JsonValueKind.String?JsonDocument.Parse(parsed.RootElement.GetString()??"null"):null;
            var msg=unwrap?.RootElement??parsed.RootElement;
            if(msg.ValueKind!=JsonValueKind.Object||msg.GetProperty("protocol").GetInt32()!=1)return;
            kind=msg.GetProperty("kind").GetString()??"";
            var nonce=msg.GetProperty("session").GetString();var client=msg.GetProperty("client").GetString();long seq=msg.GetProperty("seq").GetInt64();
            if(client is null||client.Length<1||client.Length>80||seq<=0)return;
            lock(Gate)
            {
                if(!Items.TryGetValue(id,out var w)||nonce!=w.Nonce)return;
                if(kind is not("ready" or "hidden" or "action" or "seen" or "diagnostic"))return;
                if(kind=="ready")
                {
                    if(w.Client!=client){w.Client=client;w.Seq=0;w.Confirmation=null;w.Service=null;}
                    w.Ready=true;w.Hidden=false;
                }
                if(!w.Ready||w.Client!=client||seq<=w.Seq)return;
                w.Seq=seq;_last=DateTimeOffset.UtcNow;
                if(kind=="hidden"){w.Hidden=true;w.Confirmation=null;w.Service=null;}
                if(kind=="action")
                {
                    if(!w.Visible)return;
                    verb=msg.GetProperty("verb").GetString()??"";
                    if(verb is not ("refresh" or "open-new" or "open-projects")&&(!w.Authenticated||!Live()||_data?.Running!=true||_data.Integration!="ok"))return;
                    service=msg.TryGetProperty("service",out var s)&&s.ValueKind==JsonValueKind.String?s.GetString()??"":"";
                    token=msg.TryGetProperty("confirmation",out var t)&&t.ValueKind==JsonValueKind.String?t.GetString()??"":"";
                }
            }
        }
        catch(Exception e)when(e is JsonException or KeyNotFoundException or InvalidOperationException or FormatException or OverflowException)
        {WidgetDiagnostics.Write("services-message-rejected",id,e);return;}
        if(kind=="ready"){Visibility();Push(id);Refresh();}
        else if(kind=="hidden"){Push(id);Visibility();}
        else if(kind=="action")Action(id,verb,service,token);
    }
    private static void Action(string id,string verb,string service,string confirmation)
    {
        bool run=false;CancellationToken token=default;long epoch=0;
        lock(Gate)
        {
            if(!Items.TryGetValue(id,out var w)||!w.Visible)return;
            if(verb=="refresh"){w.Message="";Refresh();return;}
            if(verb is "open-new" or "open-projects")
            {
                if(!w.Ready||!w.Authenticated||!Live()||_data?.Running!=true||!_data.Services.Where(s=>s.Id is "nginx" or "php").All(s=>s.Reachable==true))return;
                try{DevStackNavigation.Open(verb);}catch(Exception e){WidgetDiagnostics.Write("services-navigation-error",id,e);}
                return;
            }
            if(_busy is not null||!w.Authenticated||!w.Ready||!Live()||_data?.Running!=true||_data.Integration!="ok")return;
            if(verb=="restart-cancel"){w.Confirmation=null;w.Service=null;}
            else
            {
                if(!DevStackData.Ids.Contains(service)||_data.Services.FirstOrDefault(s=>s.Id==service)?.UnitActive is null)return;
                if(verb=="restart-ask"){w.Confirmation=Token();w.Service=service;w.Until=DateTimeOffset.UtcNow.AddSeconds(30);w.Message="";}
                else if(verb=="restart-confirm")
                {
                    if(w.Confirmation is null||w.Confirmation!=confirmation||w.Service!=service||w.Until<=DateTimeOffset.UtcNow)return;
                    w.Confirmation=null;w.Service=null;w.Message="";_busy=service;epoch=++_epoch;token=_visible?.Token??new CancellationToken(true);run=true;
                }
                else return;
            }
        }
        PushAll();if(run)_=Task.Run(()=>Restart(id,service,epoch,token));
    }
    private static async Task Restart(string id,string service,long epoch,CancellationToken visible)
    {
        string message="";
        try
        {
            var state=await StateProbe.CaptureWslOnlyAsync(visible);
            lock(Gate)if(visible.IsCancellationRequested||epoch!=_epoch||!Items.TryGetValue(id,out var w)||!w.Visible)return;
            if(state.WslRunning!=true){message="Ubuntu nie jest uruchomione. Restart nie został wykonany.";return;}
            // Once accepted, one bounded HTTP command may finish after closing.
            // No wsl -d, no unit argument from the page, no automatic retry.
            bool ok=await DevStackData.RestartAsync(service,CancellationToken.None);
            message=ok?"Restart zakończony. Weryfikowanie usług.":"Nie potwierdzono restartu. Sprawdź stan przed ponowieniem.";
        }
        catch(OperationCanceledException){message="Operacja przerwana. Sprawdź stan przed ponowieniem.";}
        catch(Exception e){WidgetDiagnostics.Write("services-restart-error",id,e);message="Brak potwierdzenia restartu. Polecenie nie zostało ponowione.";}
        finally
        {
            lock(Gate){if(Items.TryGetValue(id,out var w))w.Message=message;_busy=null;_fresh=false;++_epoch;_last=DateTimeOffset.UtcNow;}
            PushAll();Refresh();WidgetProvider.Wake.Set();
        }
    }
    private static void Refresh()
    {
        lock(Gate)
        {
            if(_visible is null||!Items.Values.Any(w=>w.Visible)||_busy is not null)return;
            _pending=true;if(_worker)return;_worker=true;
        }
        _=Task.Run(Drain);
    }
    private static async Task Drain()
    {
        while(true)
        {
            CancellationToken token;long epoch;
            lock(Gate){if(!_pending||_visible is null){_worker=false;return;}_pending=false;token=_visible.Token;epoch=_epoch;}
            try
            {
                var wsl=await StateProbe.CaptureWslOnlyAsync(token);
                var result=await DevStackData.CaptureAsync(wsl.WslRunning,token);
                lock(Gate){if(token.IsCancellationRequested||_visible?.Token!=token||epoch!=_epoch||_busy is not null)continue;_data=result;_fresh=true;}
                PushAll();
            }
            catch(OperationCanceledException){}
            catch(Exception e){WidgetDiagnostics.Write("services-read-error",error:e);}
        }
    }
}
