using System.Text.Json;

namespace DevStack.Widgets;

internal static class SelfTest
{
    private static int _checks;
    internal static int Run()
    {
        try
        {
            Assert(StateProbe.ParseRunningDistros("Ubuntu-26.04\r\nOther\r\n", "Ubuntu-26.04"), "running distro");
            Assert(StateProbe.ParseRunningDistros("U\0b\0u\0n\0t\0u\0-\02\06\0.\00\04\0", "Ubuntu-26.04"), "UTF16-style list");
            Assert(!StateProbe.ParseRunningDistros("Ubuntu-24.04", "Ubuntu-26.04"), "another distro");
            Assert(!StateProbe.ParseRunningDistros("", "Ubuntu-26.04"), "empty list");
            foreach (var flags in new[]
            {
                new[] { "-RegisterProcessAsComServer" }, new[] { "-RegisterProcessAsComServer", "-Embedding" },
                new[] { "-Embedding" }, new[] { "/Embedding" }, new[] { "-registerprocessascomserver", "-embedding" }
            }) Assert(WidgetServerLaunch.IsComActivation(flags), "COM launch flags");
            Assert(!WidgetServerLaunch.IsComActivation(Array.Empty<string>()), "no flags");
            Assert(!WidgetServerLaunch.IsComActivation(new[] { "--self-test" }), "test is not COM");

            var time = new DateTimeOffset(2026, 11, 23, 23, 36, 32, TimeSpan.FromHours(1));
            var off = new StateProbe.Snapshot(false, "", null, null, null, null, time);
            var on = new StateProbe.Snapshot(true, "", true, true, true, true, time);
            var partial = new StateProbe.Snapshot(true, "", true, false, true, false, time);
            var pending = new StateProbe.Snapshot(true, "", null, null, null, null, time);
            var unknown = new StateProbe.Snapshot(null, "Odczyt niedostępny", null, null, null, null, time);
            using (var closed = new CancellationTokenSource())
            {
                closed.Cancel();
                var skipped = StateProbe.CaptureServicesAfterWslAsync(off, closed.Token).GetAwaiter().GetResult();
                Assert(ReferenceEquals(skipped, off), "offline short circuit without network");
                bool canceled = false;
                try { StateProbe.CaptureServicesAfterWslAsync(on, closed.Token).GetAwaiter().GetResult(); }
                catch (OperationCanceledException) { canceled = true; }
                Assert(canceled, "hidden session cancels probes");
            }
            foreach (var size in new string?[] { "small", "medium", "large", "SMALL", null })
                Assert(WidgetDesign.NormalizeSize(size) == "large", "large-only renderer");

            var fixtures = new Dictionary<string, string>
            {
                ["ubuntu-online"] = WidgetDesign.Wsl(on, "large"),
                ["ubuntu-offline"] = WidgetDesign.Wsl(off, "large"),
                ["ubuntu-unknown"] = WidgetDesign.Wsl(unknown, "large"),
                ["ubuntu-busy"] = WidgetDesign.Wsl(on, "large", "Uruchamianie Ubuntu...", actionBusy: true),
                ["ubuntu-confirm"] = WidgetDesign.Wsl(on, "large", pendingStop: true),
                ["ubuntu-error"] = WidgetDesign.Wsl(on, "large", "Potwierdzenie wygasło. Wybierz Zatrzymaj ponownie."),
                ["devstack-online"] = WidgetDesign.DevStack(on, "large"),
                ["devstack-offline"] = WidgetDesign.DevStack(off, "large"),
                ["devstack-unknown"] = WidgetDesign.DevStack(unknown, "large"),
                ["devstack-partial"] = WidgetDesign.DevStack(partial, "large"),
                ["devstack-checking"] = WidgetDesign.DevStack(pending, "large"),
                ["ubuntu-initial"] = WidgetDesign.Initial(WidgetCards.WslId, "large"),
                ["devstack-initial"] = WidgetDesign.Initial(WidgetCards.ServicesId, "large")
            };
            foreach (var (name, json) in fixtures)
            {
                using var doc = JsonDocument.Parse(json);
                var root = doc.RootElement;
                var nodes = Walk(root).ToArray();
                Assert(root.GetProperty("type").GetString() == "AdaptiveCard", name + " format");
                Assert(root.GetProperty("version").GetString() == "1.5", name + " host version");
                Assert(!root.TryGetProperty("webUrl", out _), name + " native only");
                Assert(!root.TryGetProperty("actions", out _), name + " no buttons after footer");
                var body = root.GetProperty("body");
                var bottom = body[body.GetArrayLength() - 1];
                Assert(bottom.GetProperty("id").GetString() == "bottom-dock", name + " final dock");
                Assert(bottom.GetProperty("height").GetString() == "stretch", name + " flexible dock");
                Assert(bottom.GetProperty("verticalContentAlignment").GetString() == "bottom", name + " bottom alignment");
                var items = bottom.GetProperty("items");
                Assert(items[items.GetArrayLength() - 1].GetProperty("id").GetString() == "sync-row", name + " timestamp last");
                var ids = nodes.Where(n => n.TryGetProperty("id", out _)).Select(n => n.GetProperty("id").GetString()).ToArray();
                Assert(ids.Distinct().Count() == ids.Length, name + " unique IDs");
                foreach (var n in nodes)
                {
                    foreach (var invalid in new[] { "padding", "margin", "borderRadius", "boxShadow" })
                        Assert(!n.TryGetProperty(invalid, out _), name + " no unsupported CSS");
                    if (!n.TryGetProperty("type", out var type)) continue;
                    switch (type.GetString())
                    {
                        case "Image":
                            Assert(n.GetProperty("url").GetString()!.StartsWith("data:image/png;base64,", StringComparison.Ordinal), name + " embedded icon");
                            break;
                        case "Action.Execute":
                            Assert(name.StartsWith("ubuntu-", StringComparison.Ordinal), "DevStack cannot control WSL");
                            Assert(n.GetProperty("$when").GetString() == "${$host.isUserContextAuthenticated}", "auth guard retained");
                            break;
                        case "Action.OpenUrl":
                            Assert(name.StartsWith("devstack-", StringComparison.Ordinal), "navigation only on DevStack");
                            Assert(new[] { WidgetDesign.ProjectsUrl, WidgetDesign.NewProjectUrl }.Contains(n.GetProperty("url").GetString()), "allowlisted deep links");
                            break;
                        case "TextBlock":
                            Assert(n.GetProperty("size").GetString() != "extraLarge", "no billboard type");
                            if (n.GetProperty("size").GetString() == "large")
                                Assert(n.GetProperty("color").GetString() == "default", "neutral main headings");
                            break;
                    }
                }
            }
            Assert(Verbs(fixtures["ubuntu-online"]).SequenceEqual(new[] { "stop-ask" }), "Stop request only");
            Assert(Verbs(fixtures["ubuntu-offline"]).SequenceEqual(new[] { "start" }), "Start only");
            Assert(Verbs(fixtures["ubuntu-confirm"]).SequenceEqual(new[] { "stop-confirm", "stop-cancel" }), "two-step Stop");
            Assert(Verbs(fixtures["ubuntu-busy"]).Length == 0, "busy rejects clicks");
            Assert(Verbs(fixtures["ubuntu-unknown"]).Length == 0, "unknown rejects controls");
            foreach (var key in new[] { "devstack-offline", "devstack-unknown" })
            {
                using var d = JsonDocument.Parse(fixtures[key]);
                Assert(!Walk(d.RootElement).Any(n => n.TryGetProperty("type", out var t) && t.GetString()!.StartsWith("Action.", StringComparison.Ordinal)), key + " no actions");
            }
            string? export = Environment.GetEnvironmentVariable("DEVSTACK_WIDGET_PREVIEWS");
            if (!string.IsNullOrWhiteSpace(export))
            {
                Directory.CreateDirectory(export);
                foreach (var (name, json) in fixtures) File.WriteAllText(Path.Combine(export, name + ".json"), json);
            }
            Console.WriteLine($"PASS: {_checks} checks; {fixtures.Count} large-card state fixtures.");
            return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }

    private static IEnumerable<JsonElement> Walk(JsonElement element)
    {
        if (element.ValueKind == JsonValueKind.Object)
        {
            yield return element;
            foreach (var p in element.EnumerateObject()) foreach (var n in Walk(p.Value)) yield return n;
        }
        else if (element.ValueKind == JsonValueKind.Array)
            foreach (var child in element.EnumerateArray()) foreach (var n in Walk(child)) yield return n;
    }
    private static string[] Verbs(string json)
    {
        using var d = JsonDocument.Parse(json);
        return Walk(d.RootElement).Where(n => n.TryGetProperty("verb", out _)).Select(n => n.GetProperty("verb").GetString()!).ToArray();
    }
    private static void Assert(bool value, string message)
    {
        _checks++;
        if (!value) throw new InvalidOperationException("Self-test failed: " + message);
    }
}
