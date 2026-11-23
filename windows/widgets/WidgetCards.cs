using System.Text.Json;

namespace DevStack.Widgets;

internal static class WidgetCards
{
    internal const string WslId = "DevStack_WSL";
    internal const string ServicesId = "DevStack_Services";

    // The first card must be available synchronously in CreateWidget, before
    // querying WSL or loading external template files. This small, static card
    // also isolates pinning problems from probe/Adaptive Card rendering issues.
    internal static string InitialTemplate(string definitionId)
    {
        var title = definitionId == WslId ? "WSL — Ubuntu" : "DevStack — usługi";
        var message = definitionId == WslId
            ? "Odczytywanie stanu Ubuntu..."
            : "Odczytywanie dostępności usług DevStack...";
        return System.Text.Json.JsonSerializer.Serialize(new
        {
            type = "AdaptiveCard",
            version = "1.5",
            body = new object[]
            {
                new { type = "TextBlock", text = title, weight = "Bolder" },
                new { type = "TextBlock", text = message, wrap = true }
            }
        });
    }

    // Stage C4.1: live, Windows-only WSL state; no WSL lifecycle buttons yet.
    // Inline JSON proved reliable in the static ASUS pinning test. Do not
    // introduce template files or dynamic Adaptive Card bindings at this step.
    internal static string WslReadOnlyTemplate(StateProbe.Snapshot snapshot)
    {
        var status = snapshot.WslRunning switch
        {
            true => "Ubuntu uruchomione",
            false => "Ubuntu wyłączone",
            _ => "Stan WSL nieznany"
        };
        var details = snapshot.WslRunning.HasValue
            ? "Dystrybucja: " + StateProbe.Distro
            : string.IsNullOrWhiteSpace(snapshot.WslError)
                ? "Nie można ustalić stanu Ubuntu."
                : snapshot.WslError;

        return JsonSerializer.Serialize(new
        {
            type = "AdaptiveCard",
            version = "1.5",
            body = new object[]
            {
                new { type = "TextBlock", text = "WSL — Ubuntu", weight = "Bolder", wrap = true },
                new { type = "TextBlock", text = status, size = "Large", wrap = true },
                new { type = "TextBlock", text = details, wrap = true },
                new {
                    type = "TextBlock",
                    text = "Ostatnia kontrola: " + snapshot.CheckedAt.ToString("HH:mm:ss"),
                    isSubtle = true,
                    wrap = true
                }
            }
        });
    }

    // C4.3: native Windows Widgets Action.Execute controls. No service
    // commands are exposed by the DevStack card. Stop requires a second click.
    // Actions are shown only when the Windows widget user is authenticated.
    internal static string WslControlTemplate(
        StateProbe.Snapshot snapshot, string message = "",
        bool pendingStop = false, bool actionBusy = false)
    {
        var status = snapshot.WslRunning switch
        {
            true => "Ubuntu uruchomione",
            false => "Ubuntu wyłączone",
            _ => "Stan WSL nieznany"
        };

        var body = new List<object>
        {
            new { type = "TextBlock", text = "WSL — Ubuntu", weight = "Bolder", wrap = true },
            new { type = "TextBlock", text = status, size = "Large", wrap = true },
            new { type = "TextBlock", text = "Dystrybucja: " + StateProbe.Distro, wrap = true }
        };

        if (!string.IsNullOrWhiteSpace(message))
            body.Add(new { type = "TextBlock", text = message, wrap = true });

        if (pendingStop && snapshot.WslRunning == true && !actionBusy)
            body.Add(new
            {
                type = "TextBlock",
                text = "Uwaga: zatrzymanie Ubuntu zakończy wszystkie uruchomione procesy. Potwierdź w ciągu 30 sekund.",
                color = "Attention",
                wrap = true
            });

        body.Add(new
        {
            type = "TextBlock",
            text = "Ostatnia kontrola: " + snapshot.CheckedAt.ToString("HH:mm:ss"),
            isSubtle = true,
            wrap = true
        });

        var actions = new List<Dictionary<string, object>>();
        if (!actionBusy)
        {
            if (snapshot.WslRunning == false)
                actions.Add(Execute("Uruchom Ubuntu", "start"));
            else if (snapshot.WslRunning == true)
            {
                if (pendingStop)
                {
                    actions.Add(Execute("Tak, zatrzymaj", "stop-confirm"));
                    actions.Add(Execute("Anuluj", "stop-cancel"));
                }
                else
                    actions.Add(Execute("Zatrzymaj Ubuntu", "stop-ask"));
            }
            // When status is unknown, do not offer potentially unsafe actions.
        }

        return JsonSerializer.Serialize(new
        {
            type = "AdaptiveCard",
            version = "1.5",
            body,
            actions
        });
    }

