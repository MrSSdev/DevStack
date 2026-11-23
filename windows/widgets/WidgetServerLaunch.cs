namespace DevStack.Widgets;

/// <summary>
/// Recognize the COM local-server launch command line. Windows can append
/// -Embedding to arguments declared in the MSIX com:ExeServer element.
/// </summary>
internal static class WidgetServerLaunch
{
    internal static bool IsComActivation(IEnumerable<string> args) =>
        args.Any(argument =>
            argument.Equals("-RegisterProcessAsComServer", StringComparison.OrdinalIgnoreCase) ||
            argument.Equals("-Embedding", StringComparison.OrdinalIgnoreCase) ||
            argument.Equals("/Embedding", StringComparison.OrdinalIgnoreCase));
}
