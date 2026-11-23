using System.Collections.Concurrent;
using System.Text.Json;

namespace DevStack.Widgets;

/// <summary>
/// Two large native cards. Real Adaptive Card surfaces, restrained type,
/// and a stretch bottom dock for controls followed by the sync footer.
/// No web view, background server, CSS-only properties or synthetic metrics.
/// </summary>
internal static class WidgetDesign
{
    internal const string ProjectsUrl = "https://devstack.test/#/projects";
    internal const string NewProjectUrl = "https://devstack.test/#/new";
    private static readonly ConcurrentDictionary<string, string> Images = new();
    private const string UserContext = "${$host.isUserContextAuthenticated}";

    // Preserve the provider callback API; the manifest intentionally offers only large.
    internal static string NormalizeSize(string? size) => "large";

    private static Dictionary<string, object> Text(string value,
        string size = "default", bool strong = false, bool muted = false,
        string spacing = "small", string color = "default") => new()
    {
        ["type"] = "TextBlock", ["text"] = value, ["size"] = size,
        ["weight"] = strong ? "bolder" : "default", ["isSubtle"] = muted,
        ["color"] = color, ["spacing"] = spacing, ["wrap"] = true
    };

    private static Dictionary<string, object> Group(string id, IEnumerable<object> items,
        string spacing = "medium", bool surface = false, string? minHeight = null)
    {
        var group = new Dictionary<string, object>
        {
            ["type"] = "Container", ["id"] = id, ["items"] = items.ToArray(),
            ["spacing"] = spacing, ["bleed"] = false
        };
        if (surface) group["style"] = "emphasis";
        if (minHeight is not null) group["minHeight"] = minHeight;
        return group;
    }

    private static Dictionary<string, object> Col(object[] items, string width = "stretch",
        string spacing = "medium", bool surface = false)
    {
        var col = new Dictionary<string, object>
        {
            ["type"] = "Column", ["width"] = width, ["items"] = items,
            ["spacing"] = spacing, ["verticalContentAlignment"] = "center",
            ["bleed"] = false
        };
        if (surface) col["style"] = "emphasis";
        return col;
    }

    private static object Row(string id, object[] columns, string spacing = "medium") => new
    {
        type = "ColumnSet", id, columns, spacing
    };

    private static object Icon(string name, string alt, int size = 24)
    {
        string data = Images.GetOrAdd(name, n =>
        {
            var path = Path.Combine(AppContext.BaseDirectory, "ProviderAssets", "UI_" + n + ".png");
            // A missing packaged asset is a test failure, not a silent diamond fallback.
            return "data:image/png;base64," + Convert.ToBase64String(File.ReadAllBytes(path));
        });
        return new { type = "Image", url = data, altText = alt,
            width = size + "px", height = size + "px", spacing = "none" };
    }

    private static Dictionary<string, object> Execute(string title, string verb, string style = "default") => new()
    {
        ["type"] = "Action.Execute", ["title"] = title, ["verb"] = verb,
        ["style"] = style, ["$when"] = UserContext, ["tooltip"] = title
    };

    private static Dictionary<string, object> Link(string title, string url, string tooltip) => new()
    {
        ["type"] = "Action.OpenUrl", ["title"] = title, ["url"] = url,
        ["tooltip"] = tooltip, ["$when"] = UserContext
    };

    private static object Actions(string id, IEnumerable<Dictionary<string, object>> actions) => new
    {
        type = "ActionSet", id, spacing = "medium", actions = actions.ToArray()
    };

    private static object Hero(string icon, string label, string title, string detail, string tone) =>
        Group("status-card", new object[]
        {
            Row("status-row", new object[]
            {
                Col(new[] { Icon(icon, label, 32) }, "auto", "none"),
                Col(new object[]
                {
                    Text(title, "large", strong: true, spacing: "none"),
                    Text(detail, "small", spacing: "small", color: tone)
                })
            }, "none")
        }, "none", surface: true, minHeight: "88px");

