//! The window station the Windows sandbox starts its commands in (WINDOWS_ARM §23.4 W2).
//!
//! At a console a process lives in `WinSta0`, whose ACL lets the logon session's SID in; the
//! sandbox's processes carry that SID (the restricted token among its restricting SIDs, the
//! elevated runner because `CreateProcessWithLogonW` shares the caller's logon SID), so they can
//! open it. Over SSH a process lives in the logon's service window station
//! (`Service-0x0-…$`, not visible), whose ACL names only the user and the administrators: there
//! every sandboxed process that loads `user32` (PowerShell, .NET) died in initialisation
//! (`STATUS_DLL_INIT_FAILED`), and the elevated runner never connected its pipe. Codex also named
//! its private desktop `Winsta0\<desktop>`, which does not exist in such a session; patch `0033`
//! names it in the caller's real window station (a desktop named without its station would put a
//! sandbox account's child in its own logon's station, where the private desktop is not).
//!
//! What the launcher adds, before Codex starts and only in a window station that is not
//! visible: the logon SID's access to the window station and to its `Default` desktop (where the
//! elevated runner starts before it opens the private one), as `WinSta0` and its `Default` give it
//! at a console. When that cannot be done, every start says why commands will not run, instead of
//! 15-second timeouts and `0xC0000142`.

/// `WSF_VISIBLE` in a window station's `UOI_FLAGS`: it has a display (`WinSta0` at a console).
pub const WSF_VISIBLE: u32 = 0x1;

/// What the logon SID gets on the window station: everything a process needs there, as
/// `WinSta0` grants it, without the rights to change its ACL, owner, or delete it.
/// (`WINSTA_ENUMDESKTOPS` … `WINSTA_READSCREEN`, 0x37F, and `READ_CONTROL`.)
pub const WINSTA_PARTICIPANT_ACCESS: u32 = 0x037F | 0x0002_0000;

/// What the logon SID gets on the window station's `Default` desktop, likewise
/// (`DESKTOP_READOBJECTS` … `DESKTOP_SWITCHDESKTOP`, 0x1FF, and `READ_CONTROL`).
pub const DESKTOP_PARTICIPANT_ACCESS: u32 = 0x01FF | 0x0002_0000;

/// Whether a window station with these `UOI_FLAGS` is one a person sees.
pub fn is_visible(flags: u32) -> bool {
    flags & WSF_VISIBLE != 0
}

/// The line every start prints when the sandbox's commands cannot start in this session.
pub fn cannot_run_message(station: &str, reason: &str) -> String {
    format!(
        "The Windows sandbox cannot start commands in this session: it has no desktop (window station {station}, as over SSH), \
         and Mightling could not let the sandbox into it ({reason}). Commands the agent runs will fail; run ling in a desktop session."
    )
}

/// In a window station that is not visible, lets the logon session's sandboxed processes into it
/// and its `Default` desktop. Returns the line to show when that fails, or None (also at a
/// console, and on every other platform).
pub fn prepare_for_sandbox() -> Option<String> {
    #[cfg(windows)]
    {
        imp::prepare()
    }
    #[cfg(not(windows))]
    {
        None
    }
}

#[cfg(windows)]
mod imp {
    use std::ffi::c_void;
    use std::ptr;

    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::Foundation::HLOCAL;
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::ACL;
    use windows_sys::Win32::Security::Authorization::EXPLICIT_ACCESS_W;
    use windows_sys::Win32::Security::Authorization::GRANT_ACCESS;
    use windows_sys::Win32::Security::Authorization::GetSecurityInfo;
    use windows_sys::Win32::Security::Authorization::SE_WINDOW_OBJECT;
    use windows_sys::Win32::Security::Authorization::SetEntriesInAclW;
    use windows_sys::Win32::Security::Authorization::SetSecurityInfo;
    use windows_sys::Win32::Security::Authorization::TRUSTEE_IS_SID;
    use windows_sys::Win32::Security::Authorization::TRUSTEE_IS_UNKNOWN;
    use windows_sys::Win32::Security::Authorization::TRUSTEE_W;
    use windows_sys::Win32::Security::DACL_SECURITY_INFORMATION;
    use windows_sys::Win32::Security::PSECURITY_DESCRIPTOR;
    use windows_sys::Win32::System::StationsAndDesktops::CloseDesktop;
    use windows_sys::Win32::System::StationsAndDesktops::CloseWindowStation;
    use windows_sys::Win32::System::StationsAndDesktops::GetProcessWindowStation;
    use windows_sys::Win32::System::StationsAndDesktops::GetUserObjectInformationW;
    use windows_sys::Win32::System::StationsAndDesktops::OpenDesktopW;
    use windows_sys::Win32::System::StationsAndDesktops::OpenWindowStationW;
    use windows_sys::Win32::System::StationsAndDesktops::UOI_FLAGS;
    use windows_sys::Win32::System::StationsAndDesktops::UOI_NAME;
    use windows_sys::Win32::System::StationsAndDesktops::USEROBJECTFLAGS;

