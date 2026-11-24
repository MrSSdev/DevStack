// Windows widget lifecycle + the approved WSL page. DevStack's renderer is unchanged.
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;
using Microsoft.Windows.Widgets.Providers;

namespace DevStack.Widgets;

[ComVisible(true)]
[ComDefaultInterface(typeof(IWidgetProvider))]
[Guid("6DE550FB-ECB8-4955-87E2-4943E247E34A")]
public sealed class WidgetProvider : IWidgetProvider, IWidgetResourceProvider, IWidgetProviderMessage
{
    private sealed class Instance
    {
        public required string Id { get; init; }
        public required string Definition { get; init; }
        public string Size { get; set; } = "large";
        public bool Active { get; set; }
        public bool WebHidden { get; set; }
        public bool Published { get; set; }
        public bool Ready { get; set; }
        public bool Authenticated { get; set; }
        public string Nonce { get; } = NewToken();
        public string Client { get; set; } = "";
        public long Sequence { get; set; }
        public long Revision { get; set; }
        public DateTimeOffset ConfirmUntil { get; set; }
        public string? Confirmation { get; set; }
        public string Message { get; set; } = "";
        public bool Interested => Active && !WebHidden;
    }
    private static readonly object Gate = new();
    private static readonly Dictionary<string, Instance> Widgets = new();
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private static Timer? _timer;
    private static CancellationTokenSource? _visibleSession;
    private static CancellationTokenSource? _wslSession;
    private static bool _recovered, _pending, _worker;
    private static DateTimeOffset _lastUse = DateTimeOffset.UtcNow;
    private static StateProbe.Snapshot? _wsl, _services;
    private static WslResources.Result _resources = WslResources.Empty("unavailable");
    private static string? _operation;
    private static long _epoch;
    public static AutoResetEvent Wake { get; } = new(false);
    private static string NewToken() => Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    private static bool Known(string id) => id is WidgetCards.WslId or WidgetCards.ServicesId;

