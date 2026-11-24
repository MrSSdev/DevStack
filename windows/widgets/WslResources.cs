using System.Net.Http.Headers;
using System.Text.Json;

namespace DevStack.Widgets;

/// <summary>Read-only localhost requests, never a WSL launch or UNC filesystem access.</summary>
internal static class WslResources
{
    internal const int Port = 43871;
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web);
    private static readonly HttpClient Client = new(new HttpClientHandler
    {
        AllowAutoRedirect = false, UseCookies = false, UseProxy = false
    }) { Timeout = TimeSpan.FromSeconds(2), MaxResponseContentBufferSize = 8192 };

    internal sealed record Reading(int Schema, string Distro, string Source, string CpuScope,
        double? CpuPercent, long? MemoryUsedBytes, long? MemoryTotalBytes,
        long? DiskUsedBytes, long? DiskTotalBytes, double? UptimeSeconds);
    internal sealed record Result(string Status, Reading? Metrics, DateTimeOffset? CheckedAt);
    private sealed record Settings(int Schema, string Distro, int Port, string Token);
    internal static Result Empty(string status) => new(status, null, null);

    internal static bool Valid(Reading r) => r.Schema == 1 && r.Distro == StateProbe.Distro &&
        r.Source == "linux-procfs" && r.CpuScope == "shared-wsl-kernel" &&
        Number(r.CpuPercent, 100) && Number(r.UptimeSeconds, 3155760000) &&
        Bytes(r.MemoryUsedBytes, r.MemoryTotalBytes) && Bytes(r.DiskUsedBytes, r.DiskTotalBytes);
    private static bool Number(double? n, double maximum) => n is null ||
        (double.IsFinite(n.Value) && n >= 0 && n <= maximum);
    private static bool Bytes(long? used, long? total) =>
        (used is null && total is null) || (used >= 0 && total > 0 && used <= total);

    internal static async Task<Result> CaptureAsync(bool? running, CancellationToken cancellationToken)
    {
        if (running != true) return Empty(running == false ? "offline" : "unavailable");
        cancellationToken.ThrowIfCancellationRequested();
        var path = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "DevStack", "Widgets", "telemetry.json");
        if (!File.Exists(path)) return Empty("not-installed");
        try
        {
            var file = new FileInfo(path);
            if (file.Length > 2048) return Empty("error");
            var settings = JsonSerializer.Deserialize<Settings>(await File.ReadAllTextAsync(path, cancellationToken), Json);
            if (settings is null || settings.Schema != 1 || settings.Distro != StateProbe.Distro ||
                settings.Port != Port || settings.Token is null || settings.Token.Length != 64 ||
                settings.Token.Any(c => !"0123456789abcdef".Contains(c))) return Empty("error");
            using var request = new HttpRequestMessage(HttpMethod.Get, $"http://127.0.0.1:{Port}/v1/metrics");
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", settings.Token);
            using var response = await Client.SendAsync(request, HttpCompletionOption.ResponseContentRead, cancellationToken);
            if (!response.IsSuccessStatusCode) return Empty("error");
            if (response.Content.Headers.ContentType?.MediaType != "application/json") return Empty("error");
            var data = await response.Content.ReadAsStringAsync(cancellationToken);
            var reading = JsonSerializer.Deserialize<Reading>(data, Json);
            if (reading is null || !Valid(reading)) return Empty("error");
            return new("ok", reading, DateTimeOffset.Now);
        }
        catch (OperationCanceledException) when (cancellationToken.IsCancellationRequested) { throw; }
        catch (Exception e) when (e is HttpRequestException or IOException or UnauthorizedAccessException or JsonException or OperationCanceledException)
        {
            // Do not put bearer credentials, network response bodies or paths into UI/logs.
            return Empty("error");
        }
    }
}
