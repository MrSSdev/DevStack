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
    }

    private static readonly object Gate = new();
    private static readonly Dictionary<string, Instance> Widgets = new();
    private static readonly SemaphoreSlim RefreshGate = new(1, 1);
    private static Timer? _refreshTimer;
    private static DateTimeOffset _lastUse = DateTimeOffset.UtcNow;
    private static bool _recovered;

    // TEMPORARY C4 SMOKE TEST: keep native cards static until we verify
    // they pin, render and survive reopening the Widgets Board on ASUS.
    // Restore live refresh only after pinning is proven reliable.
    private static readonly bool StaticPinningSmokeTest = true;

    public static AutoResetEvent Wake { get; } = new(false);

    public WidgetProvider()
    {
        RecoverPinned();
    }

    public static void Start()
    {
        if (StaticPinningSmokeTest) return;
        // The timer exists while the provider runs, but it queries Windows only
        // while a widget is active in the board. No polling when the board is hidden.
        _refreshTimer = new Timer(_ => RequestRefresh(), null,
            TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(20));
    }

    public static void Stop()
    {
        _refreshTimer?.Dispose();
        _refreshTimer = null;
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
                        Definition = context.DefinitionId
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
        SendInitialCard(context.Id, context.DefinitionId);
        if (!StaticPinningSmokeTest) RequestRefresh();
    }

    public void DeleteWidget(string widgetId, string customState)
    {
        lock (Gate)
        {
            Widgets.Remove(widgetId);
            _lastUse = DateTimeOffset.UtcNow;
        }
        Wake.Set();
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
        SendInitialCard(context.Id, context.DefinitionId);
        if (!StaticPinningSmokeTest) RequestRefresh();
    }

    public void Deactivate(string widgetId)
    {
        lock (Gate)
        {
            if (Widgets.TryGetValue(widgetId, out var widget))
                widget.Active = false;
            _lastUse = DateTimeOffset.UtcNow;
        }
        Wake.Set();
    }

    public void OnWidgetContextChanged(WidgetContextChangedArgs args)
    {
        if (StaticPinningSmokeTest)
            SendInitialCard(args.WidgetContext.Id, args.WidgetContext.DefinitionId);
        else
            RequestRefresh();
    }

    private static void SendInitialCard(string widgetId, string definitionId)
    {
        // Windows widget-provider guidance sends the first template/data from
        // CreateWidget synchronously. Do not wait for WSL/network/probe tasks.
        WidgetManager.GetDefault().UpdateWidget(new WidgetUpdateRequestOptions(widgetId)
        {
            Template = WidgetCards.InitialTemplate(definitionId),
            Data = "{}",
            IsPlaceholderContent = false
        });
    }

    public void OnActionInvoked(WidgetActionInvokedArgs args)
    {
        var widgetId = args.WidgetContext.Id;
        var verb = args.Verb;
        Instance? widget;
        lock (Gate)
        {
            if (!Widgets.TryGetValue(widgetId, out widget)) return;
            _lastUse = DateTimeOffset.UtcNow;
            if (verb == "stop-ask" && widget.Definition == WidgetCards.WslId)
            {
                widget.StopConfirmationUntil = DateTimeOffset.UtcNow.AddSeconds(30);
            }
            else if (verb == "stop-cancel")
            {
                widget.StopConfirmationUntil = default;
            }
        }

        if (verb == "refresh" || verb == "stop-ask" || verb == "stop-cancel")
        {
            RequestRefresh();
            return;
        }

        // Only the WSL widget can start or stop Ubuntu, never the DevStack widget.
        if (widget.Definition != WidgetCards.WslId) return;

        if (verb == "start")
        {
            _ = Task.Run(() => RunActionAsync(widget.Id, start: true));
        }
        else if (verb == "stop-confirm")
        {
            bool allowed;
            lock (Gate)
            {
                allowed = widget.StopConfirmationUntil > DateTimeOffset.UtcNow;
                widget.StopConfirmationUntil = default;
            }
            if (allowed)
                _ = Task.Run(() => RunActionAsync(widget.Id, start: false));
            else
                RequestRefresh();
        }
    }

    private static async Task RunActionAsync(string widgetId, bool start)
    {
        try
        {
            var now = await StateProbe.CaptureAsync();
            if (start && now.WslRunning != false)
            {
                SetMessage(widgetId, now.WslRunning == true
                    ? "Ubuntu już działa." : "Nie można potwierdzić stanu WSL.");
                return;
            }
            if (!start && now.WslRunning != true)
            {
                SetMessage(widgetId, "Ubuntu jest już zatrzymane lub stan jest nieznany.");
                return;
            }

            SetMessage(widgetId, start ? "Uruchamianie Ubuntu..." : "Zatrzymywanie Ubuntu...");
            var result = start ? await StateProbe.StartAsync() : await StateProbe.StopAsync();
            SetMessage(widgetId, result.ExitCode == 0 ? ""
                : "Niepowodzenie: " + result.Output[..Math.Min(result.Output.Length, 90)]);
        }
        catch (Exception e)
        {
            SetMessage(widgetId, "Błąd operacji: " + e.GetType().Name);
        }
        finally
        {
            RequestRefresh();
        }
    }

    private static void SetMessage(string widgetId, string message)
    {
        lock (Gate)
        {
            if (Widgets.TryGetValue(widgetId, out var widget))
                widget.Message = message;
            _lastUse = DateTimeOffset.UtcNow;
        }
        RequestRefresh();
    }

    private static void RequestRefresh()
    {
        _ = Task.Run(RefreshAsync);
        Wake.Set();
    }

    private static async Task RefreshAsync()
    {
        if (!await RefreshGate.WaitAsync(0)) return;
        try
        {
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