    public WidgetProvider() => Recover();
    public static void Start()
    {
        lock (Gate) _timer ??= new Timer(_ => RequestRefresh(), null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        Visibility();
        RequestRefresh();
    }
    public static void Stop()
    {
        Timer? timer; CancellationTokenSource? visible, wsl;
        lock (Gate)
        {
            timer = _timer; _timer = null; visible = _visibleSession; _visibleSession = null;
            wsl = _wslSession; _wslSession = null; _pending = false;
        }
        timer?.Dispose(); Cancel(visible); Cancel(wsl);
    }
    private static void Cancel(CancellationTokenSource? source)
    {
        if (source is null) return;
        try { source.Cancel(); } finally { source.Dispose(); }
    }
    public static bool ShouldExit()
    {
        lock (Gate) return _operation is null && !Widgets.Values.Any(w => w.Interested) &&
            DateTimeOffset.UtcNow - _lastUse > TimeSpan.FromSeconds(90);
    }
    private static void Recover()
    {
        lock (Gate) { if (_recovered) return; _recovered = true; }
        try
        {
            foreach (var info in WidgetManager.GetDefault().GetWidgetInfos())
            {
                var c = info.WidgetContext;
                if (!Known(c.DefinitionId)) continue;
                lock (Gate) Widgets[c.Id] = new Instance { Id = c.Id, Definition = c.DefinitionId,
                    Size = WidgetDesign.NormalizeSize(c.Size.ToString()), Active = c.IsActive };
            }
        }
        catch (Exception error) { Trace.TraceWarning("Widget recovery: " + error.GetType().Name); }
    }
    private static void Visibility()
    {
        CancellationTokenSource? close = null, closeWsl = null;
        lock (Gate)
        {
            bool visible = Widgets.Values.Any(w => w.Interested);
            bool wslVisible = Widgets.Values.Any(w => w.Interested && w.Definition == WidgetCards.WslId);
            if (visible && _visibleSession is null) _visibleSession = new CancellationTokenSource();
            if (!visible) { close = _visibleSession; _visibleSession = null; _pending = false; }
            if (wslVisible && _wslSession is null) _wslSession = new CancellationTokenSource();
            if (!wslVisible) { closeWsl = _wslSession; _wslSession = null; }
            _timer?.Change(visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan,
                visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan);
        }
        Cancel(close); Cancel(closeWsl); Wake.Set();
    }
    public void CreateWidget(WidgetContext c)
    {
        if (!Known(c.DefinitionId)) return;
        lock (Gate)
        {
            Widgets[c.Id] = new Instance { Id = c.Id, Definition = c.DefinitionId,
                Active = true, Size = WidgetDesign.NormalizeSize(c.Size.ToString()) };
            _lastUse = DateTimeOffset.UtcNow;
        }
        Visibility(); Publish(c.Id, true); RequestRefresh();
    }
    public void Activate(WidgetContext c)
    {
        if (!Known(c.DefinitionId)) return;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(c.Id, out var widget))
                Widgets[c.Id] = widget = new Instance { Id = c.Id, Definition = c.DefinitionId };
            widget.Active = true; widget.WebHidden = false;
            widget.Size = WidgetDesign.NormalizeSize(c.Size.ToString());
            _lastUse = DateTimeOffset.UtcNow;
        }
        Visibility(); Publish(c.Id, false); RequestRefresh();
    }
    public void Deactivate(string widgetId)
    {
        lock (Gate)
        {
            if (Widgets.TryGetValue(widgetId, out var widget))
            {
                widget.Active = false; widget.Confirmation = null; widget.ConfirmUntil = default;
            }
            _lastUse = DateTimeOffset.UtcNow;
        }
        PushWeb(widgetId); Visibility();
    }
    public void DeleteWidget(string widgetId, string customState)
    {
        lock (Gate) { Widgets.Remove(widgetId); _lastUse = DateTimeOffset.UtcNow; }
        Visibility();
    }
    public void OnWidgetContextChanged(WidgetContextChangedArgs args)
    {
        var c = args.WidgetContext;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(c.Id, out var widget)) return;
            widget.Size = WidgetDesign.NormalizeSize(c.Size.ToString());
        }
        Publish(c.Id, false); RequestRefresh();
    }
    private static void Publish(string id, bool first)
    {
        string? template = null; bool web = false;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(id, out var widget) || !widget.Interested) return;
            web = widget.Definition == WidgetCards.WslId;
            if (web)
            {
                if (!widget.Published) template = WslWebContent.Template(widget.Nonce, _wsl);
            }
            else if (_services is not null) template = WidgetDesign.DevStack(_services, widget.Size);
            else if (first) template = WidgetDesign.Initial(widget.Definition, widget.Size);
        }
        if (template is not null)
        {
            try
            {
                WidgetManager.GetDefault().UpdateWidget(new WidgetUpdateRequestOptions(id)
                { Template = template, Data = "{}", IsPlaceholderContent = false });
                if (web) lock (Gate) { if (Widgets.TryGetValue(id, out var widget)) widget.Published = true; }
            }
            catch (Exception error) { Trace.TraceWarning("Publish widget: " + error.GetType().Name); }
        }
        if (web) PushWeb(id);
    }
    private static void PushWeb(string id)
    {
        string data;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(id, out var widget) || widget.Definition != WidgetCards.WslId || !widget.Ready) return;
            if (widget.ConfirmUntil <= DateTimeOffset.UtcNow || _wsl?.WslRunning != true)
            { widget.Confirmation = null; widget.ConfirmUntil = default; }
            var phase = _operation ?? (_wsl is null ? "checking" : _wsl.WslRunning switch
                { true => widget.Confirmation is not null ? "confirm" : "running", false => "offline", _ => "error" });
            var metrics = _resources;
            if (_wsl?.WslRunning != true || metrics.CheckedAt < DateTimeOffset.Now.AddSeconds(-30))
                metrics = WslResources.Empty(_wsl?.WslRunning == false ? "offline" : "unavailable");
            data = JsonSerializer.Serialize(new
            {
                protocol = 1, kind = "state", session = widget.Nonce, revision = ++widget.Revision,
                active = widget.Interested, canControl = widget.Interested && widget.Authenticated,
                phase, checkedAt = _wsl?.CheckedAt, message = widget.Message,
                metrics = metrics.Metrics, metricsStatus = metrics.Status,
                confirmUntil = widget.Confirmation is null ? 0 : widget.ConfirmUntil.ToUnixTimeMilliseconds(),
                confirmation = widget.Confirmation
            }, Json);
        }
        try { WidgetManager.GetDefault().SendMessageToContent(id, data); }
        catch (Exception error) { Trace.TraceWarning("WSL content message: " + error.GetType().Name); }
    }
    private static void PublishInterested()
    {
        string[] ids;
        lock (Gate) ids = Widgets.Values.Where(w => w.Interested).Select(w => w.Id).ToArray();
        foreach (var id in ids) Publish(id, false);
    }
    public void OnResourceRequested(WidgetResourceRequestedArgs args)
    {
        Uri? uri = null; bool allowed = false;
        try
        {
            var context = args.WidgetContext;
            if (context.DefinitionId == WidgetCards.WslId && Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out uri) && WslWebContent.IsLocal(uri))
            {
                lock (Gate)
                {
                    if (Widgets.TryGetValue(context.Id, out var widget))
                    {
                        allowed = true;
                        if (uri.AbsolutePath == "/wsl/index.html")
                        {
                            var query = WslWebContent.Query(uri);
                            allowed = query.GetValueOrDefault("session") == widget.Nonce;
                            if (allowed)
                            {
                                widget.Authenticated = string.Equals(query.GetValueOrDefault("auth"), "true", StringComparison.OrdinalIgnoreCase);
                                widget.Ready = false; widget.Client = ""; widget.Sequence = 0;
                                widget.Confirmation = null; widget.ConfirmUntil = default;
                            }
                        }
                    }
                }
            }
            WslWebContent.Respond(args, uri, allowed);
        }
        catch (Exception error)
        {
            Trace.TraceWarning("WSL resource: " + error.GetType().Name);
            try { WslWebContent.Respond(args, null, false); } catch { /* host is closing */ }
        }
    }
    public void OnMessageReceived(WidgetMessageReceivedArgs args)
    {
        var id = args.WidgetContext.Id;
        if (args.WidgetContext.DefinitionId != WidgetCards.WslId || args.Message.Length > 4096) return;
        string kind, verb = "", confirmation = "";
        try
        {
            using var parsed = JsonDocument.Parse(args.Message);
            var root = parsed.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.GetProperty("protocol").GetInt32() != 1) return;
            kind = root.GetProperty("kind").GetString() ?? "";
            var nonce = root.GetProperty("session").GetString();
            var client = root.GetProperty("client").GetString();
            var sequence = root.GetProperty("seq").GetInt64();
            if (client is null || client.Length > 80 || sequence <= 0) return;
            lock (Gate)
            {
                if (!Widgets.TryGetValue(id, out var widget) || !widget.Active || nonce != widget.Nonce) return;
                if (kind == "ready")
                {
                    if (client != widget.Client) { widget.Client = client; widget.Sequence = 0; widget.Confirmation = null; widget.ConfirmUntil = default; }
                    widget.Ready = true; widget.WebHidden = false;
                }
                if (!widget.Ready || widget.Client != client || sequence <= widget.Sequence) return;
                widget.Sequence = sequence; _lastUse = DateTimeOffset.UtcNow;
                if (kind == "hidden")
                {
                    widget.WebHidden = true; widget.Confirmation = null; widget.ConfirmUntil = default;
                }
                if (kind == "action")
                {
                    if (!widget.Interested) return;
                    verb = root.GetProperty("verb").GetString() ?? "";
                    if (verb != "refresh" && !widget.Authenticated) return;
                    if (verb == "stop-confirm")
                    {
                        confirmation = root.GetProperty("confirmation").GetString() ?? "";
                        if (confirmation != widget.Confirmation || widget.ConfirmUntil <= DateTimeOffset.UtcNow) return;
                    }
                }
            }
        }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException or OverflowException) { return; }
        if (kind is "ready" or "hidden") { Visibility(); PushWeb(id); if (kind == "ready") RequestRefresh(); }
        else if (kind == "action") HandleAction(id, verb);
    }
    public void OnActionInvoked(WidgetActionInvokedArgs args)
    {
        // WSL controls use the authenticated, per-instance web bridge only.
        // Ignore obsolete Action.Execute messages cached from the previous UI.
        // DevStack uses native Action.OpenUrl, which the host handles directly.
    }
    private static void HandleAction(string id, string verb)
    {
        bool run = false, start = false, refresh = false; CancellationToken token = default; long epoch = 0;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(id, out var w) || !w.Interested || w.Definition != WidgetCards.WslId || _operation is not null) return;
            _lastUse = DateTimeOffset.UtcNow;
            switch (verb)
            {
                case "refresh": refresh = true; w.Message = ""; break;
                case "start":
                    if (_wsl?.WslRunning != false) return;
                    start = true; run = true; break;
                case "stop-ask":
                    if (_wsl?.WslRunning != true) return;
                    w.Confirmation = NewToken(); w.ConfirmUntil = DateTimeOffset.UtcNow.AddSeconds(30); w.Message = ""; break;
                case "stop-cancel": w.Confirmation = null; w.ConfirmUntil = default; w.Message = ""; break;
                case "stop-confirm":
                    if (_wsl?.WslRunning != true || w.Confirmation is null || w.ConfirmUntil <= DateTimeOffset.UtcNow) return;
                    run = true; break;
                default: return;
            }
            if (run)
            {
                _operation = start ? "starting" : "stopping"; epoch = ++_epoch;
                w.Message = ""; w.Confirmation = null; w.ConfirmUntil = default;
                _resources = WslResources.Empty("unavailable");
                token = _wslSession?.Token ?? new CancellationToken(true);
            }
        }
        PushWeb(id);
        if (run) _ = Task.Run(() => RunCommandAsync(id, start, epoch, token));
        else if (refresh) RequestRefresh();
    }
    private static async Task RunCommandAsync(string id, bool start, long epoch, CancellationToken visibleToken)
    {
        string outcome = "";
        try
        {
            var current = await StateProbe.CaptureWslOnlyAsync(visibleToken);
            lock (Gate)
            {
                if (visibleToken.IsCancellationRequested || !Widgets.TryGetValue(id, out var w) || !w.Interested || epoch != _epoch) return;
            }
            if (current.WslRunning != !start)
            {
                outcome = current.WslRunning is null ? "Nie mo\u017cna potwierdzi\u0107 stanu WSL." : start ? "Ubuntu ju\u017c dzia\u0142a." : "Ubuntu jest ju\u017c zatrzymane.";
                lock (Gate) _wsl = current;
                return;
            }
            // These are deliberate user commands. Once issued, hiding the UI
            // cannot undo Start/Stop; complete the command without background polling.
            var command = start ? await StateProbe.StartAsync() : await StateProbe.StopAsync();
            if (command.ExitCode != 0) outcome = "Polecenie WSL nie powiod\u0142o si\u0119 (" + command.ExitCode + ").";
            CancellationToken verify;
            lock (Gate) verify = _visibleSession?.Token ?? new CancellationToken(true);
            if (!verify.IsCancellationRequested)
            {
                var actual = await StateProbe.CaptureWslOnlyAsync(verify);
                lock (Gate) { _wsl = actual; _services = actual.WslRunning == false ? actual : null; }
                if (command.ExitCode == 0 && actual.WslRunning != start) outcome = "Stan Ubuntu wymaga ponownego odczytu.";
            }
            else lock (Gate) { _wsl = null; _services = null; }
        }
        catch (OperationCanceledException) { /* panel hidden before command or during verification */ }
        catch (Exception error) { outcome = "B\u0142\u0105d operacji: " + error.GetType().Name; }
        finally
        {
            lock (Gate)
            {
                if (Widgets.TryGetValue(id, out var w)) w.Message = outcome;
                _operation = null; ++_epoch; _lastUse = DateTimeOffset.UtcNow;
            }
            PublishInterested(); RequestRefresh(); Wake.Set();
        }
    }
    private static void RequestRefresh()
    {
        bool launch = false;
        lock (Gate)
        {
            if (_visibleSession is null || !Widgets.Values.Any(w => w.Interested)) return;
            _pending = true;
            if (!_worker) { _worker = true; launch = true; }
        }
        if (launch) _ = Task.Run(DrainAsync);
    }
    private static async Task DrainAsync()
    {
        while (true)
        {
            CancellationToken token; long epoch;
            lock (Gate)
            {
                if (!_pending || _visibleSession is null) { _worker = false; return; }
                _pending = false; token = _visibleSession.Token; epoch = _epoch;
            }
            try { await RefreshAsync(epoch, token); }
            catch (OperationCanceledException) { }
            catch (Exception error) { Trace.TraceWarning("Widget refresh: " + error.GetType().Name); }
        }
    }
    private static bool Current(long epoch, CancellationToken token)
    {
        lock (Gate) return !token.IsCancellationRequested && _visibleSession is not null &&
            _visibleSession.Token == token && _epoch == epoch && _operation is null;
    }
    private static async Task RefreshAsync(long epoch, CancellationToken token)
    {
        if (!Current(epoch, token)) return;
        var snapshot = await StateProbe.CaptureWslOnlyAsync(token);
        if (!Current(epoch, token)) return;
        string[] ids;
        lock (Gate)
        {
            _wsl = snapshot;
            if (snapshot.WslRunning != true) { _services = snapshot; _resources = WslResources.Empty(snapshot.WslRunning == false ? "offline" : "unavailable"); }
            ids = Widgets.Values.Where(w => w.Interested).Select(w => w.Id).ToArray();
        }
        // Same fast status path as before; optional metrics never delay the status.
        foreach (var id in ids) Publish(id, false);
        var work = new List<Task>();
        lock (Gate)
        {
            if (snapshot.WslRunning == true && Widgets.Values.Any(w => w.Interested && w.Definition == WidgetCards.ServicesId)) work.Add(ServicesAsync(snapshot, epoch, token));
            if (snapshot.WslRunning == true && _wslSession is not null && Widgets.Values.Any(w => w.Interested && w.Ready && w.Definition == WidgetCards.WslId))
                work.Add(ResourcesAsync(snapshot, epoch, token, _wslSession.Token));
        }
        await Task.WhenAll(work);
    }
    private static async Task ServicesAsync(StateProbe.Snapshot snapshot, long epoch, CancellationToken token)
    {
        var services = await StateProbe.CaptureServicesAfterWslAsync(snapshot, token);
        if (!Current(epoch, token)) return;
        string[] ids;
        lock (Gate) { _services = services; ids = Widgets.Values.Where(w => w.Interested && w.Definition == WidgetCards.ServicesId).Select(w => w.Id).ToArray(); }
        foreach (var id in ids) Publish(id, false);
    }
    private static async Task ResourcesAsync(StateProbe.Snapshot snapshot, long epoch, CancellationToken token, CancellationToken wslToken)
    {
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(token, wslToken);
        var resources = await WslResources.CaptureAsync(snapshot.WslRunning, linked.Token);
        if (linked.IsCancellationRequested || !Current(epoch, token)) return;
        string[] ids;
        lock (Gate) { _resources = resources; ids = Widgets.Values.Where(w => w.Interested && w.Definition == WidgetCards.WslId).Select(w => w.Id).ToArray(); }
        foreach (var id in ids) PushWeb(id);
    }
}
