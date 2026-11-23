// Widget COM callbacks are modeled on the Microsoft WindowsAppSDK-Samples
// provider (MIT). We keep one process and two distinct widget definitions.
using System.Diagnostics;
using System.Runtime.InteropServices;
using Microsoft.Windows.Widgets.Providers;

namespace DevStack.Widgets;

[ComVisible(true)]
[ComDefaultInterface(typeof(IWidgetProvider))]
[Guid("6DE550FB-ECB8-4955-87E2-4943E247E34A")]
public sealed class WidgetProvider : IWidgetProvider
{
    private sealed class Instance
    {
        public required string Id { get; init; }
        public required string Definition { get; init; }
        public bool Active { get; set; }
        public DateTimeOffset StopConfirmationUntil { get; set; }
        public string Message { get; set; } = "";
        public bool ActionBusy { get; set; }
    }

    private static readonly object Gate = new();
    private static readonly Dictionary<string, Instance> Widgets = new();
    private static readonly SemaphoreSlim RefreshGate = new(1, 1);
    private static Timer? _refreshTimer;
    private static DateTimeOffset _lastUse = DateTimeOffset.UtcNow;
    private static bool _recovered;
    private static CancellationTokenSource? _visibleSession;
    private static bool _refreshPending;
    private static bool _refreshWorkerRunning;
    private static StateProbe.Snapshot? _lastWslSnapshot;
    private static StateProbe.Snapshot? _lastServicesSnapshot;

    // C4.3: preserve verified C4.2 status/visibility refresh, but allow
    // deliberate Start and confirmed Stop ONLY on the WSL card.
    // The DevStack card remains read-only.
    private static readonly bool WslControlStage = true;

    public static AutoResetEvent Wake { get; } = new(false);

    public WidgetProvider()
    {
        RecoverPinned();
    }

    public static void Start()
    {
        // Create an idle timer. There is no periodic work unless one or more
        // widgets are active according to IWidgetProvider.Activate/Deactivate.
        _refreshTimer = new Timer(_ => RequestRefresh(), null,
            Timeout.InfiniteTimeSpan, Timeout.InfiniteTimeSpan);
        UpdateVisibility();
        RequestRefresh(); // Startup while the board is already visible.
    }

    public static void Stop()
    {
        _refreshTimer?.Dispose();
        _refreshTimer = null;
        CancellationTokenSource? closing;
        lock (Gate)
        {
            closing = _visibleSession;
            _visibleSession = null;
            _refreshPending = false;
        }
        closing?.Cancel();
        closing?.Dispose();
    }

    private static void UpdateVisibility()
    {
        CancellationTokenSource? closing = null;
        lock (Gate)
        {
            bool visible = Widgets.Values.Any(w => w.Active);
            if (visible && _visibleSession is null)
                _visibleSession = new CancellationTokenSource();
            else if (!visible && _visibleSession is not null)
            {
                closing = _visibleSession;
                _visibleSession = null;
                _refreshPending = false;
            }

            // Periodic checks run only while the Windows widget host is
            // interested in the cards. Opening triggers a separate immediate
            // refresh, so the timer does not need an early first tick.
            _refreshTimer?.Change(
                visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan,
                visible ? TimeSpan.FromSeconds(20) : Timeout.InfiniteTimeSpan);
        }

        // Cancel in-flight WSL and HTTPS/TCP probes when the last widget hides.
        // Do this outside Gate to avoid locking callbacks against I/O teardown.
        closing?.Cancel();
        closing?.Dispose();
        Wake.Set();
    }

    private static bool SessionIsVisible(CancellationToken cancellationToken)
    {
        lock (Gate)
            return !cancellationToken.IsCancellationRequested &&
                _visibleSession is not null &&
                _visibleSession.Token.Equals(cancellationToken) &&
                Widgets.Values.Any(w => w.Active);
    }

    public static bool ShouldExit()
    {
        lock (Gate)
        {
            // Allow Windows to unload the provider after Win+W is no longer visible.
            // COM will reactivate it when a pinned widget next needs an update.
            return !Widgets.Values.Any(w => w.Active) &&
                DateTimeOffset.UtcNow - _lastUse > TimeSpan.FromSeconds(90);
        }
    }

