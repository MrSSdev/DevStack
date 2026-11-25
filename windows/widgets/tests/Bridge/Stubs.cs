using System.Collections.Concurrent;
using System.Text.Json;
namespace Windows.Storage.Streams
{
 public sealed class InMemoryRandomAccessStream : MemoryStream
 { public Stream GetOutputStreamAt(ulong position) { Position=(long)position;return this; } public void Seek(ulong position){Position=(long)position;} }
 public sealed class DataWriter(Stream stream) : IDisposable
 { private byte[] bytes=Array.Empty<byte>();public void WriteBytes(byte[] data)=>bytes=data;public async Task<uint> StoreAsync(){await stream.WriteAsync(bytes);return(uint)bytes.Length;}public void DetachStream(){}public void Dispose(){} }
 public sealed class RandomAccessStreamReference
 { public static object CreateFromUri(Uri uri)=>uri;public static object CreateFromStream(InMemoryRandomAccessStream stream)=>System.Text.Encoding.UTF8.GetString(stream.ToArray()); }
}
namespace Microsoft.Windows.Widgets.Providers
{
 public interface IWidgetProvider{}
 public interface IWidgetProviderMessage{}
 public interface IWidgetResourceProvider{}
 public enum WidgetSize{Large}
 public sealed record WidgetContext(string Id,string DefinitionId,bool IsActive=true,WidgetSize Size=WidgetSize.Large);
 public sealed record WidgetContextChangedArgs(WidgetContext WidgetContext);
 public sealed record WidgetActionInvokedArgs(WidgetContext WidgetContext,string Verb);
 public sealed record WidgetMessageReceivedArgs(WidgetContext WidgetContext,string Message);
 public sealed class WidgetResourceRequest(string uri)
 { public string Uri=>uri;public string Method{get;set;}="GET";public Dictionary<string,string> Headers{get;}=new(StringComparer.OrdinalIgnoreCase); }
 public sealed class WidgetResourceRequestedArgs(WidgetContext context,string uri)
 {
  public WidgetContext WidgetContext=>context;public WidgetResourceRequest Request{get;}=new(uri);public WidgetResourceResponse? Response{get;set;}
  public TaskCompletionSource Completion{get;}=new(TaskCreationOptions.RunContinuationsAsynchronously);
  public TestDeferral GetDeferral()=>new(Completion);
 }
 public sealed class TestDeferral(TaskCompletionSource completion){public void Complete()=>completion.TrySetResult();}
 public sealed class WidgetResourceResponse(object content,string reason,int status)
 { public object Content=>content;public int StatusCode=>status;public string ReasonPhrase=>reason;public Dictionary<string,string> Headers{get;}=new(); }
 public sealed class WidgetUpdateRequestOptions(string id)
 { public string Id=>id;public string Template{get;set;}="";public string Data{get;set;}="";public bool IsPlaceholderContent{get;set;} }
 public sealed record WidgetInfo(WidgetContext WidgetContext,string Template="");
 public sealed class WidgetManager
 {
  private static readonly WidgetManager Instance=new();
  public static readonly ConcurrentDictionary<string,string> Templates=new(),Messages=new();
  public static WidgetInfo[] SavedInfos=Array.Empty<WidgetInfo>();
  public static int Updates;
  public static WidgetManager GetDefault()=>Instance;
  public WidgetInfo[] GetWidgetInfos()=>SavedInfos;
  public void UpdateWidget(WidgetUpdateRequestOptions options){Interlocked.Increment(ref Updates);Templates[options.Id]=options.Template;}
  public void SendMessageToContent(string id,string message)=>Messages[id]=message;
 }
}
namespace DevStack.Widgets
{
 internal static class WidgetCards{internal const string WslId="DevStack_WSL",ServicesId="DevStack_Services";}
 internal static class WidgetDesign
 {
  internal static string NormalizeSize(string? size)=>"large";
  internal static string Initial(string definition,string size)=>"{\"type\":\"AdaptiveCard\",\"body\":[]}";
  internal static string DevStack(StateProbe.Snapshot state,string size)=>JsonSerializer.Serialize(new{type="AdaptiveCard",body=new[]{new{type="TextBlock",text=state.WslRunning?.ToString()??"unknown"}}});
 }
 internal static class StateProbe
 {
  internal const string Distro="Ubuntu-26.04";
  internal static bool? Running=false;internal static int Reads,Starts,Stops;
  internal sealed record Snapshot(bool? WslRunning,string WslError,bool? Nginx,bool? PhpFpm,bool? MariaDb,bool? Redis,DateTimeOffset CheckedAt);
  internal static Task<Snapshot> CaptureWslOnlyAsync(CancellationToken ct=default)
  {ct.ThrowIfCancellationRequested();Interlocked.Increment(ref Reads);return Task.FromResult(new Snapshot(Running,"",null,null,null,null,DateTimeOffset.Now));}
  internal static Task<Snapshot> CaptureServicesAfterWslAsync(Snapshot s,CancellationToken ct){ct.ThrowIfCancellationRequested();return Task.FromResult(s);}
  internal static async Task<(int ExitCode,string Output)> StartAsync(){Interlocked.Increment(ref Starts);await Task.Delay(40);Running=true;return(0,"");}
  internal static async Task<(int ExitCode,string Output)> StopAsync(){Interlocked.Increment(ref Stops);await Task.Delay(40);Running=false;return(0,"");}
 }
}

namespace DevStack.Widgets
{
 internal static class DevStackNavigation { internal static int Opens; internal static void Open(string route){Opens++;} }
 internal static class DevStackData
 {
  internal static readonly string[] Ids={"nginx","php","mariadb","redis"};
  internal sealed record Service(string Id,bool? Reachable,double? LatencyMs,long? MemoryBytes,double? CpuPercent,bool? UnitActive,bool Selected=true);
  internal sealed record Result(bool? Running,string Integration,Service[] Services,int? ProjectCount,DateTimeOffset CheckedAt,bool SelectionKnown=false);
  internal static int Restarts,Reads;internal static string Last="";internal static string? InactiveService,UnreachableService;internal static bool RestartOk=true,IntegrationOk=true;
  internal static Task<Result> CaptureAsync(bool? running,CancellationToken ct)
  { ct.ThrowIfCancellationRequested();Reads++;return Task.FromResult(new Result(running,running==true?(IntegrationOk?"ok":"error"):"offline",Ids.Select(id=>new Service(id,running==true?id!=UnreachableService:running,4,1000000,0.5,running==true?id!=InactiveService:running)).ToArray(),running==true?3:null,DateTimeOffset.Now)); }
  internal static async Task<bool> RestartAsync(string id,CancellationToken ct){Restarts++;Last=id;await Task.Delay(60);return RestartOk;}
 }
}