    /// `READ_CONTROL | WRITE_DAC`: enough to read and extend an object's ACL.
    const ACL_ACCESS: u32 = 0x0002_0000 | 0x0004_0000;
    /// `SE_GROUP_LOGON_ID`: the token group that is the logon session's SID.
    const SE_GROUP_LOGON_ID: u32 = 0xC000_0000;

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub(super) fn prepare() -> Option<String> {
        let station = unsafe { GetProcessWindowStation() };
        if station == 0 {
            return None;
        }
        let Some(flags) = (unsafe { station_flags(station) }) else { return None };
        if super::is_visible(flags) {
            return None;
        }
        let name = unsafe { object_name(station) }.unwrap_or_else(|| "?".to_string());
        match unsafe { let_logon_session_in(&name) } {
            Ok(()) => None,
            Err(reason) => Some(super::cannot_run_message(&name, &reason)),
        }
    }

    unsafe fn station_flags(handle: HANDLE) -> Option<u32> {
        let mut flags: USEROBJECTFLAGS = unsafe { std::mem::zeroed() };
        let mut needed = 0u32;
        let ok = unsafe {
            GetUserObjectInformationW(
                handle,
                UOI_FLAGS,
                &mut flags as *mut USEROBJECTFLAGS as *mut c_void,
                std::mem::size_of::<USEROBJECTFLAGS>() as u32,
                &mut needed,
            )
        };
        (ok != 0).then_some(flags.dwFlags)
    }

    unsafe fn object_name(handle: HANDLE) -> Option<String> {
        let mut buffer = [0u16; 256];
        let mut needed = 0u32;
        let ok = unsafe {
            GetUserObjectInformationW(handle, UOI_NAME, buffer.as_mut_ptr() as *mut c_void, (buffer.len() * 2) as u32, &mut needed)
        };
        if ok == 0 {
            return None;
        }
        let end = buffer.iter().position(|unit| *unit == 0).unwrap_or(buffer.len());
        Some(String::from_utf16_lossy(&buffer[..end]))
    }

    unsafe fn logon_sid() -> Result<Vec<u8>, String> {
        let token = unsafe { codex_windows_sandbox::get_current_token_for_restriction() }.map_err(|err| format!("the process token: {err}"))?;
        let groups = unsafe { codex_windows_sandbox::token_groups(token, u32::MAX) };
        unsafe { CloseHandle(token) };
        groups
            .map_err(|err| format!("the token's groups: {err}"))?
            .into_iter()
            .find(|group| group.attributes & SE_GROUP_LOGON_ID == SE_GROUP_LOGON_ID)
            .map(|group| group.sid)
            .ok_or_else(|| "the token has no logon SID".to_string())
    }