    private static Dictionary<string, object> Execute(string title, string verb) =>
        new()
        {
            ["type"] = "Action.Execute",
            ["title"] = title,
            ["verb"] = verb,
            ["$when"] = "${$host.isUserContextAuthenticated}"
        };

    // Stage C4.2: only report network reachability from Windows. These are
    // connectivity hints, not authenticated systemctl state. When WSL is
    // offline, show an offline card without contacting the services.
    internal static string DevStackReadOnlyTemplate(StateProbe.Snapshot snapshot)
    {
        var body = new List<object>
        {
            new { type = "TextBlock", text = "DevStack — usługi", weight = "Bolder", wrap = true }
        };

        if (snapshot.WslRunning == false)
        {
            body.Add(new
            {
                type = "TextBlock",
                text = "WSL wyłączony — DevStack niedostępny",
                wrap = true
            });
        }
        else if (snapshot.WslRunning != true)
        {
            body.Add(new
            {
                type = "TextBlock",
                text = "Nie można ustalić stanu WSL — nie sprawdzono usług",
                wrap = true
            });
            if (!string.IsNullOrWhiteSpace(snapshot.WslError))
                body.Add(new { type = "TextBlock", text = snapshot.WslError, wrap = true });
        }
        else
        {
            static string Label(bool? reachable) => reachable switch
            {
                true => "Odpowiada",
                false => "Brak połączenia",
                _ => "Brak danych"
            };

            body.Add(new { type = "TextBlock", text = "Ubuntu działa — test połączeń", wrap = true });
            body.Add(new { type = "TextBlock", text = "Nginx: " + Label(snapshot.Nginx), wrap = true });
            body.Add(new { type = "TextBlock", text = "PHP-FPM: " + Label(snapshot.PhpFpm), wrap = true });
            body.Add(new { type = "TextBlock", text = "MariaDB: " + Label(snapshot.MariaDb), wrap = true });
            body.Add(new { type = "TextBlock", text = "Redis: " + Label(snapshot.Redis), wrap = true });
            body.Add(new
            {
                type = "TextBlock",
                text = "Statusy oznaczają dostępność HTTPS/TCP z Windows, nie wynik systemctl.",
                isSubtle = true,
                wrap = true
            });
        }

        body.Add(new
        {
            type = "TextBlock",
            text = "Ostatnia kontrola: " + snapshot.CheckedAt.ToString("HH:mm:ss"),
            isSubtle = true,
            wrap = true
        });

        // The ASUS C4.1 inline template path works reliably. No dynamic card
        // expressions or Action.Execute controls in this stage.
        return JsonSerializer.Serialize(new { type = "AdaptiveCard", version = "1.5", body });
    }

    private static readonly Lazy<string> WslTemplate = new(() => ReadTemplate("WSL.json"));
    private static readonly Lazy<string> ServicesTemplate = new(() => ReadTemplate("DevStack.json"));

    internal static string Template(string definitionId) =>
        definitionId == WslId ? WslTemplate.Value : ServicesTemplate.Value;

    private static string ReadTemplate(string file) =>
        File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Templates", file));

    internal static string Data(string definitionId, StateProbe.Snapshot snapshot,
        string message, bool pendingStop)
    {
        var running = snapshot.WslRunning == true;
        var status = snapshot.WslRunning switch
        {
            true => "Ubuntu uruchomione",
            false => "Ubuntu wyłączone",
            _ => "Stan WSL nieznany"
        };

        if (definitionId == WslId)
        {
            return JsonSerializer.Serialize(new
            {
                status,
                details = message.Length > 0 ? message : snapshot.WslError.Length > 0
                    ? snapshot.WslError : "Dystrybucja: " + StateProbe.Distro,
                running,
                unknown = !snapshot.WslRunning.HasValue,
                pendingStop,
                checkedAt = snapshot.CheckedAt.ToString("HH:mm:ss")
            });
        }

        string label(bool? value) => !running ? "—" : value == true ? "Odpowiada" : "Brak połączenia";
        return JsonSerializer.Serialize(new
        {
            status = running ? "Ubuntu działa — test połączeń" : "WSL wyłączony — DevStack niedostępny",
            running,
            nginx = label(snapshot.Nginx),
            php = label(snapshot.PhpFpm),
            mariadb = label(snapshot.MariaDb),
            redis = label(snapshot.Redis),
            checkedAt = snapshot.CheckedAt.ToString("HH:mm:ss")
        });
    }
}
