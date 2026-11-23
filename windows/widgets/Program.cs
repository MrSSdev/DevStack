// Based on Microsoft's Windows App SDK WidgetProvider sample (MIT license).
// A single, invisible COM server hosts two separate widgets in Win+W.
using DevStack.Widgets;
using WidgetHelper;

if (args.Length == 1 && args[0] == "--self-test")
{
    Environment.ExitCode = SelfTest.Run();
}
else if (args.Length == 1 && args[0] == "-RegisterProcessAsComServer")
{
    WinRT.ComWrappersSupport.InitializeComWrappers();

    using var registration = RegistrationManager<WidgetProvider>.RegisterProvider();
    WidgetProvider.Start();
    try
    {
        // Exit after being inactive to avoid a permanent background process.
        // COM activation by the Widgets Board starts a new instance when needed.
        while (!WidgetProvider.ShouldExit())
        {
            WidgetProvider.Wake.WaitOne(TimeSpan.FromSeconds(15));
        }
    }
    finally
    {
        WidgetProvider.Stop();
    }
}
