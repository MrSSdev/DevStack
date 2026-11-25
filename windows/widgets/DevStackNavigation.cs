using System.Diagnostics;
namespace DevStack.Widgets;
internal static class DevStackNavigation
{
    internal static void Open(string verb)
    {
        string url=verb switch {"open-new"=>"https://devstack.test/#/new","open-projects"=>"https://devstack.test/#/projects",_=>throw new InvalidOperationException("Unknown route")};
        Process.Start(new ProcessStartInfo { FileName=url, UseShellExecute=true });
    }
}
