// Copyright (C) Microsoft Corporation.
// Licensed under the MIT License.

using Microsoft.Windows.Widgets.Providers;
using System;
using System.Runtime.InteropServices;
using WinRT;

namespace WidgetHelper
{
    namespace Com
    {
        static class Guids
        {
            public const string IClassFactory = "00000001-0000-0000-C000-000000000046";
        }

        [ComImport(), InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid(Guids.IClassFactory)]
        internal interface IClassFactory
        {
            [PreserveSig]
            int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject);
            [PreserveSig]
            int LockServer(bool fLock);
        }

        static class ClassObject
        {
            public static void Register(Guid clsid, object pUnk, out uint cookie)
            {
                [DllImport("ole32.dll")]
                static extern int CoRegisterClassObject(
                    [MarshalAs(UnmanagedType.LPStruct)] Guid rclsid,
                    [MarshalAs(UnmanagedType.IUnknown)] object pUnk,
                    uint dwClsContext,
                    uint flags,
                    out uint lpdwRegister);

                int result = CoRegisterClassObject(clsid, pUnk, 0x4, 0x1, out cookie);
                if (result != 0)
                {
                    Marshal.ThrowExceptionForHR(result);
                }
            }

            public static int Revoke(uint cookie)
            {
                [DllImport("ole32.dll")]
                static extern int CoRevokeClassObject(uint dwRegister);

                return CoRevokeClassObject(cookie);
            }
        }
    }

    [ComVisible(true)]
    internal class WidgetProviderFactory<T> : Com.IClassFactory
            where T : IWidgetProvider, new()
    {
        public int CreateInstance(IntPtr pUnkOuter, ref Guid riid, out IntPtr ppvObject)
        {
            ppvObject = IntPtr.Zero;

            if (pUnkOuter != IntPtr.Zero)
            {
                return CLASS_E_NOAGGREGATION;
            }

            IntPtr provider = IntPtr.Zero;
            try
            {
                // The host requests an interface IID, not necessarily our class GUID.
                // Return the requested COM interface using the official Microsoft pattern.
                provider = MarshalInspectable<IWidgetProvider>.FromManaged(new T());
                return Marshal.QueryInterface(provider, in riid, out ppvObject);
            }
            catch (Exception error)
            {
                System.Diagnostics.Trace.TraceError("Widget COM activation: " + error);
                return Marshal.GetHRForException(error);
            }
            finally
            {
                if (provider != IntPtr.Zero)
                {
                    Marshal.Release(provider);
                }
            }
        }

        int Com.IClassFactory.LockServer(bool fLock)
        {
            return 0;
        }

        private const int CLASS_E_NOAGGREGATION = -2147221232;
    }
}