    private static void RecoverPinned()
    {
        lock (Gate)
        {
            if (_recovered) return;
            _recovered = true;
        }

        try
        {
            var infos = WidgetManager.GetDefault().GetWidgetInfos();
            foreach (var info in infos)
            {
                var context = info.WidgetContext;
                if (!KnownDefinition(context.DefinitionId)) continue;
                lock (Gate)
                {
                    Widgets[context.Id] = new Instance
                    {
                        Id = context.Id,
                        Definition = context.DefinitionId,
                        Active = context.IsActive
                    };
                }
            }
        }
        catch (Exception e)
        {
            Trace.TraceWarning("Widget state recovery: " + e.Message);
        }
    }

    private static bool KnownDefinition(string id) =>
        id == WidgetCards.WslId || id == WidgetCards.ServicesId;

    public void CreateWidget(WidgetContext context)
    {
        if (!KnownDefinition(context.DefinitionId)) return;
        lock (Gate)
        {
            Widgets[context.Id] = new Instance
            {
                Id = context.Id, Definition = context.DefinitionId, Active = true
            };
            _lastUse = DateTimeOffset.UtcNow;
        }
        SendImmediateCard(context.Id, context.DefinitionId, allowPlaceholder: true);
        UpdateVisibility();
        RequestRefresh();
    }

    public void DeleteWidget(string widgetId, string customState)
    {
        lock (Gate)
        {
            Widgets.Remove(widgetId);
            _lastUse = DateTimeOffset.UtcNow;
        }
        UpdateVisibility();
    }

