using System.Collections.Concurrent;
using System.Text.Json;
namespace Windows.Storage.Streams
{
 public sealed class RandomAccessStreamReference
 { public static object CreateFromUri(Uri uri) => uri; }
}
namespace Microsoft.Windows.Widgets.Providers
{
 public interface IWidgetProvider { }
 public interface IWidgetProviderMessage { }
 public interface IWidgetResourceProvider { }
 public enum WidgetSize { Large }
 public sealed record WidgetContext(string Id, string DefinitionId, bool IsActive = true, WidgetSize Size = WidgetSize.Large);
 public sealed record WidgetContextChangedArgs(WidgetContext WidgetContext);
 public sealed record WidgetActionInvokedArgs(WidgetContext WidgetContext, string Verb);
 public sealed record WidgetMessageReceivedArgs(WidgetContext WidgetContext, string Message);
 public sealed record WidgetResourceRequest(string Uri);
 public sealed class WidgetResourceRequestedArgs(WidgetContext context, string uri)
 { public WidgetContext WidgetContext => context; public WidgetResourceRequest Request { get; } = new(uri); public WidgetResourceResponse? Response { get; set; } }
 public sealed class WidgetResourceResponse(object content, string reason, int status)
 { public object Content => content; public int StatusCode => status; public string ReasonPhrase => reason; public Dictionary<string,string> Headers { get; } = new(); }
 public sealed class WidgetUpdateRequestOptions(string id)
 { public string Id => id; public string Template { get; set; } = ""; public string Data { get; set; } = ""; public bool IsPlaceholderContent { get; set; } }
 public sealed record WidgetInfo(WidgetContext WidgetContext);
 public sealed class WidgetManager
 {
  private static readonly WidgetManager Instance = new();
  public static readonly ConcurrentDictionary<string,string> Templates = new();
  public static readonly ConcurrentDictionary<string,string> Messages = new();
  public static WidgetManager GetDefault() => Instance;
  public WidgetInfo[] GetWidgetInfos() => Array.Empty<WidgetInfo>();
  public void UpdateWidget(WidgetUpdateRequestOptions options) => Templates[options.Id] = options.Template;
  public void SendMessageToContent(string id, string message) => Messages[id] = message;
 }
}
namespace DevStack.Widgets
{
 internal static class WidgetCards { internal const string WslId="DevStack_WSL",ServicesId="DevStack_Services"; }
 internal static class WidgetDesign
 {
  internal static string NormalizeSize(string? size)=>"large";
  internal static string Initial(string definition,string size)=>"{\"type\":\"AdaptiveCard\",\"body\":[]}";
  internal static string DevStack(StateProbe.Snapshot state,string size)=>JsonSerializer.Serialize(new {type="AdaptiveCard",body=new[]{new{type="TextBlock",text=state.WslRunning?.ToString()??"unknown"}}});
 }
 internal static class StateProbe
 {
  internal const string Distro="Ubuntu-26.04";
  internal static bool? Running=false;
  internal static int Reads,Starts,Stops;
  internal sealed record Snapshot(bool? WslRunning,string WslError,bool? Nginx,bool? PhpFpm,bool? MariaDb,bool? Redis,DateTimeOffset CheckedAt);
  internal static Task<Snapshot> CaptureWslOnlyAsync(CancellationToken ct=default)
  { ct.ThrowIfCancellationRequested();Interlocked.Increment(ref Reads);return Task.FromResult(new Snapshot(Running,"",null,null,null,null,DateTimeOffset.Now)); }
  internal static Task<Snapshot> CaptureServicesAfterWslAsync(Snapshot s,CancellationToken ct) {ct.ThrowIfCancellationRequested();return Task.FromResult(s);}
  internal static async Task<(int ExitCode,string Output)> StartAsync(){Interlocked.Increment(ref Starts);await Task.Delay(40);Running=true;return(0,"");}
  internal static async Task<(int ExitCode,string Output)> StopAsync(){Interlocked.Increment(ref Stops);await Task.Delay(40);Running=false;return(0,"");}
 }
}
