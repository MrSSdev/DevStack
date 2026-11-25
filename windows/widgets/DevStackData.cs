using System.Diagnostics;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace DevStack.Widgets;

/// <summary>Windows-only network reads; service actions use a separate localhost credential.</summary>
internal static class DevStackData
{
    internal static readonly string[] Ids = { "nginx", "php", "mariadb", "redis" };
    internal sealed record Service(string Id, bool? Reachable, double? LatencyMs, long? MemoryBytes, double? CpuPercent, bool? UnitActive,
        string? Name=null, string? Description=null, string? Probe=null, int Port=0, bool Selected=true);
    internal sealed record Result(bool? Running, string Integration, Service[] Services, int? ProjectCount, DateTimeOffset CheckedAt, bool SelectionKnown=false);
    private sealed record Settings(int Schema, string Distro, int ReadPort, int ControlPort, string ReadToken, string ControlToken);
    private sealed record Unit(string Id, bool? Active, long? MemoryBytes, double? CpuPercent,
        string? Name=null, string? Description=null, string? Probe=null, int Port=0, bool Selected=true);
    private sealed record Response(int Schema, string Distro, string Source, int? ProjectCount, Unit[]? Services);
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private static readonly HttpClient Client = new(new HttpClientHandler { AllowAutoRedirect=false, UseCookies=false, UseProxy=false })
        { Timeout=TimeSpan.FromSeconds(16), MaxResponseContentBufferSize=32768 };
    internal static Result Empty(bool? running, string status) => new(running,status,Ids.Select(id=>new Service(id,null,null,null,null,null)).ToArray(),null,DateTimeOffset.Now);
    private static bool Token(string? text) => text?.Length==64 && text.All(c=>"0123456789abcdef".Contains(c));
    private static async Task<Settings?> Config(CancellationToken ct)
    {
        var path=Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),"DevStack","Widgets","services.json");
        if(!File.Exists(path)||new FileInfo(path).Length>4096)return null;
        var settings=JsonSerializer.Deserialize<Settings>(await File.ReadAllTextAsync(path,ct),Json);
        return settings is not null && settings.Schema==1 && settings.Distro==StateProbe.Distro && settings.ReadPort==43872 && settings.ControlPort==43873 && Token(settings.ReadToken)&&Token(settings.ControlToken)?settings:null;
    }
    internal static bool ValidId(string? id) => id is not null && Regex.IsMatch(id, @"\A[a-z][a-z0-9-]{0,31}\z", RegexOptions.CultureInvariant);
    internal static bool ValidService(Service s) => ValidId(s.Id) &&
        (s.LatencyMs is null || double.IsFinite(s.LatencyMs.Value)&&s.LatencyMs>=0&&s.LatencyMs<=60000) &&
        (s.MemoryBytes is null || s.MemoryBytes>=0&&s.MemoryBytes<=(1L<<50)) &&
        (s.CpuPercent is null || double.IsFinite(s.CpuPercent.Value)&&s.CpuPercent>=0&&s.CpuPercent<=100000);
    private static bool ValidResponse(Response? response)
    {
        if(response is null||response.Distro!=StateProbe.Distro||response.Source!="systemd-cgroup"||
            response.Services is null||response.Services.Length>24||
            response.Services.Select(s=>s.Id).Distinct().Count()!=response.Services.Length||
            !(response.ProjectCount is null||response.ProjectCount>=0&&response.ProjectCount<=100000)||
            !response.Services.All(s=>ValidService(new(s.Id,null,null,s.MemoryBytes,s.CpuPercent,s.Active))))return false;
        if(response.Schema==1)return response.Services.Length==4&&response.Services.Select(s=>s.Id).Order().SequenceEqual(Ids.Order());
        return response.Schema==2&&response.Services.All(s=>s.Name is {Length:>0 and <=40}&&s.Description is {Length:>0 and <=150}
            &&(s.Probe is "https" or "tcp" or "unit")&&s.Port>=0&&s.Port<=65535&&(s.Probe!="tcp"||s.Port>0));
    }
    private static async Task<Response?> ReadMetrics(Settings settings,CancellationToken ct)
    {
        using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);timeout.CancelAfter(TimeSpan.FromSeconds(6));
        foreach(var route in new[]{"/v2/services","/v1/services"})
        {
            using var request=new HttpRequestMessage(HttpMethod.Get,"http://127.0.0.1:43872"+route);
            request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",settings.ReadToken);
            using var reply=await Client.SendAsync(request,timeout.Token);
            if(reply.StatusCode==System.Net.HttpStatusCode.NotFound&&route=="/v2/services")continue;
            if(!reply.IsSuccessStatusCode||reply.Content.Headers.ContentType?.MediaType!="application/json")return null;
            var result=JsonSerializer.Deserialize<Response>(await reply.Content.ReadAsStringAsync(timeout.Token),Json);
            return ValidResponse(result)?result:null;
        }
        return null;
    }
    internal static async Task<Result> CaptureAsync(bool? running,CancellationToken ct)
    {
        if(running!=true)return Empty(running,running==false?"offline":"unavailable");
        ct.ThrowIfCancellationRequested();
        Response? measured=null;string status="not-installed";
        try
        {
            var cfg=await Config(ct);
            if(cfg is not null)
            {
                status="error";measured=await ReadMetrics(cfg,ct);
                if(measured is not null)status="ok";
            }
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){throw;}
        catch(Exception e)when(e is IOException or UnauthorizedAccessException or JsonException or HttpRequestException or OperationCanceledException){status="error";}
        // Never accept an upstream URL or hostname. Only the fixed dashboard
        // HTTPS endpoint, a bounded loopback TCP port, or systemd state.
        var http=Dashboard(ct);
        if(measured?.Schema==2)
        {
            var tasks=measured.Services!.Select(async s=>{
                Probe probe;
                if(s.Id=="nginx"||s.Id=="php") {var web=await http;probe=s.Id=="nginx"?web.Nginx:web.Php;}
                else if(s.Selected&&s.Probe=="tcp")probe=await Tcp(s.Port,ct);
                else if(s.Selected&&s.Probe=="https")probe=(await http).Php;
                else if(s.Selected&&s.Probe=="unit")return new Service(s.Id,s.Active,null,s.MemoryBytes,s.CpuPercent,s.Active,s.Name,s.Description,s.Probe,s.Port,s.Selected);
                else return new Service(s.Id,null,null,s.MemoryBytes,s.CpuPercent,s.Active,s.Name,s.Description,s.Probe,s.Port,s.Selected);
                return new Service(s.Id,probe.Ok,probe.Ms,s.MemoryBytes,s.CpuPercent,s.Active,s.Name,s.Description,s.Probe,s.Port,s.Selected);
            }).ToArray();
            var services=await Task.WhenAll(tasks);await http;ct.ThrowIfCancellationRequested();
            return new(running,status,services,measured.ProjectCount,DateTimeOffset.Now,true);
        }
        var db=Tcp(3306,ct);var redis=Tcp(6379,ct);
        await Task.WhenAll(http,db,redis);ct.ThrowIfCancellationRequested();
        var oldWeb=await http;var database=await db;var cache=await redis;
        var results=new[]{oldWeb.Nginx,oldWeb.Php,database,cache};
        return new(running,status,Ids.Select((id,index)=>
        {
            var metric=measured?.Services?.FirstOrDefault(s=>s.Id==id);
            return new Service(id,results[index].Ok,results[index].Ms,metric?.MemoryBytes,metric?.CpuPercent,metric?.Active);
        }).ToArray(),measured?.ProjectCount,DateTimeOffset.Now);
    }
    private sealed record Probe(bool Ok,double? Ms);
    private static async Task<(Probe Nginx,Probe Php)> Dashboard(CancellationToken ct)
    {
        using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);timeout.CancelAfter(TimeSpan.FromSeconds(3));
        var watch=Stopwatch.StartNew();
        try
        {
            using var reply=await Client.GetAsync("https://devstack.test/api/session",HttpCompletionOption.ResponseContentRead,timeout.Token);
            double elapsed=Math.Round(watch.Elapsed.TotalMilliseconds,1);
            var payload=await reply.Content.ReadAsStringAsync(timeout.Token);
            bool php=false;
            if(reply.IsSuccessStatusCode)try{using var json=JsonDocument.Parse(payload);php=json.RootElement.TryGetProperty("authenticated",out var auth)&&auth.ValueKind is JsonValueKind.True or JsonValueKind.False;}catch(JsonException){}
            // Both timings are the same end-to-end HTTPS request, not separate server processing times.
            return(new(true,elapsed),new(php,php?elapsed:null));
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){throw;}
        catch(HttpRequestException){return(new(false,null),new(false,null));}
        catch(OperationCanceledException){return(new(false,null),new(false,null));}
    }
    private static async Task<Probe> Tcp(int port,CancellationToken ct)
    {
        using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);timeout.CancelAfter(TimeSpan.FromMilliseconds(1400));
        using var socket=new TcpClient();var watch=Stopwatch.StartNew();
        try{await socket.ConnectAsync("127.0.0.1",port,timeout.Token);return new(true,Math.Round(watch.Elapsed.TotalMilliseconds,1));}
        catch(OperationCanceledException)when(ct.IsCancellationRequested){throw;}
        catch(Exception e)when(e is SocketException or OperationCanceledException){return new(false,null);}
    }
    internal static async Task<bool> RestartAsync(string id,CancellationToken ct)
    {
        if(!ValidId(id))throw new InvalidOperationException("Unknown service");
        var cfg=await Config(ct)??throw new InvalidOperationException("Integration unavailable");
        using var request=new HttpRequestMessage(HttpMethod.Post,"http://127.0.0.1:43873/v1/restart");
        request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",cfg.ControlToken);
        request.Content=new StringContent(JsonSerializer.Serialize(new{service=id,requestId=Guid.NewGuid().ToString("N")}),Encoding.UTF8,"application/json");
        // No automatic retry, redirects or commands through read-only endpoints.
        using var reply=await Client.SendAsync(request,ct);
        if(!reply.IsSuccessStatusCode||reply.Content.Headers.ContentType?.MediaType!="application/json")return false;
        using var result=JsonDocument.Parse(await reply.Content.ReadAsStringAsync(ct));
        return result.RootElement.TryGetProperty("ok",out var ok)&&ok.ValueKind==JsonValueKind.True;
    }
}
