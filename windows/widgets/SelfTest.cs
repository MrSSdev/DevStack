using System.Text.Json;

namespace DevStack.Widgets;

/// <summary>Small dependency-free checks runnable on a GitHub Windows runner.</summary>
internal static class SelfTest
{
    internal static int Run()
    {
        try
        {
            Assert(StateProbe.ParseRunningDistros("Ubuntu-26.04\r\nOther\r\n", "Ubuntu-26.04"), "running distro");
            Assert(StateProbe.ParseRunningDistros("U\0b\0u\0n\0t\0u\0-\02\06\0.\00\04\0", "Ubuntu-26.04"), "utf16-style distro list");
            Assert(!StateProbe.ParseRunningDistros("Ubuntu-24.04\r\n", "Ubuntu-26.04"), "different distro");
            Assert(!StateProbe.ParseRunningDistros("", "Ubuntu-26.04"), "empty distro list");

            // COM LocalServer32 can append -Embedding after MSIX ExeServer Arguments.
            // This must start registration; otherwise Windows reports DCOM 10010.
            Assert(WidgetServerLaunch.IsComActivation(
                new[] { "-RegisterProcessAsComServer" }), "COM manifest argument");
            Assert(WidgetServerLaunch.IsComActivation(
                new[] { "-RegisterProcessAsComServer", "-Embedding" }), "COM appended -Embedding");
            Assert(WidgetServerLaunch.IsComActivation(
                new[] { "-Embedding" }), "COM default -Embedding");
            Assert(WidgetServerLaunch.IsComActivation(
                new[] { "/Embedding" }), "COM slash Embedding");
            Assert(WidgetServerLaunch.IsComActivation(
                new[] { "-registerprocessascomserver", "-embedding" }), "COM case insensitive");
            Assert(!WidgetServerLaunch.IsComActivation(Array.Empty<string>()), "no COM activation without flags");
            Assert(!WidgetServerLaunch.IsComActivation(
                new[] { "--self-test" }), "self-test is not COM activation");

            var offline = new StateProbe.Snapshot(false, "", null, null, null, null, DateTimeOffset.Now);
            using var status = JsonDocument.Parse(WidgetCards.Data(WidgetCards.ServicesId, offline, "", false));
            Assert(!status.RootElement.GetProperty("running").GetBoolean(), "DevStack offline state");
            Assert(status.RootElement.GetProperty("nginx").GetString() == "—", "no false status while WSL off");

            // The C4.1 card is intentionally read-only: no action buttons,
            // no dynamic templating, no service probe and no WSL startup.
            foreach (var snapshot in new[]
            {
                offline,
                new StateProbe.Snapshot(true, "", null, null, null, null, DateTimeOffset.Now),
                new StateProbe.Snapshot(null, "Błąd odczytu WSL", null, null, null, null, DateTimeOffset.Now)
            })
            {
                using var live = JsonDocument.Parse(WidgetCards.WslReadOnlyTemplate(snapshot));
                Assert(live.RootElement.GetProperty("type").GetString() == "AdaptiveCard", "C4.1 card format");
                Assert(!live.RootElement.TryGetProperty("actions", out _), "C4.1 has no action buttons");
                var statusText = live.RootElement.GetProperty("body")[1].GetProperty("text").GetString();
                Assert(statusText == (snapshot.WslRunning == true
                    ? "Ubuntu uruchomione"
                    : snapshot.WslRunning == false ? "Ubuntu wyłączone" : "Stan WSL nieznany"),
                    "C4.1 WSL status label");
            }

            foreach (var name in new[] { WidgetCards.WslId, WidgetCards.ServicesId })
            {
                using var template = JsonDocument.Parse(WidgetCards.Template(name));
                Assert(template.RootElement.GetProperty("type").GetString() == "AdaptiveCard", "widget template");
                using var firstCard = JsonDocument.Parse(WidgetCards.InitialTemplate(name));
                Assert(firstCard.RootElement.GetProperty("type").GetString() == "AdaptiveCard", "static initial card");
                Assert(firstCard.RootElement.GetProperty("body")[0].GetProperty("text").GetString() is string title
                    && title.Length > 0, "static card title");
            }
            return 0;
        }
        catch (Exception error)
        {
            Console.Error.WriteLine(error);
            return 1;
        }
    }

    private static void Assert(bool value, string message)
    {
        if (!value) throw new InvalidOperationException("Self-test failed: " + message);
    }
}
