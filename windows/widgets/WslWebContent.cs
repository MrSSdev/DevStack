using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Windows.Widgets.Providers;
using Windows.Storage.Streams;

namespace DevStack.Widgets;

/// <summary>Packaged HTML, not a Linux-hosted page; all resources are allowlisted.</summary>
internal static class WslWebContent
{
    internal const string Origin = "https://devstack-widgets.invalid";
    internal const string Filter = Origin + "/*";
    private static readonly Dictionary<string, (string File, string Type)> Resources = new()
    {
        ["/wsl/index.html"] = ("index.html", "text/html; charset=utf-8"),
        ["/wsl/wsl.css"] = ("wsl.css", "text/css; charset=utf-8"),
        ["/wsl/wsl.js"] = ("wsl.js", "text/javascript; charset=utf-8")
    };
    internal static string Template(string nonce, StateProbe.Snapshot? snapshot)
    {
        var card = new JsonObject
        {
            ["type"] = "AdaptiveCard", ["version"] = "1.6", ["header"] = null,
            ["body"] = new JsonArray
            {
                new JsonObject { ["type"] = "TextBlock", ["text"] = "Ubuntu", ["weight"] = "Bolder" },
                new JsonObject { ["type"] = "TextBlock", ["wrap"] = true,
                    ["text"] = "Ten panel Windows nie udostepnia zawartosci HTML. To widok zastepczy, nie zatwierdzony prototyp.", ["color"] = "Warning" },
                new JsonObject { ["type"] = "TextBlock", ["wrap"] = true,
                    ["text"] = "Wymagany host Web Widgets w EOG. Zaktualizuj Windows Web Experience Pack i otworz panel ponownie." }
            },
            ["metadata"] = new JsonObject
            {
                ["webUrl"] = Origin + "/wsl/index.html?session=" + nonce +
                    "&theme=${$host.hostTheme}&auth=${$host.isUserContextAuthenticated}"
            }
        };
        return card.ToJsonString();
    }

    internal static Dictionary<string, string> Query(Uri uri) => uri.Query.TrimStart('?')
        .Split('&', StringSplitOptions.RemoveEmptyEntries)
        .Select(p => p.Split('=', 2))
        .GroupBy(p => Uri.UnescapeDataString(p[0]), StringComparer.Ordinal)
        .Where(g => g.Count() == 1)
        .ToDictionary(g => g.Key, g => g.First().Length > 1 ? Uri.UnescapeDataString(g.First()[1]) : "", StringComparer.Ordinal);

    internal static bool IsLocal(Uri uri) => uri.Scheme == "https" &&
        uri.Host == "devstack-widgets.invalid" && uri.IsDefaultPort && string.IsNullOrEmpty(uri.UserInfo);

    internal static void Respond(WidgetResourceRequestedArgs args, Uri? uri, bool allowed)
    {
        // Always supply a response, including failures: never fall through to DNS/network.
        var good = allowed && uri is not null && IsLocal(uri) && Resources.TryGetValue(uri.AbsolutePath, out _);
        var entry = good ? Resources[uri!.AbsolutePath] : ("unavailable.html", "text/html; charset=utf-8");
        var stream = RandomAccessStreamReference.CreateFromUri(new Uri("ms-appx:///Web/" + entry.Item1));
        var response = new WidgetResourceResponse(stream, good ? "OK" : "Forbidden", good ? 200 : 403);
        response.Headers.Add("Content-Type", entry.Item2);
        response.Headers.Add("Cache-Control", "no-store");
        response.Headers.Add("X-Content-Type-Options", "nosniff");
        response.Headers.Add("Referrer-Policy", "no-referrer");
        response.Headers.Add("Content-Security-Policy", "default-src 'none'; style-src 'self'; script-src 'self'; img-src data: 'self'; connect-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'; object-src 'none'");
        args.Response = response;
    }
}
