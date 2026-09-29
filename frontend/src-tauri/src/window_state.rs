//! Where the Sage window opens. The first time: 1419 x 866 inside (1435 x 905
//! with its frame), centred on the screen -- the size the user picked on
//! 29 September. After that: wherever and however large it was last left,
//! maximised included, kept in `window-state.json` in the app's config folder.

use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
    thread,
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::{
    AppHandle, LogicalSize, Manager, PhysicalPosition, PhysicalSize, Runtime, WebviewWindow,
};

pub const FIRST_WIDTH: f64 = 1419.0;
pub const FIRST_HEIGHT: f64 = 866.0;

/// The window's normal (not maximised) bounds in physical pixels.
#[derive(Clone, Copy, Debug, Serialize, Deserialize)]
struct WindowState {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    maximized: bool,
}

fn state_path<R: Runtime>(app: &AppHandle<R>) -> Option<PathBuf> {
    app.path().app_config_dir().ok().map(|dir| dir.join("window-state.json"))
}

fn load<R: Runtime>(app: &AppHandle<R>) -> Option<WindowState> {
    let text = fs::read_to_string(state_path(app)?).ok()?;
    serde_json::from_str(&text).ok()
}

/// True when enough of the rectangle (a 100 x 50 corner of the title bar) is on
/// a screen that is connected now -- a window saved on an unplugged monitor
/// would otherwise open where nobody can see it.
fn on_a_screen<R: Runtime>(window: &WebviewWindow<R>, s: &WindowState) -> bool {
    window.available_monitors().unwrap_or_default().iter().any(|m| {
        let (p, z) = (m.position(), m.size());
        s.x + 100 > p.x
            && s.x < p.x + z.width as i32 - 100
            && s.y >= p.y - 10
            && s.y < p.y + z.height as i32 - 50
    })
}

/// Size and place the (still hidden) window before it is first shown.
pub fn restore<R: Runtime>(window: &WebviewWindow<R>) {
    match load(window.app_handle()) {
        Some(s) if s.width >= 400 && s.height >= 300 && on_a_screen(window, &s) => {
            let _ = window.set_size(PhysicalSize::new(s.width, s.height));
            let _ = window.set_position(PhysicalPosition::new(s.x, s.y));
            if s.maximized {
                let _ = window.maximize();
            }
        }
        _ => {
            let _ = window.set_size(LogicalSize::new(FIRST_WIDTH, FIRST_HEIGHT));
            let _ = window.center();
        }
    }
}

static GENERATION: AtomicU64 = AtomicU64::new(0);

/// Called on every move and resize: saves half a second after the last one,
/// so dragging the window does not write the file dozens of times a second.
pub fn changed<R: Runtime>(window: &WebviewWindow<R>) {
    let generation = GENERATION.fetch_add(1, Ordering::SeqCst) + 1;
    let window = window.clone();
    thread::spawn(move || {
        thread::sleep(Duration::from_millis(500));
        if GENERATION.load(Ordering::SeqCst) == generation {
            save(&window);
        }
    });
}

/// Record the current bounds. Minimised or hidden, nothing is recorded (the
/// position is then off-screen); maximised, only the flag changes, so
/// un-maximising after a restart returns to the last normal size.
pub fn save<R: Runtime>(window: &WebviewWindow<R>) {
    if window.is_minimized().unwrap_or(false) || !window.is_visible().unwrap_or(false) {
        return;
    }
    let maximized = window.is_maximized().unwrap_or(false);
    let app = window.app_handle();
    let state = if maximized {
        match load(app) {
            Some(previous) => WindowState { maximized: true, ..previous },
            None => return,
        }
    } else {
        let (Ok(pos), Ok(size)) = (window.outer_position(), window.inner_size()) else { return };
        WindowState { x: pos.x, y: pos.y, width: size.width, height: size.height, maximized: false }
    };
    let Some(path) = state_path(app) else { return };
    if let Some(dir) = path.parent() {
        let _ = fs::create_dir_all(dir);
    }
    if let Ok(text) = serde_json::to_string_pretty(&state) {
        let _ = fs::write(path, text);
    }
}
