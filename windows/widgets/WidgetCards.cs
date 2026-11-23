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
            : "Test C4: widżet DevStack działa. Odczyt usług jeszcze wyłączony.";
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
