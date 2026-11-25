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


// Services widget uses its own session, origin and command allowlist.
StateProbe.Running=true;
var serviceContext=new WidgetContext("test-devstack",WidgetCards.ServicesId);
WidgetProvider.Start();provider.CreateWidget(serviceContext);
string serviceUrl=JsonDocument.Parse(WidgetManager.Templates[serviceContext.Id]).RootElement.GetProperty("metadata").GetProperty("webUrl").GetString()!;
serviceUrl=serviceUrl.Replace("${$host.hostTheme}","dark").Replace("${$host.isUserContextAuthenticated}","true");
string serviceNonce=DevStackWebContent.Query(new Uri(serviceUrl))["session"];
long serviceSeq=0;
JsonElement ServiceState()=>JsonDocument.Parse(WidgetManager.Messages[serviceContext.Id]).RootElement.Clone();
void ServiceMessage(string kind,string verb="",string service="",string? confirmation=null,string? overrideSession=null)
{
 provider.OnMessageReceived(new(serviceContext,JsonSerializer.Serialize(new{protocol=1,kind,session=overrideSession??serviceNonce,client="services-test",seq=++serviceSeq,verb,service,confirmation})));
}
async Task ServiceResource(bool auth,bool read=false,string? customUrl=null)
{
 var r=new WidgetResourceRequestedArgs(serviceContext,customUrl??serviceUrl.Replace("auth=true","auth="+auth.ToString().ToLowerInvariant()));
 if(read)r.Request.Headers["X-DevStack-View"]="state";
 provider.OnResourceRequested(r);await r.Completion.Task.WaitAsync(TimeSpan.FromSeconds(5));
 Assert(r.Response?.StatusCode==(customUrl is null?200:403),"services origin/session resource validation");
 if(read){using var parsed=JsonDocument.Parse((string)r.Response!.Content);Assert(!parsed.RootElement.GetProperty("canControl").GetBoolean(),"services read fallback cannot authorize actions");Assert(parsed.RootElement.GetProperty("confirmation").ValueKind==JsonValueKind.Null,"services fallback cannot disclose confirmation");}
}
await ServiceResource(false);ServiceMessage("ready");await Wait(()=>WidgetManager.Messages.ContainsKey(serviceContext.Id)&&ServiceState().GetProperty("phase").GetString()=="running","service status read");
Assert(!ServiceState().GetProperty("canControl").GetBoolean(),"unauthenticated service page cannot control");
ServiceMessage("action","restart-ask","nginx");Assert(ServiceState().GetProperty("confirmation").ValueKind==JsonValueKind.Null,"unauthenticated confirmation denied");
await ServiceResource(true);ServiceMessage("ready");await Wait(()=>ServiceState().GetProperty("canControl").GetBoolean(),"authenticated services controls");
ServiceMessage("action","start");ServiceMessage("action","stop-confirm");ServiceMessage("action","restart-ask","sshd");
Assert(DevStackData.Restarts==0,"WSL and arbitrary service verbs ignored");
ServiceMessage("action","restart-confirm","nginx","forged");Assert(DevStackData.Restarts==0,"service restart requires native confirmation");
ServiceMessage("action","restart-ask","nginx");var proof=ServiceState().GetProperty("confirmation").GetString();Assert(!string.IsNullOrEmpty(proof),"service restart returns confirmation");
ServiceMessage("action","restart-cancel");Assert(DevStackData.Restarts==0&&ServiceState().GetProperty("confirmation").ValueKind==JsonValueKind.Null,"cancel has no service side effect");
ServiceMessage("action","restart-ask","nginx");proof=ServiceState().GetProperty("confirmation").GetString();
ServiceMessage("action","restart-confirm","redis",proof);Assert(DevStackData.Restarts==0,"confirmation bound to selected service");
ServiceMessage("action","restart-confirm","nginx",proof);ServiceMessage("action","restart-confirm","nginx",proof);
await Wait(()=>DevStackData.Restarts==1&&ServiceState().GetProperty("busy").ValueKind==JsonValueKind.Null,"service restart finished once");
Assert(DevStackData.Last=="nginx","only selected service restarted");
await Wait(()=>ServiceState().GetProperty("canControl").GetBoolean(),"service state rechecked after restart");
var notice=ServiceState().GetProperty("restartStatus");Assert(notice.GetProperty("service").GetString()=="nginx"&&notice.GetProperty("phase").GetString()=="success","restart success requires a fresh healthy selected service");
var expires=notice.GetProperty("expiresAt").GetInt64();Assert(expires>DateTimeOffset.UtcNow.AddSeconds(25).ToUnixTimeMilliseconds()&&expires<=DateTimeOffset.UtcNow.AddSeconds(30).ToUnixTimeMilliseconds(),"success deadline is absolute and bounded to 30 seconds");
ServiceMessage("action","restart-confirm","nginx",proof);Assert(DevStackData.Restarts==1,"used confirmation cannot replay");
ServiceMessage("action","open-projects");Assert(DevStackNavigation.Opens==1,"project route is delegated to system browser");
await ServiceResource(true,true);
ServiceMessage("action","restart-ask","nginx");proof=ServiceState().GetProperty("confirmation").GetString();provider.Deactivate(serviceContext.Id);
ServiceMessage("action","restart-confirm","nginx",proof);await Task.Delay(100);Assert(DevStackData.Restarts==1,"hidden service restart denied");
int serviceReads=DevStackData.Reads;ServiceMessage("action","refresh");await Task.Delay(100);Assert(DevStackData.Reads==serviceReads,"hidden services do not probe");
var priorUpdates=WidgetManager.Updates;provider.Activate(serviceContext);await Task.Delay(100);Assert(WidgetManager.Updates==priorUpdates,"services reopen does not navigate URL again");
await ServiceResource(true,false,WslWebContent.Origin+"/wsl/index.html?session="+session);Assert(DevStackData.Restarts==1,"cross-widget resources cannot become control");
ServiceMessage("action","restart-ask","nginx",overrideSession:session);Assert(DevStackData.Restarts==1,"WSL session cannot authorize services");
StateProbe.Running=false;ServiceMessage("action","refresh");await Wait(()=>ServiceState().GetProperty("phase").GetString()=="offline","services offline state");
ServiceMessage("action","restart-ask","nginx");ServiceMessage("action","open-new");Assert(DevStackData.Restarts==1&&DevStackNavigation.Opens==1,"offline services neither restart nor navigate");
// A reachable port alone cannot prove a restarted systemd unit is healthy.
StateProbe.Running=true;ServiceMessage("action","refresh");
await Wait(()=>ServiceState().GetProperty("canControl").GetBoolean(),"services live before verification failure test");
ServiceMessage("action","restart-ask","redis");proof=ServiceState().GetProperty("confirmation").GetString();
DevStackData.InactiveService="redis";
ServiceMessage("action","restart-confirm","redis",proof);
await Wait(()=>DevStackData.Restarts==2&&ServiceState().GetProperty("restartStatus").ValueKind==JsonValueKind.Object&&ServiceState().GetProperty("restartStatus").GetProperty("phase").GetString()=="error","inactive selected unit cannot verify even with reachable port");
Assert(ServiceState().GetProperty("restartStatus").GetProperty("service").GetString()=="redis"&&ServiceState().GetProperty("restartStatus").GetProperty("expiresAt").GetInt64()==0,"verification error is service-specific and does not expire");
provider.Deactivate(serviceContext.Id);provider.Activate(serviceContext);
await Wait(()=>ServiceState().GetProperty("canControl").GetBoolean(),"reopen gets fresh state");
string NoticePhase()=>ServiceState().GetProperty("restartStatus").GetProperty("phase").GetString()!;
JsonElement Row(string id)=>ServiceState().GetProperty("services").EnumerateArray().Single(s=>s.GetProperty("id").GetString()==id);
Assert(NoticePhase()=="error","reopening does not discard an unresolved verification error");
DevStackData.UnreachableService="redis";DevStackData.InactiveService=null;ServiceMessage("action","refresh");
await Wait(()=>Row("redis").GetProperty("unitActive").GetBoolean()&&!Row("redis").GetProperty("reachable").GetBoolean(),"selected unit active but unreachable");
Assert(NoticePhase()=="error","systemd active alone and other healthy services cannot resolve error");
DevStackData.IntegrationOk=false;DevStackData.UnreachableService=null;ServiceMessage("action","refresh");
await Wait(()=>ServiceState().GetProperty("integration").GetString()=="error","integration read failed");
Assert(NoticePhase()=="error","reachable port with invalid integration cannot resolve error");
DevStackData.IntegrationOk=true;ServiceMessage("action","refresh");
await Wait(()=>NoticePhase()=="recovered"&&ServiceState().GetProperty("canControl").GetBoolean(),"fresh selected unit and port healthy: recovery replaces error");
Assert(ServiceState().GetProperty("restartStatus").GetProperty("service").GetString()=="redis"&&ServiceState().GetProperty("restartStatus").GetProperty("expiresAt").GetInt64()>DateTimeOffset.UtcNow.AddSeconds(25).ToUnixTimeMilliseconds(),"recovery names service and expires after 30 seconds");
Assert(DevStackData.Restarts==2,"verification recovery never repeats command");
DevStackData.RestartOk=false;ServiceMessage("action","restart-ask","php");proof=ServiceState().GetProperty("confirmation").GetString();
DevStackData.InactiveService="php";ServiceMessage("action","restart-confirm","php",proof);
await Wait(()=>DevStackData.Restarts==3&&NoticePhase()=="unconfirmed"&&ServiceState().GetProperty("canControl").GetBoolean(),"unconfirmed command remains warning while unhealthy");
Assert(ServiceState().GetProperty("restartStatus").GetProperty("expiresAt").GetInt64()==0,"unconfirmed restart remains visible while service unhealthy");
DevStackData.InactiveService=null;ServiceMessage("action","refresh");
await Wait(()=>NoticePhase()=="available-unconfirmed","healthy service after failed command does not claim restart succeeded");
Assert(ServiceState().GetProperty("restartStatus").GetProperty("expiresAt").GetInt64()>DateTimeOffset.UtcNow.AddSeconds(25).ToUnixTimeMilliseconds()&&DevStackData.Restarts==3,"availability-only notice expires and no extra restart");
provider.Deactivate(serviceContext.Id);WidgetProvider.Stop();
Console.WriteLine($"PASS: {checks} combined WSL/services policy checks (mocked WinRT and OS actions).");
