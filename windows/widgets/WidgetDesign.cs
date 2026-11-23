using System.Collections.Concurrent;
using System.Text.Json;

namespace DevStack.Widgets;

/// <summary>
/// DevStack visual system for native Windows 11 Adaptive Card widgets.
/// Separate layouts for each manifest size; all text/images work without
/// WSL or external icon hosting. Rendering is entirely passive.
/// </summary>
internal static class WidgetDesign
{
    internal const string ProjectsUrl = "https://devstack.test/#/projects";
    internal const string NewProjectUrl = "https://devstack.test/#/new";

    private static readonly ConcurrentDictionary<string, string> ImageCache = new();
    private static readonly string[] SizeNames = { "small", "medium", "large" };

    internal static string NormalizeSize(string? size) =>
        SizeNames.FirstOrDefault(s =>
            string.Equals(s, size, StringComparison.OrdinalIgnoreCase))
        ?? "medium";

    private static object Text(string content, string size = "Default",
        string color = "Default", string weight = "Default",
        string spacing = "Small", bool subtle = false, int maxLines = 0)
    {
        var element = new Dictionary<string, object>
        {
            ["type"] = "TextBlock", ["text"] = content, ["size"] = size,
            ["color"] = color, ["weight"] = weight, ["spacing"] = spacing,
            ["wrap"] = true
        };
        if (subtle) element["isSubtle"] = true;
        if (maxLines > 0) element["maxLines"] = maxLines;
        return element;
    }

    private static object Row(params (object Item, string Width)[] columns) =>
        new
        {
            type = "ColumnSet",
            spacing = "Small",
            columns = columns.Select(c => new
            {
                type = "Column", width = c.Width,
                items = new object[] { c.Item }
            }).ToArray()
        };

    private static object Panel(params object[] items) =>
        new { type = "Container", style = "emphasis",
              spacing = "Medium", items };

    private static object Icon(string name, string alt, int pixels = 24)
    {
        // Adaptive Cards 1.2+ supports self-contained PNG data URIs.
        // Small PNGs are generated on the CI runner and packaged in MSIX.
        string url = ImageCache.GetOrAdd(name, n =>
        {
            var path = Path.Combine(AppContext.BaseDirectory, "ProviderAssets",
                "UI_" + n + ".png");
            try
            {
                return File.Exists(path)
                    ? "data:image/png;base64," +
                      Convert.ToBase64String(File.ReadAllBytes(path))
                    : "";
            }
            catch (IOException) { return ""; }
            catch (UnauthorizedAccessException) { return ""; }
        });

        // The fallback remains usable if artwork is absent in a local
        // developer test. No external requests are performed.
        if (url.Length == 0)
            return Text("◆", size: "Medium", color: "Accent", weight: "Bolder");
        return new
        {
            type = "Image", url, altText = alt,
            width = pixels + "px", height = pixels + "px",
            spacing = "None"
        };
    }

    private static object IconLabel(string icon, string label, int pixels = 24) =>
        Row((Icon(icon, label, pixels), "auto"),
            (Text(label, weight: "Bolder", spacing: "None"), "stretch"));

    private static object Dot(string caption, string color, bool compact = false) =>
        Row((Text("●", size: "Medium", color: color, spacing: "None"), "auto"),
            (Text(caption, size: compact ? "Small" : "Default",
                weight: "Bolder", spacing: "None"), "stretch"));

    private static object Footer(StateProbe.Snapshot snapshot, bool details = false) =>
        Text((details ? "OSTATNI ODCZYT  ·  " : "Sprawdzono  ") +
             snapshot.CheckedAt.ToString("HH:mm:ss"),
             "Small", spacing: "Medium", subtle: true);

    private static Dictionary<string, object> Execute(string title, string verb) =>
        new()
        {
            ["type"] = "Action.Execute", ["title"] = title, ["verb"] = verb,
            ["$when"] = "${$host.isUserContextAuthenticated}"
        };

    private static Dictionary<string, object> Link(string title, string url) =>
        new()
        {
            ["type"] = "Action.OpenUrl", ["title"] = title, ["url"] = url
        };

    private static string Serialize(List<object> body,
        List<Dictionary<string, object>>? actions = null) =>
        JsonSerializer.Serialize(new
        {
            type = "AdaptiveCard",
            version = "1.5",
            body,
            actions = actions ?? new List<Dictionary<string, object>>()
        });

