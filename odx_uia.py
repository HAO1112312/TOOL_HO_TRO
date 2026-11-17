"""Native accessibility default action for background Odx Ready.

UIA Invoke may activate a WPF window. LegacyIAccessible.DoDefaultAction keeps
its background state. The interop declarations follow UIAutomationClient.h;
Reserved methods intentionally retain unused COM vtable slots and are never
called. No COM packages or installed Python extensions are required.
"""

LEGACY_READY_SOURCE = r"""
using System;
using System.Runtime.InteropServices;
[ComImport,Guid("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ILegacyReadyAutomation { [PreserveSig] int Reserved0();
[PreserveSig] int Reserved1();
[PreserveSig] int Reserved2();
[PreserveSig] int ElementFromHandle(IntPtr h, out ILegacyReadyElement e);
[PreserveSig] int Reserved4();
[PreserveSig] int Reserved5();
[PreserveSig] int Reserved6();
[PreserveSig] int Reserved7();
[PreserveSig] int Reserved8();
[PreserveSig] int Reserved9();
[PreserveSig] int Reserved10();
[PreserveSig] int Reserved11();
[PreserveSig] int Reserved12();
[PreserveSig] int Reserved13();
[PreserveSig] int Reserved14();
[PreserveSig] int Reserved15();
[PreserveSig] int Reserved16();
[PreserveSig] int Reserved17();
[PreserveSig] int Reserved18();
[PreserveSig] int Reserved19();
[PreserveSig] int CreatePropertyCondition(int p,[MarshalAs(UnmanagedType.Struct)]object v,out IntPtr c); }
[ComImport,Guid("d22108aa-8ac5-49a5-837b-37bbb3d7591e"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ILegacyReadyElement { [PreserveSig] int Reserved0();
[PreserveSig] int GetRuntimeId([MarshalAs(UnmanagedType.SafeArray,SafeArraySubType=VarEnum.VT_I4)]out int[] id);
[PreserveSig] int FindFirst(int scope,IntPtr cond,out ILegacyReadyElement e);
[PreserveSig] int Reserved3();
[PreserveSig] int Reserved4();
[PreserveSig] int Reserved5();
[PreserveSig] int Reserved6();
[PreserveSig] int GetCurrentPropertyValue(int p,[MarshalAs(UnmanagedType.Struct)]out object v);
[PreserveSig] int Reserved8();
[PreserveSig] int Reserved9();
[PreserveSig] int Reserved10();
[PreserveSig] int Reserved11();
[PreserveSig] int Reserved12();
[PreserveSig] int GetCurrentPattern(int p,[MarshalAs(UnmanagedType.IUnknown)]out object v); }
[ComImport,Guid("828055ad-355b-4435-86d5-3b51c14a9b1b"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface ILegacyReadyPattern { [PreserveSig]int Select(int flags); [PreserveSig]int DoDefaultAction(); }
public static class OdxLegacyReady {
    static void Check(int h) { Marshal.ThrowExceptionForHR(h); }
    static object Property(ILegacyReadyElement e,int id) {object v; Check(e.GetCurrentPropertyValue(id,out v));return v;}
    static void Release(object o) {if(o!=null && Marshal.IsComObject(o)) Marshal.FinalReleaseComObject(o);}
    public static void Invoke(long hwnd,int pid,string id,string label,int[] runtimeId) {
        object clientObject=null,pattern=null;ILegacyReadyElement window=null,button=null;IntPtr condition=IntPtr.Zero;
        try {
            clientObject=Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("ff48dba4-60ef-4201-aa87-54103eef594e")));
            var client=(ILegacyReadyAutomation)clientObject;
            Check(client.ElementFromHandle(new IntPtr(hwnd),out window));
            if(Convert.ToInt32(Property(window,30002))!=pid || !String.Equals(Convert.ToString(Property(window,30005)),"Odx Capture",StringComparison.Ordinal))
                throw new InvalidOperationException("Managed Odx identity changed.");
            Check(client.CreatePropertyCondition(String.IsNullOrEmpty(id)?30005:30011,String.IsNullOrEmpty(id)?label:id,out condition));
            Check(window.FindFirst(4,condition,out button));
            if(button==null)throw new InvalidOperationException("Ready control is unavailable.");
            int[] actual;Check(button.GetRuntimeId(out actual));
            if(runtimeId==null || actual==null || runtimeId.Length!=actual.Length)throw new InvalidOperationException("Ready identity changed.");
            for(int i=0;i<actual.Length;i++)if(actual[i]!=runtimeId[i])throw new InvalidOperationException("Ready identity changed.");
            if(Convert.ToInt32(Property(button,30002))!=pid || Convert.ToInt32(Property(button,30003))!=50000 ||
                !String.Equals(Convert.ToString(Property(button,30005)),label,StringComparison.Ordinal) || !Convert.ToBoolean(Property(button,30010)))
                throw new InvalidOperationException("Ready state changed.");
            Check(button.GetCurrentPattern(10018,out pattern));
            if(pattern==null)throw new InvalidOperationException("Background Ready action is unavailable.");
            Check(((ILegacyReadyPattern)pattern).DoDefaultAction());
        } finally {
            Release(pattern);Release(button);Release(window);Release(clientObject);
            if(condition!=IntPtr.Zero) Marshal.Release(condition);
        }
    }
}

"""


def ready_action_preamble():
    """Compile the checked helper only in a worker which will set Ready."""
    return ("if (-not ('OdxLegacyReady' -as [type])) {\n"
            "Add-Type -TypeDefinition @'\n" + LEGACY_READY_SOURCE + "\n'@\n}\n")
