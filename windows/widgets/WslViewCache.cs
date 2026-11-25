using System.Text.Json;
namespace DevStack.Widgets;

/// <summary>Display data only. Never restores authentication, commands or Stop confirmation.</summary>
internal static class WslViewCache
{
    internal sealed record Entry(int Schema, bool? Running, DateTimeOffset CheckedAt, WslResources.Result Resources);
    private static readonly object Gate = new();
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    internal static Entry? Load()
    {
        lock (Gate)
        {
            try
            {
                var path = Path.Combine(WidgetDiagnostics.DirectoryPath, "wsl-view-cache.json");
                if (!File.Exists(path) || new FileInfo(path).Length > 8192) return null;
                var data = JsonSerializer.Deserialize<Entry>(File.ReadAllText(path), Json);
                var now = DateTimeOffset.Now;
                if (data is null || data.Schema != 1 || data.CheckedAt < now.AddDays(-1) || data.CheckedAt > now.AddMinutes(1)) return null;
                if (data.Resources is null || data.Resources.Metrics is not null && !WslResources.Valid(data.Resources.Metrics)) return null;
                return data;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException) { return null; }
        }
    }
    internal static void Save(StateProbe.Snapshot? state, WslResources.Result resources)
    {
        if (state is null || state.WslRunning is null) return;
        lock (Gate)
        {
            try
            {
                Directory.CreateDirectory(WidgetDiagnostics.DirectoryPath);
                var path = Path.Combine(WidgetDiagnostics.DirectoryPath, "wsl-view-cache.json");
                File.WriteAllText(path + ".tmp", JsonSerializer.Serialize(new Entry(1, state.WslRunning, state.CheckedAt,
                    state.WslRunning == true ? resources : WslResources.Empty("offline")), Json));
                File.Move(path + ".tmp", path, true);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* display cache is optional */ }
        }
    }
}
