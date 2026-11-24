using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Windows.Widgets.Providers;
using Windows.Storage.Streams;

namespace DevStack.Widgets;

/// <summary>Versioned, self-contained local page. Never fall through to the Internet.</summary>
internal static class WslWebContent
{
    internal const string Origin = "https://devstack-widgets.invalid";
    internal const string Filter = Origin + "/*";
    internal const string Version = "0.3.4";
    private static readonly Dictionary<string, (string File, string Type)> Resources = new()
    {
        ["/wsl/index.html"] = ("index.html", "text/html; charset=utf-8"),
        ["/wsl/wsl.css"] = ("wsl.css", "text/css; charset=utf-8"),
        ["/wsl/wsl.js"] = ("wsl.js", "text/javascript; charset=utf-8")
    };
    internal static string Template(string nonce, StateProbe.Snapshot? snapshot)
    {
        return new JsonObject
        {
            ["type"] = "AdaptiveCard", ["version"] = "1.6", ["header"] = null,
            ["body"] = new JsonArray
            {
                new JsonObject { ["type"] = "TextBlock", ["text"] = "Ubuntu", ["weight"] = "Bolder" },
                new JsonObject { ["type"] = "TextBlock", ["wrap"] = true,
                    ["text"] = "Ten panel Windows nie udostepnia zawartosci HTML. To widok zastepczy, nie zatwierdzony prototyp.", ["color"] = "Warning" }
            },
            ["metadata"] = new JsonObject
            {
                ["webUrl"] = Origin + "/wsl/index.html?v=" + Version + "&session=" + nonce +
                    "&theme=${$host.hostTheme}&auth=${$host.isUserContextAuthenticated}"
            }
        }.ToJsonString();
    }

    // Restore the host-owned URL instead of regenerating it at every COM restart.
    // No command capability or authentication is restored by this method.
    internal static (string? Nonce, bool Current) Resume(string? template)
    {
        if (string.IsNullOrEmpty(template) || template.Length > 32768) return (null, false);
        try
        {
            using var doc = JsonDocument.Parse(template);
            var raw = doc.RootElement.GetProperty("metadata").GetProperty("webUrl").GetString();
            if (!Uri.TryCreate(raw, UriKind.Absolute, out var uri) || !IsLocal(uri) || uri.AbsolutePath != "/wsl/index.html") return (null, false);
            var q = Query(uri); var nonce = q.GetValueOrDefault("session");
            if (nonce?.Length != 64 || nonce.Any(c => !"0123456789abcdef".Contains(c))) return (null, false);
            return (nonce, q.GetValueOrDefault("v") == Version);
        }
        catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException or UriFormatException) { return (null, false); }
    }
    internal static Dictionary<string, string> Query(Uri uri) => uri.Query.TrimStart('?')
        .Split('&', StringSplitOptions.RemoveEmptyEntries).Select(p => p.Split('=', 2))
        .GroupBy(p => Uri.UnescapeDataString(p[0]), StringComparer.Ordinal).Where(g => g.Count() == 1)
        .ToDictionary(g => g.Key, g => g.First().Length > 1 ? Uri.UnescapeDataString(g.First()[1]) : "", StringComparer.Ordinal);
    internal static bool IsLocal(Uri uri) => uri.Scheme == "https" && uri.Host == "devstack-widgets.invalid" &&
        uri.IsDefaultPort && string.IsNullOrEmpty(uri.UserInfo);
    internal static bool IsStateRead(WidgetResourceRequestedArgs args) =>
        args.Request.Headers.TryGetValue("X-DevStack-View", out var value) && value == "state";

    // A closed set of page observations, not an authorization signal.
    // Report over the already existing read-only request even if postMessage
    // itself is missing or throws. No extra polling, raw exceptions or secrets.
    internal static string? BridgeHealthCode(string? health) => health switch
    {
        "not-attempted" => "bridge-js-not-attempted",
        "missing" => "bridge-js-missing",
        "listen-error" => "bridge-js-listen-error",
        "origin-mismatch" => "bridge-js-origin-mismatch",
        "post-error" => "bridge-js-post-error",
        "posted" => "bridge-js-posted",
        "received" => "bridge-js-received",
        "connected" => "bridge-js-connected",
        _ => null
    };

    internal static async Task RespondAsync(WidgetResourceRequestedArgs args, Uri? uri, bool allowed, string bootstrap = "{}")
    {
        bool good = allowed && uri is not null && IsLocal(uri) && Resources.TryGetValue(uri.AbsolutePath, out _);
        var entry = good ? Resources[uri!.AbsolutePath] : ("unavailable.html", "text/html; charset=utf-8");
        var directory = Path.Combine(AppContext.BaseDirectory, "Web");
        WidgetResourceResponse response;
        if (good && uri!.AbsolutePath == "/wsl/index.html")
        {
            bool stateRead = IsStateRead(args);
            if (stateRead && args.Request.Headers.TryGetValue("X-DevStack-Bridge", out var health))
            {
                var code = BridgeHealthCode(health);
                if (code is not null) WidgetDiagnostics.Write(code, args.WidgetContext.Id);
            }
            var boot = JsonNode.Parse(bootstrap)?.AsObject() ?? new JsonObject();
            boot["documentUri"] = uri.AbsoluteUri;
            bootstrap = boot.ToJsonString();
            string text = stateRead ? bootstrap : (await File.ReadAllTextAsync(Path.Combine(directory, entry.Item1)))
                .Replace("<!--WSL_BOOTSTRAP-->", "<script id=\"wsl-bootstrap\" type=\"application/json\">" + bootstrap + "</script>", StringComparison.Ordinal);
            var stream = new InMemoryRandomAccessStream();
            using (var writer = new DataWriter(stream.GetOutputStreamAt(0)))
            {
                writer.WriteBytes(Encoding.UTF8.GetBytes(text));
                await writer.StoreAsync();
                writer.DetachStream();
            }
            stream.Seek(0);
            response = new WidgetResourceResponse(RandomAccessStreamReference.CreateFromStream(stream), "OK", 200);
            response.Headers.Add("Content-Type", stateRead ? "application/json; charset=utf-8" : entry.Item2);
            response.Headers.Add("Cache-Control", "private, no-cache");
            if (!stateRead) response.Headers.Add("Content-Security-Policy", await File.ReadAllTextAsync(Path.Combine(directory, "csp.txt")));
        }
        else
        {
            var content = RandomAccessStreamReference.CreateFromUri(new Uri("ms-appx:///Web/" + entry.Item1));
            response = new WidgetResourceResponse(content, good ? "OK" : "Forbidden", good ? 200 : 403);
            response.Headers.Add("Content-Type", entry.Item2);
            response.Headers.Add("Cache-Control", good ? "private, max-age=3600" : "no-store");
        }
        response.Headers.Add("X-Content-Type-Options", "nosniff");
        response.Headers.Add("Referrer-Policy", "no-referrer");
        args.Response = response;
    }
    internal static void Deny(WidgetResourceRequestedArgs args)
    {
        var response = new WidgetResourceResponse(RandomAccessStreamReference.CreateFromUri(new Uri("ms-appx:///Web/unavailable.html")), "Forbidden", 403);
        response.Headers.Add("Content-Type", "text/html; charset=utf-8");
        response.Headers.Add("Cache-Control", "no-store");
        args.Response = response;
    }
}
