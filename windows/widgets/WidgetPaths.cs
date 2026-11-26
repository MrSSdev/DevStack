namespace DevStack.Widgets;

internal static class WidgetPaths
{
    internal const string InstalledDirectory = @"C:\DevStack\Widgets";
#if DEVSTACK_WIDGET_TEST
    // Only the test assembly can redirect its disposable fixture path.
    internal static string DirectoryPath { get; set; } = InstalledDirectory;
#else
    internal static string DirectoryPath => InstalledDirectory;
#endif
    internal static bool Available
    {
        get
        {
            try
            {
                var parent = Path.GetDirectoryName(DirectoryPath);
                return parent is not null && Directory.Exists(parent) && Directory.Exists(DirectoryPath)
                    && (File.GetAttributes(parent) & FileAttributes.ReparsePoint) == 0
                    && (File.GetAttributes(DirectoryPath) & FileAttributes.ReparsePoint) == 0;
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return false; }
        }
    }
}
