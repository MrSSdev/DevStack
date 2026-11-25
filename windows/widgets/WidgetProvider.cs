// Native lifecycle and local WSL web bridge. DevStack's renderer is unchanged.
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
        public bool ContentReady { get; set; }
        public bool Authenticated { get; set; }
        public string Nonce { get; init; } = NewToken();
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
    private static readonly string Server = Guid.NewGuid().ToString("N");
    private static Timer? _timer;
    private static CancellationTokenSource? _visibleSession, _wslSession;
    private static bool _recovered, _pending, _worker, _wslFresh;
    private static DateTimeOffset _lastUse = DateTimeOffset.UtcNow;
    private static StateProbe.Snapshot? _wsl, _services;
    private static WslResources.Result _resources = WslResources.Empty("unavailable");
    private static string? _operation;
    private static long _epoch;
    public static AutoResetEvent Wake { get; } = new(false);
    private static string NewToken() => Convert.ToHexString(RandomNumberGenerator.GetBytes(32)).ToLowerInvariant();
    private static bool Known(string id) => id == WidgetCards.WslId;

    public WidgetProvider() { Recover(); DevStackWidget.Recover(); }
    public static void Start()
    {
        DevStackWidget.Start();
        lock (Gate) _timer ??= new Timer(_ => RequestRefresh(), null, Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        Visibility(); RequestRefresh();
    }
    public static void Stop()
    {
        DevStackWidget.Stop();
        Timer? timer; CancellationTokenSource? visible, wsl;
        lock (Gate)
        {
            timer = _timer; _timer = null; visible = _visibleSession; _visibleSession = null;
            wsl = _wslSession; _wslSession = null; _pending = false; _wslFresh = false;
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
        if (!DevStackWidget.CanExit()) return false;
        lock (Gate) return _operation is null && !Widgets.Values.Any(w => w.Interested) &&
            DateTimeOffset.UtcNow - _lastUse > TimeSpan.FromSeconds(90);
    }
    private static void Recover()
    {
        lock (Gate)
        {
            if (_recovered) return;
            _recovered = true;
            var cached = WslViewCache.Load();
            if (cached is not null)
            {
                _wsl = new(cached.Running, "", null, null, null, null, cached.CheckedAt);
                _resources = cached.Resources;
            }
        }
        WidgetDiagnostics.Write("provider-recovered");
        try
        {
            foreach (var info in WidgetManager.GetDefault().GetWidgetInfos())
            {
                var c = info.WidgetContext;
                if (!Known(c.DefinitionId)) continue;
                (string? Nonce, bool Current) resume = c.DefinitionId == WidgetCards.WslId
                    ? WslWebContent.Resume(info.Template) : (null, false);
                lock (Gate) Widgets[c.Id] = new Instance { Id = c.Id, Definition = c.DefinitionId,
                    Nonce = resume.Nonce ?? NewToken(), Published = resume.Current,
                    Size = WidgetDesign.NormalizeSize(c.Size.ToString()), Active = c.IsActive };
                WidgetDiagnostics.Write(resume.Current ? "url-restored" : "url-needs-publish", c.Id);
            }
        }
        catch (Exception error) { WidgetDiagnostics.Write("recovery-error", error: error); }
    }
    private static void Visibility()
    {
        CancellationTokenSource? close = null, closeWsl = null;
        lock (Gate)
        {
            bool visible = Widgets.Values.Any(w => w.Interested);
            bool wslVisible = Widgets.Values.Any(w => w.Interested && w.Definition == WidgetCards.WslId);
            if (visible && _visibleSession is null) _visibleSession = new CancellationTokenSource();
            if (!visible) { close = _visibleSession; _visibleSession = null; _pending = false; _wslFresh = false; }
            if (wslVisible && _wslSession is null) _wslSession = new CancellationTokenSource();
            if (!wslVisible) { closeWsl = _wslSession; _wslSession = null; }
            _timer?.Change(visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan,
                visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan);
        }
        Cancel(close); Cancel(closeWsl); Wake.Set();
    }
    public void CreateWidget(WidgetContext c)
    {
        if (c.DefinitionId == WidgetCards.ServicesId) { DevStackWidget.Activate(c); return; }
        if (!Known(c.DefinitionId)) return;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(c.Id, out var widget))
                Widgets[c.Id] = widget = new Instance { Id = c.Id, Definition = c.DefinitionId };
            widget.Active = true;
            widget.Size = WidgetDesign.NormalizeSize(c.Size.ToString());
            _lastUse = DateTimeOffset.UtcNow;
        }
        WidgetDiagnostics.Write("create", c.Id);
        Visibility(); Publish(c.Id, true); RequestRefresh();
    }
    public void Activate(WidgetContext c)
    {
        if (c.DefinitionId == WidgetCards.ServicesId) { DevStackWidget.Activate(c); return; }
        if (!Known(c.DefinitionId)) return;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(c.Id, out var widget))
                Widgets[c.Id] = widget = new Instance { Id = c.Id, Definition = c.DefinitionId };
            widget.Active = true; widget.WebHidden = false;
            widget.Size = WidgetDesign.NormalizeSize(c.Size.ToString());
            _lastUse = DateTimeOffset.UtcNow;
        }
        WidgetDiagnostics.Write("activate", c.Id);
        Visibility(); Publish(c.Id, false); RequestRefresh();
    }
    public void Deactivate(string widgetId)
    {
        if (DevStackWidget.Contains(widgetId)) { DevStackWidget.Deactivate(widgetId); return; }
        lock (Gate)
        {
            if (Widgets.TryGetValue(widgetId, out var widget))
            {
                widget.Active = false; widget.Confirmation = null; widget.ConfirmUntil = default;
            }
            _lastUse = DateTimeOffset.UtcNow;
        }
        WidgetDiagnostics.Write("deactivate", widgetId);
        PushWeb(widgetId); Visibility();
    }
    public void DeleteWidget(string widgetId, string customState)
    {
        if (DevStackWidget.Contains(widgetId)) { DevStackWidget.Delete(widgetId); return; }
        lock (Gate) { Widgets.Remove(widgetId); _lastUse = DateTimeOffset.UtcNow; }
        Visibility();
    }
    public void OnWidgetContextChanged(WidgetContextChangedArgs args)
    {
        var c = args.WidgetContext;
        if (c.DefinitionId == WidgetCards.ServicesId) return;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(c.Id, out var widget)) return;
            widget.Size = WidgetDesign.NormalizeSize(c.Size.ToString());
        }
        Publish(c.Id, false); RequestRefresh();
    }
    private static void Publish(string id, bool first)
    {
        string? template = null; bool web;
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
                if (web)
                {
                    lock (Gate) { if (Widgets.TryGetValue(id, out var widget)) widget.Published = true; }
                    WidgetDiagnostics.Write("page-url-published", id);
                }
            }
            catch (Exception error) { WidgetDiagnostics.Write("publish-error", id, error); }
        }
        if (web) PushWeb(id);
    }
    private static string BuildWebState(string id, bool native)
    {
        lock (Gate)
        {
            if (!Widgets.TryGetValue(id, out var widget) || widget.Definition != WidgetCards.WslId) return "{}";
            if (widget.ConfirmUntil <= DateTimeOffset.UtcNow || _wsl?.WslRunning != true)
            { widget.Confirmation = null; widget.ConfirmUntil = default; }
            bool live = _wslFresh && _wsl is not null && _wsl.CheckedAt >= DateTimeOffset.Now.AddSeconds(-30);
            bool controls = native && widget.Ready && widget.Interested && widget.Authenticated && live;
            var phase = _operation ?? (_wsl is null ? "checking" : _wsl.WslRunning switch
                { true => controls && widget.Confirmation is not null ? "confirm" : "running", false => "offline", _ => "error" });
            var metrics = _resources;
            if (_wsl?.WslRunning != true) metrics = WslResources.Empty(_wsl?.WslRunning == false ? "offline" : "unavailable");
            bool metricsStale = metrics.Metrics is not null && (!live || metrics.CheckedAt < DateTimeOffset.Now.AddSeconds(-30));
            return JsonSerializer.Serialize(new
            {
                protocol = 1, kind = "state", session = widget.Nonce, server = Server, client = widget.Client,
                revision = ++widget.Revision, transport = native ? "native" : "resource",
                active = widget.Interested, canControl = controls,
                phase, cached = !live, checkedAt = _wsl?.CheckedAt, message = widget.Message,
                metrics = metrics.Metrics, metricsStatus = metricsStale ? "stale" : metrics.Status,
                confirmUntil = controls && widget.Confirmation is not null ? widget.ConfirmUntil.ToUnixTimeMilliseconds() : 0,
                confirmation = controls ? widget.Confirmation : null
            }, Json);
        }
    }
    private static void PushWeb(string id)
    {
        lock (Gate)
            if (!Widgets.TryGetValue(id, out var widget) || !widget.Ready) return;
        var data = BuildWebState(id, true);
        try
        {
            WidgetManager.GetDefault().SendMessageToContent(id, data);
            WidgetDiagnostics.Write("state-sent", id);
        }
        catch (Exception error) { WidgetDiagnostics.Write("state-send-error", id, error); }
    }
    private static void PublishInterested()
    {
        string[] ids;
        lock (Gate) ids = Widgets.Values.Where(w => w.Interested).Select(w => w.Id).ToArray();
        foreach (var id in ids) Publish(id, false);
    }
    public async void OnResourceRequested(WidgetResourceRequestedArgs args)
    {
        if (args.WidgetContext.DefinitionId == WidgetCards.ServicesId) { DevStackWidget.Resource(args); return; }
        var deferral = args.GetDeferral();
        Uri? uri = null; bool allowed = false, read = false; string state = "{}";
        var id = args.WidgetContext.Id;
        try
        {
            var context = args.WidgetContext;
            if (context.DefinitionId == WidgetCards.WslId && args.Request.Method == "GET" &&
                Uri.TryCreate(args.Request.Uri, UriKind.Absolute, out uri) && WslWebContent.IsLocal(uri))
            {
                lock (Gate)
                {
                    if (Widgets.TryGetValue(id, out var widget))
                    {
                        allowed = true;
                        if (uri.AbsolutePath == "/wsl/index.html")
                        {
                            var query = WslWebContent.Query(uri);
                            allowed = query.GetValueOrDefault("session") == widget.Nonce;
                            if (allowed)
                            {
                                widget.Authenticated = string.Equals(query.GetValueOrDefault("auth"), "true", StringComparison.OrdinalIgnoreCase);
                                read = WslWebContent.IsStateRead(args);
                                if (!read)
                                {
                                    widget.Ready = false; widget.Client = ""; widget.Sequence = 0;
                                    widget.Confirmation = null; widget.ConfirmUntil = default;
                                }
                                widget.ContentReady = true;
                            }
                        }
                    }
                }
            }
            if (allowed && uri?.AbsolutePath == "/wsl/index.html")
            {
                state = BuildWebState(id, false);
                WidgetDiagnostics.Write(read ? "state-resource-read" : "document-served", id);
            }
            await WslWebContent.RespondAsync(args, uri, allowed, state);
            // A resource GET cannot invoke commands. Refresh still requires native visibility.
            if (allowed && uri?.AbsolutePath == "/wsl/index.html")
            {
                if (read) PushWeb(id);
                RequestRefresh();
            }
        }
        catch (Exception error)
        {
            WidgetDiagnostics.Write("resource-error", id, error);
            try { WslWebContent.Deny(args); } catch { /* host is closing */ }
        }
        finally { deferral.Complete(); }
    }
    public void OnMessageReceived(WidgetMessageReceivedArgs args)
    {
        if (args.WidgetContext.DefinitionId == WidgetCards.ServicesId) { DevStackWidget.Message(args); return; }
        var id = args.WidgetContext.Id;
        if (args.WidgetContext.DefinitionId != WidgetCards.WslId || args.Message.Length > 4096) return;
        string kind, verb = "", confirmation = "";
        try
        {
            using var parsed = JsonDocument.Parse(args.Message);
            using var unwrapped = parsed.RootElement.ValueKind == JsonValueKind.String
                ? JsonDocument.Parse(parsed.RootElement.GetString() ?? "null") : null;
            var root = unwrapped?.RootElement ?? parsed.RootElement;
            if (root.ValueKind != JsonValueKind.Object || root.GetProperty("protocol").GetInt32() != 1) return;
            kind = root.GetProperty("kind").GetString() ?? "";
            var nonce = root.GetProperty("session").GetString();
            var client = root.GetProperty("client").GetString();
            var sequence = root.GetProperty("seq").GetInt64();
            if (client is null || client.Length > 80 || sequence <= 0) return;
            lock (Gate)
            {
                if (!Widgets.TryGetValue(id, out var widget) || nonce != widget.Nonce)
                { WidgetDiagnostics.Write("message-session-rejected", id); return; }
                if (kind is not ("ready" or "hidden" or "action" or "seen" or "diagnostic")) return;
                if (kind == "ready")
                {
                    if (client != widget.Client) { widget.Client = client; widget.Sequence = 0; widget.Confirmation = null; widget.ConfirmUntil = default; }
                    // Remember early hello, but never poll or enable commands before Activate.
                    widget.Ready = true; widget.ContentReady = true; widget.WebHidden = false;
                    WidgetDiagnostics.Write(widget.Active ? "hello-active" : "hello-before-activate", id);
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
                    if (verb != "refresh" && (!widget.Authenticated || !_wslFresh)) return;
                    if (verb == "stop-confirm")
                    {
                        confirmation = root.GetProperty("confirmation").GetString() ?? "";
                        if (confirmation != widget.Confirmation || widget.ConfirmUntil <= DateTimeOffset.UtcNow) return;
                    }
                }
            }
        }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException or OverflowException)
        { WidgetDiagnostics.Write("message-format-rejected", id, error); return; }
        if (kind == "seen") { WidgetDiagnostics.Write("state-seen-by-page", id); return; }
        if (kind == "diagnostic") { WidgetDiagnostics.Write("page-error-reported", id); return; }
        if (kind is "ready" or "hidden") { Visibility(); PushWeb(id); if (kind == "ready") RequestRefresh(); }
        else if (kind == "action") HandleAction(id, verb);
    }
    public void OnActionInvoked(WidgetActionInvokedArgs args)
    {
        // Obsolete Action.Execute is ignored. DevStack's Action.OpenUrl is host-owned.
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
                outcome = current.WslRunning is null ? "Nie można potwierdzić stanu WSL." : start ? "Ubuntu już działa." : "Ubuntu jest już zatrzymane.";
                lock (Gate) { _wsl = current; _wslFresh = true; }
                return;
            }
            // Only explicit, confirmed user actions reach these calls. Never retry a command.
            var command = start ? await StateProbe.StartAsync() : await StateProbe.StopAsync();
            if (command.ExitCode != 0) outcome = "Polecenie WSL nie powiodło się (" + command.ExitCode + ").";
            CancellationToken verify;
            lock (Gate) verify = _visibleSession?.Token ?? new CancellationToken(true);
            if (!verify.IsCancellationRequested)
            {
                var actual = await StateProbe.CaptureWslOnlyAsync(verify);
                lock (Gate) { _wsl = actual; _wslFresh = true; _services = actual.WslRunning == false ? actual : null; }
                if (command.ExitCode == 0 && actual.WslRunning != start) outcome = "Stan Ubuntu wymaga ponownego odczytu.";
            }
            else lock (Gate) { _wslFresh = false; _services = null; }
        }
        catch (OperationCanceledException) { /* hidden before command or during verification */ }
        catch (Exception error) { outcome = "Błąd operacji: " + error.GetType().Name; }
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
            catch (Exception error) { WidgetDiagnostics.Write("refresh-error", error: error); }
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
            _wsl = snapshot; _wslFresh = true;
            if (snapshot.WslRunning != true) { _services = snapshot; _resources = WslResources.Empty(snapshot.WslRunning == false ? "offline" : "unavailable"); }
            ids = Widgets.Values.Where(w => w.Interested).Select(w => w.Id).ToArray();
        }
        WslViewCache.Save(snapshot, _resources);
        WidgetDiagnostics.Write("wsl-status-read");
        foreach (var id in ids) Publish(id, false);
        var work = new List<Task>();
        lock (Gate)
        {
            if (snapshot.WslRunning == true && Widgets.Values.Any(w => w.Interested && w.Definition == WidgetCards.ServicesId)) work.Add(ServicesAsync(snapshot, epoch, token));
            if (snapshot.WslRunning == true && _wslSession is not null && Widgets.Values.Any(w => w.Interested && w.ContentReady && w.Definition == WidgetCards.WslId))
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
        WslViewCache.Save(snapshot, resources);
        WidgetDiagnostics.Write("metrics-" + resources.Status);
        foreach (var id in ids) PushWeb(id);
    }
}