    unsafe fn let_logon_session_in(station_name: &str) -> Result<(), String> {
        let mut sid = unsafe { logon_sid() }?;
        let station = unsafe { OpenWindowStationW(wide(station_name).as_ptr(), 0, ACL_ACCESS) };
        if station == 0 {
            return Err(format!("OpenWindowStationW failed: {}", unsafe { GetLastError() }));
        }
        let granted = unsafe { grant(station, &mut sid, super::WINSTA_PARTICIPANT_ACCESS) };
        unsafe { CloseWindowStation(station) };
        granted.map_err(|err| format!("window station: {err}"))?;
        // OpenDesktopW opens a desktop of the calling process's window station: this one.
        let desktop = unsafe { OpenDesktopW(wide("Default").as_ptr(), 0, 0, ACL_ACCESS) };
        if desktop == 0 {
            return Err(format!("OpenDesktopW(Default) failed: {}", unsafe { GetLastError() }));
        }
        let granted = unsafe { grant(desktop, &mut sid, super::DESKTOP_PARTICIPANT_ACCESS) };
        unsafe { CloseDesktop(desktop) };
        granted.map_err(|err| format!("Default desktop: {err}"))
    }

    /// Adds an allow entry for `sid` to a window station's or desktop's ACL. `GRANT_ACCESS` merges
    /// with an entry the SID already has, so a second start adds nothing.
    unsafe fn grant(handle: HANDLE, sid: &mut [u8], access: u32) -> Result<(), String> {
        let mut dacl: *mut ACL = ptr::null_mut();
        let mut descriptor: PSECURITY_DESCRIPTOR = ptr::null_mut();
        let code = unsafe {
            GetSecurityInfo(
                handle,
                SE_WINDOW_OBJECT,
                DACL_SECURITY_INFORMATION,
                ptr::null_mut(),
                ptr::null_mut(),
                &mut dacl,
                ptr::null_mut(),
                &mut descriptor,
            )
        };
        if code != ERROR_SUCCESS {
            return Err(format!("GetSecurityInfo failed: {code}"));
        }
        let entry = EXPLICIT_ACCESS_W {
            grfAccessPermissions: access,
            grfAccessMode: GRANT_ACCESS,
            grfInheritance: 0,
            Trustee: TRUSTEE_W {
                pMultipleTrustee: ptr::null_mut(),
                MultipleTrusteeOperation: 0,
                TrusteeForm: TRUSTEE_IS_SID,
                TrusteeType: TRUSTEE_IS_UNKNOWN,
                ptstrName: sid.as_mut_ptr() as *mut u16,
            },
        };
        let mut updated: *mut ACL = ptr::null_mut();
        let code = unsafe { SetEntriesInAclW(1, &entry, dacl, &mut updated) };
        unsafe { LocalFree(descriptor as HLOCAL) };
        if code != ERROR_SUCCESS {
            return Err(format!("SetEntriesInAclW failed: {code}"));
        }
        let code = unsafe {
            SetSecurityInfo(handle, SE_WINDOW_OBJECT, DACL_SECURITY_INFORMATION, ptr::null_mut(), ptr::null_mut(), updated, ptr::null_mut())
        };
        unsafe { LocalFree(updated as HLOCAL) };
        if code != ERROR_SUCCESS {
            return Err(format!("SetSecurityInfo failed: {code}"));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_a_station_without_a_display_needs_the_grant() {
        assert!(is_visible(0x1));
        assert!(is_visible(0x1 | 0x8));
        assert!(!is_visible(0));
    }

    #[test]
    fn the_grant_never_includes_the_rights_to_change_the_acl_or_the_owner_or_to_delete() {
        const DELETE: u32 = 0x0001_0000;
        const WRITE_DAC: u32 = 0x0004_0000;
        const WRITE_OWNER: u32 = 0x0008_0000;
        for access in [WINSTA_PARTICIPANT_ACCESS, DESKTOP_PARTICIPANT_ACCESS] {
            assert_eq!(access & (DELETE | WRITE_DAC | WRITE_OWNER), 0, "{access:#x}");
        }
    }

    #[test]
    fn the_message_names_the_station_and_what_to_do() {
        let line = cannot_run_message("Service-0x0-1$", "OpenWindowStationW failed: 5");
        assert!(line.contains("Service-0x0-1$") && line.contains("failed: 5") && line.contains("desktop session"), "{line}");
    }

    /// On a Windows test machine, whatever its session, preparing never fails silently: either
    /// the station is visible, or the grant worked, or there is a line saying why not.
    #[cfg(windows)]
    #[test]
    fn preparing_answers_on_this_machine() {
        if let Some(line) = prepare_for_sandbox() {
            assert!(line.contains("desktop session"), "{line}");
        }
    }
}
