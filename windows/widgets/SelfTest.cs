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

            var offline = new StateProbe.Snapshot(false, "", null, null, null, null, DateTimeOffset.Now);
            using var status = JsonDocument.Parse(WidgetCards.Data(WidgetCards.ServicesId, offline, "", false));
            Assert(!status.RootElement.GetProperty("running").GetBoolean(), "DevStack offline state");
            Assert(status.RootElement.GetProperty("nginx").GetString() == "—", "no false status while WSL off");

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