    private static object Bottom(StateProbe.Snapshot? snapshot, IEnumerable<object> controls,
        string context)
    {
        var items = controls.ToList();
        items.Add(Row("sync-row", new object[]
        {
            Col(new object[] { Text(snapshot is null ? "Oczekiwanie na odczyt" :
                    "Synchronizacja  " + snapshot.CheckedAt.ToString("HH:mm:ss"),
                    "small", muted: true, spacing: "none") }, spacing: "none"),
            Col(new object[] { Text(context, "small", muted: true, spacing: "none") }, "auto", "small")
        }, "medium"));
        var bottom = Group("bottom-dock", items);
        bottom["height"] = "stretch";
        bottom["verticalContentAlignment"] = "bottom";
        return bottom;
    }

    private static string Card(IEnumerable<object> body) => JsonSerializer.Serialize(new
    {
        type = "AdaptiveCard", version = "1.5", lang = "pl",
        verticalContentAlignment = "top", body = body.ToArray()
        // Actions are inside bottom-dock BEFORE sync-row. Root actions would
        // appear below the footer. No fixed-height spacer or unsupported padding.
    });

    internal static string Initial(string definition, string size)
    {
        bool wsl = definition == WidgetCards.WslId;
        return Card(new object[]
        {
            Hero(wsl ? "WSL" : "DevStack", wsl ? "Ubuntu" : "DevStack",
                "Sprawdzanie stanu", "Pierwszy odczyt środowiska", "default"),
            Group("initial-note", new object[]
            {
                Text("Za chwilę pojawią się aktualne informacje.", "small", muted: true, spacing: "none")
            }),
            Bottom(null, Array.Empty<object>(), wsl ? "WSL 2" : "Lokalnie")
        });
    }

    internal static string Wsl(StateProbe.Snapshot snapshot, string size,
        string message = "", bool pendingStop = false, bool actionBusy = false)
    {
        bool? running = snapshot.WslRunning;
        bool confirming = pendingStop && running == true && !actionBusy;
        string title = actionBusy ? "Wykonywanie…" : running switch
        {
            true => "Uruchomione", false => "Wyłączone", _ => "Brak danych"
        };
        string detail = actionBusy ? "Poczekaj na wynik polecenia" : running switch
        {
            true => "● Gotowe do pracy", false => "● System zatrzymany", _ => "● Odczyt niedostępny"
        };
        string tone = actionBusy ? "default" : running == true ? "good" :
            running == false ? "default" : "attention";
        var body = new List<object> { Hero("WSL", "Ubuntu", title, detail, tone) };
        if (confirming)
            body.Add(Group("stop-warning", new object[]
            {
                Text("Zatrzymać Ubuntu?", strong: true, spacing: "none"),
                Text("Zakończy to wszystkie procesy w Ubuntu.", "small", color: "attention"),
                Text("Potwierdzenie jest ważne przez 30 sekund.", "small", muted: true, spacing: "medium")
            }, surface: true));
        else
            body.Add(Group("environment-card", new object[]
            {
                Text("Środowisko", strong: true, spacing: "none"),
                Row("environment-details", new object[]
                {
                    Col(new object[] { Text("Dystrybucja", "small", muted: true, spacing: "none"),
                        Text("Ubuntu 26.04", strong: true) }, spacing: "none"),
                    Col(new object[] { Text("Silnik", "small", muted: true, spacing: "none"),
                        Text("WSL 2", strong: true) }, "auto")
                }),
                Text("Uruchamianie i zatrzymywanie na żądanie.", "small", muted: true, spacing: "medium")
            }, surface: true));

        var controls = new List<object>();
        var actions = new List<Dictionary<string, object>>();
        if (!string.IsNullOrWhiteSpace(message))
            controls.Add(Text(message, "small", color: actionBusy ? "default" : "attention"));
        else if (running is null && !string.IsNullOrWhiteSpace(snapshot.WslError))
            controls.Add(Text(snapshot.WslError, "small", color: "attention"));
        if (confirming)
        {
            actions.Add(Execute("Zatrzymaj", "stop-confirm", "destructive"));
            actions.Add(Execute("Anuluj", "stop-cancel"));
        }
        else if (!actionBusy && running == true)
            actions.Add(Execute("Zatrzymaj Ubuntu", "stop-ask"));
        else if (!actionBusy && running == false)
            actions.Add(Execute("Uruchom Ubuntu", "start", "positive"));
        if (actions.Count > 0) controls.Add(Actions("wsl-actions", actions));
        body.Add(Bottom(snapshot, controls, "Ręcznie"));
        return Card(body);
    }