    internal static string Initial(string definition, string size)
    {
        // Keep the synchronous first content that solved the COM pinning bug.
        // This is only for newly pinned widgets, not for each board reopen.
        bool wsl = definition == WidgetCards.WslId;
        var body = new List<object>
        {
            IconLabel(wsl ? "WSL" : "DevStack", wsl ? "Ubuntu" : "DevStack", 26),
            Text("Sprawdzanie stanu…", color: "Accent", spacing: "Medium"),
            Text(wsl ? "WSL2 · Ubuntu 26.04" : "Lokalne środowisko",
                "Small", subtle: true)
        };
        return Serialize(body);
    }

    internal static string Wsl(StateProbe.Snapshot snapshot, string size,
        string message = "", bool pendingStop = false, bool actionBusy = false)
    {
        size = NormalizeSize(size);
        var body = new List<object>();
        var running = snapshot.WslRunning;
        string headline = running switch
        {
            true => "Uruchomione",
            false => "Wyłączone",
            _ => "Stan nieznany"
        };
        string tone = running switch
        {
            true => "Good",
            false => "Warning",
            _ => "Attention"
        };

        if (size == "small")
        {
            body.Add(Row((Icon("WSL", "Ubuntu", 27), "auto"),
                (Text("WSL  ·  UBUNTU", "Small", "Accent", "Bolder",
                    "None"), "stretch")));
            body.Add(Text(headline, "Large", tone, "Bolder", "Medium"));
            body.Add(Text("Ubuntu 26.04", "Small", spacing: "None", subtle: true));
        }
        else if (size == "medium")
        {
            body.Add(Row((Icon("WSL", "Ubuntu", 35), "auto"),
                (Text("WSL2  /  UBUNTU 26.04", "Small", "Accent",
                    "Bolder", "None"), "stretch")));
            body.Add(Panel(
                Text("STATUS SYSTEMU", "Small", spacing: "None", subtle: true),
                Dot(headline, tone),
                Text("Dystrybucja Ubuntu 26.04", "Small",
                    spacing: "Small", subtle: true)));
            body.Add(Footer(snapshot));
        }
        else
        {
            body.Add(Row((Icon("WSL", "Ubuntu", 44), "auto"),
                (Text("WSL2  /  LINUX", "Small", "Accent",
                    "Bolder", "None"), "stretch")));
            body.Add(Text(headline, "ExtraLarge", tone, "Bolder", "Medium"));
            body.Add(Dot(running == true ? "Ubuntu jest gotowe do pracy" :
                running == false ? "Środowisko jest zatrzymane" :
                "Nie można odczytać stanu", tone, true));
            body.Add(Panel(
                Row((Text("DYSTRYBUCJA", "Small", subtle: true), "stretch"),
                    (Text("TRYB", "Small", subtle: true), "stretch")),
                Row((Text("Ubuntu 26.04", "Default", weight: "Bolder"), "stretch"),
                    (Text("Ręczne sterowanie", "Small"), "stretch"))));
            body.Add(Footer(snapshot, details: true));
        }

        if (!string.IsNullOrWhiteSpace(message))
            body.Add(Text(message, "Small", "Warning", spacing: "Medium"));
        else if (running is null && !string.IsNullOrWhiteSpace(snapshot.WslError))
            body.Add(Text(snapshot.WslError, "Small", "Attention"));

        var actions = new List<Dictionary<string, object>>();
        if (actionBusy)
            body.Add(Dot("Trwa wykonywanie polecenia", "Accent", true));
        else if (running == false)
            actions.Add(Execute(size == "small" ? "Uruchom" : "Uruchom Ubuntu", "start"));
        else if (running == true)
        {
            if (pendingStop)
            {
                body.Add(Text("Zatrzymanie zakończy procesy Ubuntu. Potwierdź.",
                    "Small", "Attention", spacing: "Medium"));
                actions.Add(Execute("Tak, zatrzymaj", "stop-confirm"));
                actions.Add(Execute("Anuluj", "stop-cancel"));
            }
            else
                actions.Add(Execute(size == "small" ? "Zatrzymaj" : "Zatrzymaj Ubuntu",
                    "stop-ask"));
        }

        return Serialize(body, actions);
    }

    private sealed record Service(string Label, string IconName, bool? Reachable);

    private static object ServiceTile(Service service, bool compact)
    {
        var state = service.Reachable switch
        {
            true => "OK",
            false => "Brak połączenia",
            _ => "Brak danych"
        };
        var tone = service.Reachable switch
        {
            true => "Good",
            false => "Attention",
            _ => "Warning"
        };

