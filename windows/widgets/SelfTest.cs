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

            // C4.3: verify native actions, state gating and two-step Stop.
            using (var card = JsonDocument.Parse(WidgetCards.WslControlTemplate(offline)))
            {
                var actions = card.RootElement.GetProperty("actions").EnumerateArray().ToArray();
                Assert(actions.Length == 1, "C4.3 WSL offline offers only Start");
                Assert(actions[0].GetProperty("type").GetString() == "Action.Execute",
                    "C4.3 action type");
                Assert(actions[0].GetProperty("verb").GetString() == "start",
                    "C4.3 WSL offline verb");
                Assert(actions[0].GetProperty("$when").GetString() ==
                    "${$host.isUserContextAuthenticated}", "C4.3 auth guard");
            }

            var onlineControl = new StateProbe.Snapshot(
                true, "", null, null, null, null, DateTimeOffset.Now);
            using (var card = JsonDocument.Parse(WidgetCards.WslControlTemplate(onlineControl)))
            {
                var verbs = CardActionVerbs(card.RootElement);
                Assert(verbs.SequenceEqual(new[] { "stop-ask" }),
                    "C4.3 WSL online only offers Stop request");
            }
            using (var card = JsonDocument.Parse(
                WidgetCards.WslControlTemplate(onlineControl, pendingStop: true)))
            {
                var verbs = CardActionVerbs(card.RootElement);
                Assert(verbs.SequenceEqual(new[] { "stop-confirm", "stop-cancel" }),
                    "C4.3 Stop requires second confirmation");
                Assert(ReadCardText(card.RootElement).Contains(
                    "zatrzymanie Ubuntu zakończy wszystkie uruchomione procesy",
                    StringComparison.Ordinal), "C4.3 Stop warning");
            }
            using (var card = JsonDocument.Parse(
                WidgetCards.WslControlTemplate(onlineControl, actionBusy: true)))
                Assert(CardActionVerbs(card.RootElement).Length == 0,
                    "C4.3 busy state disables all actions");

            var unknownControl = new StateProbe.Snapshot(
                null, "Błąd odczytu WSL", null, null, null, null, DateTimeOffset.Now);
            using (var card = JsonDocument.Parse(
                WidgetCards.WslControlTemplate(unknownControl)))
                Assert(CardActionVerbs(card.RootElement).Length == 0,
                    "C4.3 unknown state must not offer Start or Stop");
            using (var card = JsonDocument.Parse(
                WidgetCards.WslControlTemplate(offline, pendingStop: true)))
                Assert(CardActionVerbs(card.RootElement).SequenceEqual(new[] { "start" }),
                    "C4.3 stale Stop confirmation never enables termination offline");

            // C4.2: DevStack card must remain noninteractive and distinguish
            // offline/unknown WSL from service connectivity when Ubuntu runs.
            using (var card = JsonDocument.Parse(WidgetCards.DevStackReadOnlyTemplate(offline)))
            {
                Assert(card.RootElement.GetProperty("type").GetString() == "AdaptiveCard", "C4.2 offline format");
                Assert(!card.RootElement.TryGetProperty("actions", out _), "C4.2 offline has no actions");
                var text = ReadCardText(card.RootElement);
                Assert(text.Contains("WSL wyłączony — DevStack niedostępny", StringComparison.Ordinal),
                    "C4.2 offline message");
                Assert(!text.Contains("Nginx:", StringComparison.Ordinal),
                    "C4.2 offline must not show stale service status");
            }

            var reachable = new StateProbe.Snapshot(
                true, "", true, false, true, false, DateTimeOffset.Now);
            using (var card = JsonDocument.Parse(WidgetCards.DevStackReadOnlyTemplate(reachable)))
            {
                Assert(!card.RootElement.TryGetProperty("actions", out _), "C4.2 online has no actions");
                var text = ReadCardText(card.RootElement);
                Assert(text.Contains("Nginx: Odpowiada", StringComparison.Ordinal), "C4.2 Nginx true");
                Assert(text.Contains("PHP-FPM: Brak połączenia", StringComparison.Ordinal), "C4.2 PHP false");
                Assert(text.Contains("MariaDB: Odpowiada", StringComparison.Ordinal), "C4.2 MariaDB true");
                Assert(text.Contains("Redis: Brak połączenia", StringComparison.Ordinal), "C4.2 Redis false");
                Assert(text.Contains("nie wynik systemctl", StringComparison.Ordinal), "C4.2 reachability disclaimer");
            }

            // When the board is hidden, the provider cancels its visibility
            // token: running service probes must not start. With WSL off, the
            // probe must return immediately without touching HTTPS or TCP.
            using (var closedBoard = new CancellationTokenSource())
            {
                closedBoard.Cancel();
                var skipped = StateProbe.CaptureServicesAfterWslAsync(
                    offline, closedBoard.Token).GetAwaiter().GetResult();
                Assert(ReferenceEquals(skipped, offline),
                    "C4.2 no network probes when Ubuntu is off");

                bool canceled = false;
                try
                {
                    _ = StateProbe.CaptureServicesAfterWslAsync(
                        reachable, closedBoard.Token).GetAwaiter().GetResult();
                }
                catch (OperationCanceledException)
                {
                    canceled = true;
                }
                Assert(canceled, "C4.2 in-flight services canceled on board close");
            }

            var unknown = new StateProbe.Snapshot(
                null, "Błąd odczytu WSL", null, null, null, null, DateTimeOffset.Now);
            using (var card = JsonDocument.Parse(WidgetCards.DevStackReadOnlyTemplate(unknown)))
            {
                var text = ReadCardText(card.RootElement);
                Assert(text.Contains("nie sprawdzono usług", StringComparison.Ordinal), "C4.2 unknown WSL");
                Assert(!text.Contains("Nginx:", StringComparison.Ordinal), "C4.2 unknown no services");
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

    // Test decoded Adaptive Card text rather than escaped JSON source:
    // System.Text.Json escapes Polish Unicode characters by default.
    private static string ReadCardText(JsonElement card) =>
        string.Join("\n", card.GetProperty("body").EnumerateArray()
            .Select(item => item.GetProperty("text").GetString() ?? ""));

    private static string[] CardActionVerbs(JsonElement card) =>
        card.GetProperty("actions").EnumerateArray()
            .Select(action => action.GetProperty("verb").GetString() ?? "")
            .ToArray();

    private static void Assert(bool value, string message)
    {
        if (!value) throw new InvalidOperationException("Self-test failed: " + message);
    }
}