    private sealed record Service(string Label, string Icon, bool? Value);

    private static object ServiceColumn(Service service, bool? running, string spacing)
    {
        string label = running switch
        {
            false => "Nieaktywne", null => "Nieznane",
            true => service.Value switch { true => "Dostępne", false => "Brak połączenia", _ => "Sprawdzanie" }
        };
        string tone = running != true ? "default" : service.Value switch
        {
            true => "good", false => "attention", _ => "default"
        };
        return Col(new object[]
        {
            Row("service-" + service.Icon, new object[]
            {
                Col(new[] { Icon(service.Icon, service.Label, 22) }, "auto", "none"),
                Col(new object[] { Text(service.Label, "small", strong: true, spacing: "none") }, spacing: "small")
            }, "none"),
            Text("● " + label, "small", spacing: "medium", color: tone)
        }, spacing: spacing, surface: true);
    }

    internal static string DevStack(StateProbe.Snapshot snapshot, string size)
    {
        bool? running = snapshot.WslRunning;
        var services = new[]
        {
            new Service("Nginx", "Nginx", snapshot.Nginx),
            new Service("PHP-FPM", "PHP", snapshot.PhpFpm),
            new Service("MariaDB", "MariaDB", snapshot.MariaDb),
            new Service("Redis", "Redis", snapshot.Redis)
        };
        int count = services.Count(s => s.Value == true);
        bool known = services.All(s => s.Value.HasValue);
        string title = running switch
        {
            false => "Środowisko offline", null => "Stan nieznany",
            true when count == 4 => "Gotowe do pracy",
            true when !known => "Sprawdzanie usług", _ => "Wymaga uwagi"
        };
        string detail = running switch
        {
            false => "● Ubuntu jest wyłączone", null => "● Brak potwierdzonego odczytu",
            true => "● Dostępne usługi: " + count + " z 4"
        };
        var body = new List<object>
        {
            Hero("DevStack", "DevStack", title, detail,
                running == true && count == 4 ? "good" : running is null ? "attention" : "default"),
            Row("service-row-1", new[]
            {
                ServiceColumn(services[0], running, "none"),
                ServiceColumn(services[1], running, "medium")
            }),
            Row("service-row-2", new[]
            {
                ServiceColumn(services[2], running, "none"),
                ServiceColumn(services[3], running, "medium")
            }, "small")
        };
        var controls = new List<object>();
        if (running == true)
        {
            controls.Add(Actions("project-actions", new[]
            {
                Link("Dodaj projekt", NewProjectUrl, "Otwórz formularz dodawania projektu w DevStack"),
                Link("Projekty", ProjectsUrl, "Otwórz listę projektów w DevStack")
            }));
        }
        else controls.Add(Text(running == false ?
            "Uruchom Ubuntu w sąsiednim widżecie." :
            string.IsNullOrWhiteSpace(snapshot.WslError) ? "Nie udało się odczytać stanu Ubuntu." : snapshot.WslError,
            "small", muted: true));
        // Explicit connectivity scope, without a large diagnostic paragraph.
        body.Add(Bottom(snapshot, controls, "HTTPS/TCP"));
        return Card(body);
    }
}