        return Panel(
            Row((Icon(service.IconName, service.Label,
                compact ? 20 : 26), "auto"),
                (Text(service.Label, compact ? "Small" : "Default",
                    weight: "Bolder", spacing: "None"), "stretch")),
            Dot(state, tone, compact: true));
    }

    internal static string DevStack(StateProbe.Snapshot snapshot, string size)
    {
        size = NormalizeSize(size);
        var body = new List<object>();
        var running = snapshot.WslRunning;
        var services = new[]
        {
            new Service("Nginx", "Nginx", snapshot.Nginx),
            new Service("PHP-FPM", "PHP", snapshot.PhpFpm),
            new Service("MariaDB", "MariaDB", snapshot.MariaDb),
            new Service("Redis", "Redis", snapshot.Redis)
        };
        var available = services.Count(s => s.Reachable == true);
        string status = running switch
        {
            false => "Środowisko offline",
            null => "Stan nieznany",
            true when services.All(s => s.Reachable.HasValue) && available == 4 =>
                "Wszystko działa",
            true => available + "/4 usługi dostępne"
        };
        string tone = running switch
        {
            false => "Warning",
            null => "Attention",
            true when available == 4 => "Good",
            true => "Warning"
        };

        if (size == "small")
        {
            body.Add(Row((Icon("DevStack", "DevStack", 26), "auto"),
                (Text("DEVSTACK  /  LOCAL", "Small", "Accent",
                    "Bolder", "None"), "stretch")));
            body.Add(Text(running == true && available == 4 ? "4/4 online" :
                running == true ? available + "/4 online" : "Offline",
                "Large", tone, "Bolder", "Medium"));
            body.Add(Text(running == true ? "Usługi środowiska" :
                "Uruchom Ubuntu", "Small", subtle: true));
            body.Add(Footer(snapshot));
        }
        else if (size == "medium")
        {
            body.Add(Row((Icon("DevStack", "DevStack", 33), "auto"),
                (Text("DEVSTACK  /  LOCAL", "Small", "Accent",
                    "Bolder", "None"), "stretch")));
            body.Add(Dot(status, tone));
            if (running == true)
            {
                body.Add(Row((ServiceTile(services[0], true), "stretch"),
                    (ServiceTile(services[1], true), "stretch")));
                body.Add(Row((ServiceTile(services[2], true), "stretch"),
                    (ServiceTile(services[3], true), "stretch")));
            }
            else
                body.Add(Panel(Text(running == false ?
                    "Ubuntu jest wyłączone. Usługi nie są sprawdzane." :
                    "Nie można odczytać stanu Ubuntu.",
                    "Small", spacing: "None")));
            body.Add(Footer(snapshot));
        }
        else
        {
            body.Add(Row((Icon("DevStack", "DevStack", 44), "auto"),
                (Text("DEVSTACK  /  ŚRODOWISKO", "Small", "Accent",
                    "Bolder", "None"), "stretch")));
            body.Add(Text(status, "ExtraLarge", tone, "Bolder", "Medium"));
            body.Add(Text(running == true ?
                "Dostępność usług z poziomu Windows" :
                "Środowisko czeka na uruchomienie WSL",
                "Small", subtle: true));

            if (running == true)
            {
                body.Add(Row((ServiceTile(services[0], false), "stretch"),
                    (ServiceTile(services[1], false), "stretch")));
                body.Add(Row((ServiceTile(services[2], false), "stretch"),
                    (ServiceTile(services[3], false), "stretch")));
                body.Add(Text("Wskaźniki oznaczają połączenie HTTPS/TCP, " +
                    "a nie wynik systemctl.", "Small", subtle: true));
            }
            else
                body.Add(Panel(
                    Dot("WSL nieaktywne", tone),
                    Text("Uruchom Ubuntu w widżecie WSL. " +
                        "DevStack nie uruchamia go sam.", "Small", subtle: true)));
            body.Add(Footer(snapshot, details: true));
        }

        var actions = new List<Dictionary<string, object>>();
        if (running == true)
        {
            if (size != "small")
                actions.Add(Link("Dodaj projekt", NewProjectUrl));
            actions.Add(Link(size == "small" ? "Projekty" : "Przeglądaj projekty",
                ProjectsUrl));
        }
        return Serialize(body, actions);
    }
}
