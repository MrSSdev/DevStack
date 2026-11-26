// Windows PowerShell 5.1 Add-Type helper. Rename the checked directory
// object by handle, never follow a leaf reparse point or replace a target.
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace DevStack.WindowsLayout
{
    public static class DirectoryIdentity
    {
        [StructLayout(LayoutKind.Sequential)]
        private struct FileInformation
        {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
            public uint VolumeSerialNumber;
            public uint FileSizeHigh;
            public uint FileSizeLow;
            public uint NumberOfLinks;
            public uint FileIndexHigh;
            public uint FileIndexLow;
        }

        [StructLayout(LayoutKind.Sequential)]
        private struct RenameInformation
        {
            public uint Flags;
            public IntPtr RootDirectory;
            public uint FileNameLength;
            public ushort FileName;
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(string path, uint access,
            uint share, IntPtr security, uint creation, uint flags, IntPtr template);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle handle,
            out FileInformation information);

        [DllImport("kernel32.dll", SetLastError = true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        private static extern bool SetFileInformationByHandle(SafeFileHandle handle,
            int informationClass, IntPtr information, uint size);

        private static SafeFileHandle Open(string path, uint access, uint share)
        {
            // OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT.
            var handle = CreateFileW(path, access, share, IntPtr.Zero, 3,
                0x02200000, IntPtr.Zero);
            if (handle.IsInvalid)
            {
                int error = Marshal.GetLastWin32Error();
                handle.Dispose();
                throw new Win32Exception(error, "Cannot open directory: " + path);
            }
            return handle;
        }

        private static string ReadIdentity(SafeFileHandle handle)
        {
            FileInformation info;
            if (!GetFileInformationByHandle(handle, out info))
                throw new Win32Exception(Marshal.GetLastWin32Error());
            if ((info.Attributes & 0x10) == 0 || (info.Attributes & 0x400) != 0)
                throw new IOException("Expected a real directory, not a reparse point.");
            return String.Format("{0:x8}:{1:x8}{2:x8}", info.VolumeSerialNumber,
                info.FileIndexHigh, info.FileIndexLow);
        }

        public static string Get(string path)
        {
            using (var handle = Open(path, 0x80, 7))
                return ReadIdentity(handle);
        }

        public static void Move(string source, string destination, string expected)
        {
            // DELETE | FILE_READ_ATTRIBUTES; do not share DELETE while
            // renaming. A competing incompatible handle causes a safe refusal.
            using (var handle = Open(source, 0x10080, 3))
            {
                if (!String.Equals(ReadIdentity(handle), expected, StringComparison.Ordinal))
                    throw new IOException("Directory identity changed before rename: " + source);
                byte[] name = Encoding.Unicode.GetBytes(destination);
                int offset = Marshal.OffsetOf(typeof(RenameInformation), "FileName").ToInt32();
                int lengthOffset = Marshal.OffsetOf(typeof(RenameInformation), "FileNameLength").ToInt32();
                int size = Math.Max(Marshal.SizeOf(typeof(RenameInformation)), offset + name.Length + 2);
                IntPtr buffer = Marshal.AllocHGlobal(size);
                try
                {
                    // ReplaceIfExists = FALSE; RootDirectory = NULL.
                    Marshal.Copy(new byte[size], 0, buffer, size);
                    Marshal.WriteInt32(buffer, lengthOffset, name.Length);
                    Marshal.Copy(name, 0, IntPtr.Add(buffer, offset), name.Length);
                    if (!SetFileInformationByHandle(handle, 3, buffer, (uint)size))
                        throw new Win32Exception(Marshal.GetLastWin32Error(),
                            "Cannot rename " + source + " to " + destination);
                }
                finally { Marshal.FreeHGlobal(buffer); }
            }
        }
    }
}