    public void Activate(WidgetContext context)
    {
        if (!KnownDefinition(context.DefinitionId)) return;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(context.Id, out var widget))
            {
                widget = new Instance { Id = context.Id, Definition = context.DefinitionId };
                Widgets[context.Id] = widget;
            }
            widget.Active = true;
            _lastUse = DateTimeOffset.UtcNow;
        }
        UpdateVisibility();
        // Reuse the last known state immediately. If the provider just started,
        // leave the host's previously rendered card intact until the new probe.
        SendImmediateCard(context.Id, context.DefinitionId, allowPlaceholder: false);
        RequestRefresh();
    }

    public void Deactivate(string widgetId)
    {
        lock (Gate)
        {
            if (Widgets.TryGetValue(widgetId, out var widget))
                widget.Active = false;
            _lastUse = DateTimeOffset.UtcNow;
        }
        UpdateVisibility();
    }

    public void OnWidgetContextChanged(WidgetContextChangedArgs args)
    {
        // Never overwrite a valid card with "Odczytywanie..." during a
        // context/size change. Recheck immediately only when visible.
        RequestRefresh();
    }

    private static void SendImmediateCard(
        string widgetId, string definitionId, bool allowPlaceholder)
    {
        StateProbe.Snapshot? snapshot;
        lock (Gate)
            snapshot = definitionId == WidgetCards.WslId
                ? _lastWslSnapshot : _lastServicesSnapshot;

        if (snapshot is null && !allowPlaceholder)
            return; // Preserve Windows' cached rendering across COM restarts.

        try
        {
            var template = snapshot is not null
                ? definitionId == WidgetCards.WslId
                    ? WidgetCards.WslReadOnlyTemplate(snapshot)
                    : WidgetCards.DevStackReadOnlyTemplate(snapshot)
                : WidgetCards.InitialTemplate(definitionId);

            WidgetManager.GetDefault().UpdateWidget(new WidgetUpdateRequestOptions(widgetId)
            {
                Template = template,
                Data = "{}",
                IsPlaceholderContent = false
            });
        }
        catch (Exception error)
        {
            Trace.TraceWarning("Immediate widget content: " + error);
        }
    }

    public void OnActionInvoked(WidgetActionInvokedArgs args)
    {
        // Action.Execute sends an app-defined verb. Never trust it as a
        // permission grant: reject unknown IDs, hidden cards, DevStack
        // definitions, repeated clicks and unrecognized verbs.
        var widgetId = args.WidgetContext.Id;
        var verb = args.Verb;
        bool publish = false, execute = false, start = false, refresh = false;

        lock (Gate)
        {
            if (!WslControlStage ||
                !Widgets.TryGetValue(widgetId, out var widget) ||
                !widget.Active || widget.Definition != WidgetCards.WslId ||
                widget.ActionBusy)
                return;

            _lastUse = DateTimeOffset.UtcNow;
            var knownState = _lastWslSnapshot?.WslRunning;
            switch (verb)
            {
                case "start":
                    if (knownState != false) return;
                    widget.ActionBusy = true;
                    widget.StopConfirmationUntil = default;
                    widget.Message = "Uruchamianie Ubuntu...";
                    execute = true;
                    start = true;
                    publish = true;
                    break;

                case "stop-ask":
                    if (knownState != true) return;
                    widget.StopConfirmationUntil = DateTimeOffset.UtcNow.AddSeconds(30);
                    widget.Message = "";
                    publish = true;
                    break;

                case "stop-cancel":
                    widget.StopConfirmationUntil = default;
                    widget.Message = "";
                    publish = true;
                    break;

                case "stop-confirm":
                    // A second, explicit click in a 30-second confirmation
                    // window is mandatory before terminating the distro.
                    if (knownState != true ||
                        widget.StopConfirmationUntil <= DateTimeOffset.UtcNow)
                    {
                        widget.StopConfirmationUntil = default;
                        widget.Message = "Potwierdzenie wygasło. Wybierz Zatrzymaj ponownie.";
                        publish = true;
                        break;
                    }
                    widget.ActionBusy = true;
                    widget.StopConfirmationUntil = default;
                    widget.Message = "Zatrzymywanie Ubuntu...";
                    execute = true;
                    publish = true;
                    break;

                case "refresh":
                    refresh = true;
                    break;

                default:
                    return;
            }
        }

        // The button and confirmation update immediately, without waiting
        // for the next status-poll tick.
        if (publish)
            SendImmediateCard(widgetId, WidgetCards.WslId, allowPlaceholder: false);

        if (execute)
            _ = Task.Run(() => RunActionAsync(widgetId, start));
        else if (refresh)
            RequestRefresh();
    }

    private static async Task RunActionAsync(string widgetId, bool start)
    {
        string outcome = "";
        try
        {
            // Recheck the state before any destructive or startup command.
            // Passive check never invokes wsl.exe -d.
            var current = await StateProbe.CaptureWslOnlyAsync();
            if (start && current.WslRunning != false)
            {
                outcome = current.WslRunning == true
                    ? "Ubuntu już działa."
                    : "Nie udało się potwierdzić stanu WSL.";
                return;
            }

            if (!start && current.WslRunning != true)
            {
                outcome = "Ubuntu jest już zatrzymane lub stan jest nieznany.";
                return;
            }

            // These are the only paths that deliberately start/stop Ubuntu.
            var command = start ? await StateProbe.StartAsync()
                                : await StateProbe.StopAsync();
            if (command.ExitCode != 0)
            {
                outcome = string.IsNullOrWhiteSpace(command.Output)
                    ? "Polecenie WSL zwróciło kod " + command.ExitCode
                    : "Błąd WSL: " + command.Output[..Math.Min(command.Output.Length, 100)];
                return;
            }

            // Verify the actual post-command state before showing controls
            // again. Never infer success solely from a zero exit code.
            var verified = await StateProbe.CaptureWslOnlyAsync();
            lock (Gate)
            {
                _lastWslSnapshot = verified;
                _lastServicesSnapshot = null; // Services status may be stale.
            }

            if (verified.WslRunning != start)
                outcome = "Polecenie wykonano, ale stan Ubuntu wymaga ponownego sprawdzenia.";
        }
        catch (Exception error)
        {
            outcome = "Błąd operacji: " + error.GetType().Name;
        }
        finally
        {
            lock (Gate)
            {
                if (Widgets.TryGetValue(widgetId, out var widget))
                {
                    widget.ActionBusy = false;
                    widget.StopConfirmationUntil = default;
                    widget.Message = outcome;
                }
                _lastUse = DateTimeOffset.UtcNow;
            }
            SendImmediateCard(widgetId, WidgetCards.WslId, allowPlaceholder: false);
            RequestRefresh();
        }
    }

    private static void RequestRefresh()
    {
        bool startWorker = false;
        lock (Gate)
        {
            if (_visibleSession is null || !Widgets.Values.Any(w => w.Active))
                return; // No reads, timers or background work while hidden.

            // Coalesce simultaneous Activate/Create/periodic triggers rather
            // than drop an activation request when another probe is in flight.
            _refreshPending = true;
            if (!_refreshWorkerRunning)
            {
                _refreshWorkerRunning = true;
                startWorker = true;
            }
        }
        if (startWorker)
            _ = Task.Run(ProcessRefreshQueueAsync);
        Wake.Set();
    }

    private static async Task ProcessRefreshQueueAsync()
    {
        while (true)
        {
            CancellationToken cancellationToken;
            lock (Gate)
            {
                if (!_refreshPending || _visibleSession is null)
                {
                    _refreshWorkerRunning = false;
                    return;
                }
                _refreshPending = false;
                cancellationToken = _visibleSession.Token;
            }

            try
            {
                await RefreshAsync(cancellationToken);
            }
            catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested)
            {
                // The board closed. Pending requests from a newly opened board
                // (if any) will be processed with its new visibility token.
            }
            catch (Exception error)
            {
                Trace.TraceWarning("Widget status refresh: " + error);
            }
        }
    }

    private static void PublishReadOnlyCard(
        string widgetId, string definitionId, StateProbe.Snapshot snapshot,
        CancellationToken cancellationToken)
    {
        lock (Gate)
        {
            if (_visibleSession is null ||
                !_visibleSession.Token.Equals(cancellationToken))
                return;
            if (cancellationToken.IsCancellationRequested ||
                !Widgets.TryGetValue(widgetId, out var current) ||
                !current.Active || current.Definition != definitionId)
                return;
        }

        try
        {
            var template = definitionId == WidgetCards.WslId
                ? WidgetCards.WslReadOnlyTemplate(snapshot)
                : WidgetCards.DevStackReadOnlyTemplate(snapshot);
            WidgetManager.GetDefault().UpdateWidget(new WidgetUpdateRequestOptions(widgetId)
            {
                Template = template,
                Data = "{}",
                IsPlaceholderContent = false
            });
        }
        catch (Exception error)
        {
            Trace.TraceWarning("Read-only widget update: " + error);
        }
    }

    private static async Task RefreshReadOnlyStatusesAsync(
        CancellationToken cancellationToken)
    {
        (string Id, string Definition)[] selected;
        lock (Gate)
            selected = Widgets.Values
                .Where(w => w.Active)
                .Select(w => (w.Id, w.Definition))
                .ToArray();
        if (selected.Length == 0)
            return;

        // First get WSL status, then immediately update its card (and the
        // DevStack offline card). Do not hold up the WSL result while waiting
        // for optional HTTPS/TCP checks.
        var wsl = await StateProbe.CaptureWslOnlyAsync(cancellationToken);
        if (!SessionIsVisible(cancellationToken))
            return;

        lock (Gate)
        {
            _lastWslSnapshot = wsl;
            if (wsl.WslRunning != true)
                _lastServicesSnapshot = wsl;
        }

        foreach (var (id, definition) in selected)
        {
            if (definition == WidgetCards.WslId || wsl.WslRunning != true)
                PublishReadOnlyCard(id, definition, wsl, cancellationToken);
        }

        if (wsl.WslRunning == true &&
            selected.Any(w => w.Definition == WidgetCards.ServicesId))
        {
            // Do not probe services if the last card was hidden while we
            // were reading WSL. Use its same snapshot to avoid a second
            // invocation of wsl.exe.
            if (!SessionIsVisible(cancellationToken))
                return;
            var services = await StateProbe.CaptureServicesAfterWslAsync(
                wsl, cancellationToken);
            if (!SessionIsVisible(cancellationToken))
                return;

            lock (Gate)
                _lastServicesSnapshot = services;

            foreach (var (id, definition) in selected)
                if (definition == WidgetCards.ServicesId)
                    PublishReadOnlyCard(id, definition, services, cancellationToken);
        }
    }

    private static async Task RefreshAsync(CancellationToken cancellationToken)
    {
        await RefreshGate.WaitAsync(cancellationToken);
        try
        {
            if (WslControlStage)
            {
                await RefreshReadOnlyStatusesAsync(cancellationToken);
                return;
            }

            Instance[] selected;
            lock (Gate)
                selected = Widgets.Values.Where(w => w.Active).ToArray();
            if (selected.Length == 0) return;

            // One state capture serves every active widget, reducing cost.
            var snapshot = await StateProbe.CaptureAsync();
            foreach (var widget in selected)
            {
                bool pending;
                string message;
                lock (Gate)
                {
                    if (!Widgets.TryGetValue(widget.Id, out var current) || !current.Active)
                        continue;
                    pending = current.StopConfirmationUntil > DateTimeOffset.UtcNow;
                    message = current.Message;
                }

                try
                {
                    var options = new WidgetUpdateRequestOptions(widget.Id)
                    {
                        Template = WidgetCards.Template(widget.Definition),
                        Data = WidgetCards.Data(widget.Definition, snapshot, message, pending),
                        IsPlaceholderContent = false
                    };
                    WidgetManager.GetDefault().UpdateWidget(options);
                }
                catch (Exception error)
                {
                    Trace.TraceWarning("Widget update: " + error.Message);
                }
            }
        }
        catch (Exception error)
        {
            Trace.TraceWarning("Refresh failed: " + error.Message);
        }
        finally
        {
            RefreshGate.Release();
        }
    }
}
