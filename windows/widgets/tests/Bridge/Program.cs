using System.Collections;
using System.Reflection;
using System.Text.Json;
using DevStack.Widgets;
using Microsoft.Windows.Widgets.Providers;

var provider=new WidgetProvider();
var context=new WidgetContext("test-wsl",WidgetCards.WslId);
int checks=0;long sequence=0;
void Assert(bool value,string label){if(!value)throw new InvalidOperationException(label);checks++;}
JsonElement State()=>JsonDocument.Parse(WidgetManager.Messages[context.Id]).RootElement.Clone();
async Task Wait(Func<bool> predicate,string label){for(int i=0;i<250;i++){if(predicate()){checks++;return;}await Task.Delay(10);}throw new Exception("Timed out: "+label);}
WidgetProvider.Start();provider.CreateWidget(context);
var template=JsonDocument.Parse(WidgetManager.Templates[context.Id]);
var url=template.RootElement.GetProperty("metadata").GetProperty("webUrl").GetString()!;
var session=WslWebContent.Query(new Uri(url.Replace("${$host.hostTheme}","dark").Replace("${$host.isUserContextAuthenticated}","false")))["session"];
void Resource(bool auth){var args=new WidgetResourceRequestedArgs(context,WslWebContent.Origin+"/wsl/index.html?session="+session+"&auth="+auth.ToString().ToLowerInvariant());provider.OnResourceRequested(args);Assert(args.Response?.StatusCode==200,"packaged page response");}
void Message(string kind,string? verb=null,string? confirmation=null,string? nonce=null){provider.OnMessageReceived(new WidgetMessageReceivedArgs(context,JsonSerializer.Serialize(new{protocol=1,kind,session=nonce??session,client="test-client",seq=++sequence,verb,confirmation})));}
Resource(false);Message("ready");await Wait(()=>WidgetManager.Messages.ContainsKey(context.Id)&&State().GetProperty("phase").GetString()=="offline","initial offline");
Assert(!State().GetProperty("canControl").GetBoolean(),"unauthenticated host is noninteractive");Message("action","start");await Task.Delay(60);Assert(StateProbe.Starts==0,"unauthenticated Start denied");
Resource(true);Message("ready");await Wait(()=>State().GetProperty("canControl").GetBoolean(),"authenticated session");
Message("action","start",nonce:"wrong");await Task.Delay(60);Assert(StateProbe.Starts==0,"wrong session denied");
Message("action","start");Message("action","start");await Wait(()=>State().GetProperty("phase").GetString()=="running","start complete");Assert(StateProbe.Starts==1,"duplicate Start coalesced");
Message("action","stop-confirm","forged");await Task.Delay(50);Assert(StateProbe.Stops==0,"Stop without confirmation denied");
Message("action","stop-ask");await Wait(()=>State().GetProperty("phase").GetString()=="confirm","ask Stop");var token=State().GetProperty("confirmation").GetString();
Message("action","stop-cancel");Assert(StateProbe.Running==true&&StateProbe.Stops==0,"Cancel does not stop");
Message("action","stop-ask");
var table=(IDictionary)typeof(WidgetProvider).GetField("Widgets",BindingFlags.NonPublic|BindingFlags.Static)!.GetValue(null)!;
var entry=table[context.Id]!;entry.GetType().GetProperty("ConfirmUntil")!.SetValue(entry,DateTimeOffset.UtcNow.AddSeconds(-1));
Message("action","stop-confirm",State().GetProperty("confirmation").GetString());await Task.Delay(40);Assert(StateProbe.Stops==0,"expired confirmation denied");
Message("action","stop-ask");token=State().GetProperty("confirmation").GetString();provider.Deactivate(context.Id);Message("action","stop-confirm",token);await Task.Delay(80);Assert(StateProbe.Stops==0,"hidden Stop denied");
var reads=StateProbe.Reads;Message("action","refresh");await Task.Delay(80);Assert(StateProbe.Reads==reads,"no reads for inactive card");
provider.Activate(context);Message("ready");await Wait(()=>State().GetProperty("phase").GetString()=="running","reopen");
Message("action","stop-ask");token=State().GetProperty("confirmation").GetString();Message("action","stop-confirm",token);Message("action","stop-confirm",token);await Wait(()=>State().GetProperty("phase").GetString()=="offline","confirmed Stop");Assert(StateProbe.Stops==1,"confirmed Stop executes once");
StateProbe.Running=null;Message("action","refresh");await Wait(()=>State().GetProperty("phase").GetString()=="error","unknown is not offline");Message("action","start");Assert(StateProbe.Starts==1,"unknown state cannot start");
var bad=new WidgetResourceRequestedArgs(context,"https://evil.invalid/wsl/index.html");provider.OnResourceRequested(bad);Assert(bad.Response?.StatusCode==403,"foreign origin rejected");
var missing=new WidgetResourceRequestedArgs(context,WslWebContent.Origin+"/private-file");provider.OnResourceRequested(missing);Assert(missing.Response?.StatusCode==403,"unknown resource never fetched remotely");
var valid=new WslResources.Reading(1,StateProbe.Distro,"linux-procfs","shared-wsl-kernel",7,1800,23000,12600,256000,5040);
Assert(WslResources.Valid(valid),"valid readings");Assert(!WslResources.Valid(valid with{CpuPercent=101}),"out-of-range CPU rejected");Assert(!WslResources.Valid(valid with{MemoryUsedBytes=24000}),"invalid memory rejected");Assert(!WslResources.Valid(valid with{Distro="different"}),"wrong distro rejected");
var off=await WslResources.CaptureAsync(false,new CancellationToken(true));Assert(off.Status=="offline"&&off.Metrics is null,"offline never makes I/O even with canceled token");
WidgetProvider.Stop();Console.WriteLine($"PASS: {checks} real provider/message-policy checks with mocked Windows/WSL transport. No system commands executed.");
