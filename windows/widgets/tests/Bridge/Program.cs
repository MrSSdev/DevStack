using System.Collections;
using System.Reflection;
using System.Text.Json;
using DevStack.Widgets;
using Microsoft.Windows.Widgets.Providers;

var provider=new WidgetProvider();
var context=new WidgetContext("test-wsl",WidgetCards.WslId);
int checks=0;long sequence=0;
void Assert(bool value,string label){if(!value)throw new InvalidOperationException(label);checks++;Console.WriteLine("PASS: "+label);}
JsonElement State()=>JsonDocument.Parse(WidgetManager.Messages[context.Id]).RootElement.Clone();
async Task Wait(Func<bool> predicate,string label){for(int i=0;i<250;i++){if(predicate()){checks++;return;}await Task.Delay(10);}throw new Exception("Timed out: "+label);}
WidgetProvider.Start();provider.CreateWidget(context);
var template=JsonDocument.Parse(WidgetManager.Templates[context.Id]);
var url=template.RootElement.GetProperty("metadata").GetProperty("webUrl").GetString()!;
var session=WslWebContent.Query(new Uri(url.Replace("${$host.hostTheme}","dark").Replace("${$host.isUserContextAuthenticated}","false")))["session"];
async Task<WidgetResourceRequestedArgs> Resource(bool auth,bool read=false)
{
 var args=new WidgetResourceRequestedArgs(context,WslWebContent.Origin+"/wsl/index.html?v="+WslWebContent.Version+"&session="+session+"&auth="+auth.ToString().ToLowerInvariant());
 if(read)args.Request.Headers["X-DevStack-View"]="state";
 provider.OnResourceRequested(args);await args.Completion.Task.WaitAsync(TimeSpan.FromSeconds(5));
 Assert(args.Response?.StatusCode==200,"packaged resource response");return args;
}
void Message(string kind,string? verb=null,string? confirmation=null,string? nonce=null,bool wrapped=false)
{
 var text=JsonSerializer.Serialize(new{protocol=1,kind,session=nonce??session,client="test-client",seq=++sequence,verb,confirmation});
 provider.OnMessageReceived(new WidgetMessageReceivedArgs(context,wrapped?JsonSerializer.Serialize(text):text));
}
var page=await Resource(false);
Assert(page.Response!.Content.ToString()!.Contains("id=\"wsl-bootstrap\""),"real bootstrap embedded in initial page");
Assert(page.Response.Headers["Content-Security-Policy"].Contains("sha256-"),"hash CSP delivered");
Message("ready");await Wait(()=>WidgetManager.Messages.ContainsKey(context.Id)&&State().GetProperty("phase").GetString()=="offline","initial offline");
Assert(!State().GetProperty("canControl").GetBoolean(),"unauthenticated host is noninteractive");Message("action","start");await Task.Delay(60);Assert(StateProbe.Starts==0,"unauthenticated Start denied");
await Resource(true);Message("ready",wrapped:true);await Wait(()=>State().GetProperty("canControl").GetBoolean(),"JSON-string envelope accepted without weakening session check");
Message("action","start",nonce:"wrong");await Task.Delay(60);Assert(StateProbe.Starts==0,"wrong session denied");
Message("action","start");Message("action","start");await Wait(()=>State().GetProperty("phase").GetString()=="running","start complete");Assert(StateProbe.Starts==1,"duplicate Start coalesced");
Message("action","stop-confirm","forged");await Task.Delay(50);Assert(StateProbe.Stops==0,"Stop without confirmation denied");
Message("action","stop-ask");await Wait(()=>State().GetProperty("phase").GetString()=="confirm","ask Stop");
Message("action","stop-cancel");Assert(StateProbe.Running==true&&StateProbe.Stops==0,"Cancel does not stop");
Message("action","stop-ask");
var table=(IDictionary)typeof(WidgetProvider).GetField("Widgets",BindingFlags.NonPublic|BindingFlags.Static)!.GetValue(null)!;
var entry=table[context.Id]!;entry.GetType().GetProperty("ConfirmUntil")!.SetValue(entry,DateTimeOffset.UtcNow.AddSeconds(-1));
Message("action","stop-confirm",State().GetProperty("confirmation").GetString());await Task.Delay(40);Assert(StateProbe.Stops==0,"expired confirmation denied");
Message("action","stop-ask");var token=State().GetProperty("confirmation").GetString();provider.Deactivate(context.Id);Message("action","stop-confirm",token);await Task.Delay(80);Assert(StateProbe.Stops==0,"hidden Stop denied");
var reads=StateProbe.Reads;Message("action","refresh");await Task.Delay(80);Assert(StateProbe.Reads==reads,"no reads for inactive card");
await Resource(true);Message("ready");await Task.Delay(60);Assert(StateProbe.Reads==reads,"early ready cannot start background probes");
provider.Activate(context);await Wait(()=>State().GetProperty("phase").GetString()=="running"&&State().GetProperty("canControl").GetBoolean(),"early ready remembered: activation pushes without another ready");
var before=WidgetManager.Updates;provider.Deactivate(context.Id);provider.Activate(context);await Task.Delay(80);Assert(WidgetManager.Updates==before,"reopen does not republish WSL URL");
await Resource(true);var read=await Resource(true,true);
using(var data=JsonDocument.Parse((string)read.Response!.Content))
{Assert(data.RootElement.GetProperty("phase").GetString()=="running","read-only fallback returns measured WSL state");Assert(!data.RootElement.GetProperty("canControl").GetBoolean(),"resource response cannot grant control");Assert(data.RootElement.GetProperty("confirmation").ValueKind==JsonValueKind.Null,"resource never returns confirmation capability");}
Message("ready");await Wait(()=>State().GetProperty("canControl").GetBoolean(),"native control re-established after context read");
Message("action","stop-ask");token=State().GetProperty("confirmation").GetString();Message("action","stop-confirm",token);Message("action","stop-confirm",token);await Wait(()=>State().GetProperty("phase").GetString()=="offline","confirmed Stop");Assert(StateProbe.Stops==1,"confirmed Stop executes once");
provider.Deactivate(context.Id);await Task.Delay(60);WidgetProvider.Stop();before=WidgetManager.Updates;
WidgetManager.SavedInfos=new[]{new WidgetInfo(context,WidgetManager.Templates[context.Id])};
table.Clear();typeof(WidgetProvider).GetField("_recovered",BindingFlags.NonPublic|BindingFlags.Static)!.SetValue(null,false);
provider=new WidgetProvider();WidgetProvider.Start();provider.Activate(context);await Task.Delay(80);
Assert(WidgetManager.Updates==before,"provider recovery retains original URL without republishing");
Assert(table[context.Id]!.GetType().GetProperty("Nonce")!.GetValue(table[context.Id])!.Equals(session),"provider restart keeps host-issued session");
Assert(WslViewCache.Load()?.Running==false,"last measured offline state survives cache recovery");
await Resource(true);Message("ready");await Wait(()=>State().GetProperty("canControl").GetBoolean(),"host context rechecked after recovery");
StateProbe.Running=null;Message("action","refresh");await Wait(()=>State().GetProperty("phase").GetString()=="error","unknown is not offline");Message("action","start");Assert(StateProbe.Starts==1,"unknown state cannot start");
foreach(var uri in new[]{"https://evil.invalid/wsl/index.html",WslWebContent.Origin+"/private-file",WslWebContent.Origin+"/wsl/index.html?session=forged"})
{var bad=new WidgetResourceRequestedArgs(context,uri);provider.OnResourceRequested(bad);await bad.Completion.Task.WaitAsync(TimeSpan.FromSeconds(5));Assert(bad.Response?.StatusCode==403,"foreign or unknown resource denied without network fallback");}
var valid=new WslResources.Reading(1,StateProbe.Distro,"linux-procfs","shared-wsl-kernel",7,1800,23000,12600,256000,5040);
Assert(WslResources.Valid(valid),"valid readings");Assert(!WslResources.Valid(valid with{CpuPercent=101}),"out-of-range CPU rejected");Assert(!WslResources.Valid(valid with{MemoryUsedBytes=24000}),"invalid memory rejected");Assert(!WslResources.Valid(valid with{Distro="different"}),"wrong distro rejected");
var off=await WslResources.CaptureAsync(false,new CancellationToken(true));Assert(off.Status=="offline"&&off.Metrics is null,"offline never makes I/O even with canceled token");
var log=File.ReadAllText(Path.Combine(WidgetDiagnostics.DirectoryPath,"wsl-bridge.log"));Assert(!log.Contains(session),"diagnostics never contain session credentials");Assert(log.Contains("hello-before-activate"),"ordering case recorded without payload");
WidgetProvider.Stop();Console.WriteLine($"PASS: {checks} real provider policy checks with mocked WinRT/WSL; physical host still needs validation.");
