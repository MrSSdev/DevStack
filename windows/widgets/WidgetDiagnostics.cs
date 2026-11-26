using System.Security.Cryptography;
using System.Text;

namespace DevStack.Widgets;

/// <summary>Bounded local diagnostics. Never logs message bodies, URLs, credentials or command output.</summary>
internal static class WidgetDiagnostics
{
    private static readonly object Gate = new();
    internal static string DirectoryPath => WidgetPaths.DirectoryPath;
    internal static void Write(string code, string? id = null, Exception? error = null)
    {
        if (code.Length > 64 || code.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '.' or '-'))) return;
        var tag = id is null ? "-" : Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(id)))[..8];
        var status = error is null ? "" : " hr=0x" + error.HResult.ToString("X8") + " type=" + error.GetType().Name;
        lock (Gate)
        {
            try
            {
                if (!WidgetPaths.Available) return;
                var path = Path.Combine(DirectoryPath, "wsl-bridge.log");
                if (File.Exists(path) && new FileInfo(path).Length > 131072) File.Move(path, path + ".previous", true);
                File.AppendAllText(path, DateTimeOffset.Now.ToString("O") + " " + code + " widget=" + tag + status + "\n");
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { /* diagnostics must not break the widget */ }
        }
    }
}
