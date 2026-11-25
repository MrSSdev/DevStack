using System.Diagnostics;
using System.Net.Http.Headers;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;

namespace DevStack.Widgets;

/// <summary>Windows-only network reads; service actions use a separate localhost credential.</summary>
internal static class DevStackData
{
    internal static readonly string[] Ids = { "nginx", "php", "mariadb", "redis" };
    internal sealed record Service(string Id, bool? Reachable, double? LatencyMs, long? MemoryBytes, double? CpuPercent, bool? UnitActive);
    internal sealed record Result(bool? Running, string Integration, Service[] Services, int? ProjectCount, DateTimeOffset CheckedAt);
    private sealed record Settings(int Schema, string Distro, int ReadPort, int ControlPort, string ReadToken, string ControlToken);
    private sealed record Unit(string Id, bool? Active, long? MemoryBytes, double? CpuPercent);
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
    internal static bool ValidService(Service s) => Ids.Contains(s.Id) &&
        (s.LatencyMs is null || double.IsFinite(s.LatencyMs.Value)&&s.LatencyMs>=0&&s.LatencyMs<=60000) &&
        (s.MemoryBytes is null || s.MemoryBytes>=0&&s.MemoryBytes<=(1L<<50)) &&
        (s.CpuPercent is null || double.IsFinite(s.CpuPercent.Value)&&s.CpuPercent>=0&&s.CpuPercent<=100000);
    internal static async Task<Result> CaptureAsync(bool? running,CancellationToken ct)
    {
        if(running!=true)return Empty(running,running==false?"offline":"unavailable");
        ct.ThrowIfCancellationRequested();
        var http=Dashboard(ct); var db=Tcp(3306,ct); var redis=Tcp(6379,ct);
        Response? measured=null;string status="not-installed";
        try
        {
            var cfg=await Config(ct);
            if(cfg is not null)
            {
                using var timeout=CancellationTokenSource.CreateLinkedTokenSource(ct);timeout.CancelAfter(TimeSpan.FromSeconds(6));
                using var request=new HttpRequestMessage(HttpMethod.Get,"http://127.0.0.1:43872/v1/services");
                request.Headers.Authorization=new AuthenticationHeaderValue("Bearer",cfg.ReadToken);
                using var reply=await Client.SendAsync(request,timeout.Token);
                status="error";
                if(reply.IsSuccessStatusCode && reply.Content.Headers.ContentType?.MediaType=="application/json")
                {
                    measured=JsonSerializer.Deserialize<Response>(await reply.Content.ReadAsStringAsync(timeout.Token),Json);
                    if(measured is not null && measured.Schema==1 && measured.Distro==StateProbe.Distro && measured.Source=="systemd-cgroup" &&
                        measured.Services?.Length==4 && measured.Services.Select(s=>s.Id).Distinct().Order().SequenceEqual(Ids.Order()) &&
                        (measured.ProjectCount is null||measured.ProjectCount>=0&&measured.ProjectCount<=100000) &&
                        measured.Services.All(s=>ValidService(new(s.Id,null,null,s.MemoryBytes,s.CpuPercent,s.Active))))status="ok";
                    else measured=null;
                }
            }
        }
        catch(OperationCanceledException)when(ct.IsCancellationRequested){throw;}
        catch(Exception e)when(e is IOException or UnauthorizedAccessException or JsonException or HttpRequestException or OperationCanceledException){status="error";}
        await Task.WhenAll(http,db,redis);ct.ThrowIfCancellationRequested();
        var web=await http;var database=await db;var cache=await redis;
        var results=new[]{web.Nginx,web.Php,database,cache};
        return new(running,status,Ids.Select((id,i)=>
        {
            var metric=measured?.Services?.FirstOrDefault(s=>s.Id==id);
            return new Service(id,results[i].Ok,results[i].Ms,metric?.MemoryBytes,metric?.CpuPercent,metric?.Active);
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
        if(!Ids.Contains(id))throw new InvalidOperationException("Unknown service");
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
