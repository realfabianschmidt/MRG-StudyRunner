use serde::Serialize;
use std::sync::Mutex;
use std::time::Duration;
use tauri::{ipc::Channel, Emitter, Manager, WindowEvent};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tauri_plugin_updater::{Update, UpdaterExt};

const SERVER_PORT: u16 = 3000;

struct ServerProcess {
    child: Mutex<Option<CommandChild>>,
}

struct PendingUpdate(Mutex<Option<Update>>);

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct LauncherInfo {
    admin_url: String,
    study_url: String,
    participant_url: String,
    data_dir: String,
    port: u16,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct UpdateCheckResult {
    current_version: String,
    version: Option<String>,
}

#[derive(Clone, Serialize)]
#[serde(tag = "event", content = "data")]
enum DownloadEvent {
    #[serde(rename_all = "camelCase")]
    Started {
        content_length: Option<u64>,
    },
    #[serde(rename_all = "camelCase")]
    Progress {
        chunk_length: usize,
    },
    Finished,
}

#[tauri::command]
fn launcher_info(app: tauri::AppHandle) -> Result<LauncherInfo, String> {
    let data_dir = app
        .path()
        .app_data_dir()
        .map_err(|error| error.to_string())?;

    Ok(LauncherInfo {
        admin_url: admin_url(),
        study_url: study_url(),
        participant_url: participant_url(),
        data_dir: data_dir.to_string_lossy().to_string(),
        port: SERVER_PORT,
    })
}

#[tauri::command]
async fn fetch_update(
    app: tauri::AppHandle,
    pending_update: tauri::State<'_, PendingUpdate>,
) -> Result<UpdateCheckResult, String> {
    let current_version = app.package_info().version.to_string();
    let update = app
        .updater_builder()
        .timeout(Duration::from_secs(8))
        .build()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?;

    let version = update.as_ref().map(|update| update.version.clone());
    let mut pending = pending_update
        .0
        .lock()
        .map_err(|_| "Could not lock pending update state".to_string())?;
    *pending = update;

    Ok(UpdateCheckResult {
        current_version,
        version,
    })
}

#[tauri::command]
async fn install_update(
    app: tauri::AppHandle,
    pending_update: tauri::State<'_, PendingUpdate>,
    on_event: Channel<DownloadEvent>,
) -> Result<(), String> {
    let update = {
        let mut pending = pending_update
            .0
            .lock()
            .map_err(|_| "Could not lock pending update state".to_string())?;
        pending
            .take()
            .ok_or_else(|| "There is no pending update to install".to_string())?
    };

    stop_server_sidecar(&app);

    let mut started = false;
    update
        .download_and_install(
            |chunk_length, content_length| {
                if !started {
                    let _ = on_event.send(DownloadEvent::Started { content_length });
                    started = true;
                }
                let _ = on_event.send(DownloadEvent::Progress { chunk_length });
            },
            || {
                let _ = on_event.send(DownloadEvent::Finished);
            },
        )
        .await
        .map_err(|error| error.to_string())?;

    #[cfg(target_os = "windows")]
    {
        Ok(())
    }

    #[cfg(not(target_os = "windows"))]
    {
        app.restart()
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .manage(PendingUpdate(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            launcher_info,
            fetch_update,
            install_update
        ])
        .setup(|app| {
            start_server_sidecar(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, WindowEvent::CloseRequested { .. }) {
                stop_server_sidecar(window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running Study Runner desktop launcher");
}

fn start_server_sidecar(app: &tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let data_dir = app.path().app_data_dir()?;
    std::fs::create_dir_all(&data_dir)?;

    let data_dir_string = data_dir.to_string_lossy().to_string();
    let (mut receiver, child) = app
        .shell()
        .sidecar("study-runner-server")?
        .env("STUDY_RUNNER_APP_MODE", "desktop")
        .env("STUDY_RUNNER_DATA_DIR", data_dir_string)
        .env("STUDY_RUNNER_HOST", "0.0.0.0")
        .env("STUDY_RUNNER_PORT", SERVER_PORT.to_string())
        .env("STUDY_RUNNER_DISABLE_RUNTIME_PIP", "1")
        .spawn()?;

    app.manage(ServerProcess {
        child: Mutex::new(Some(child)),
    });

    let output_app = app.handle().clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = receiver.recv().await {
            match event {
                CommandEvent::Stdout(bytes) | CommandEvent::Stderr(bytes) => {
                    let line = String::from_utf8_lossy(&bytes).to_string();
                    let _ = output_app.emit("server-output", line);
                }
                _ => {}
            }
        }
    });

    let ready_app = app.handle().clone();
    std::thread::spawn(move || {
        wait_for_server();
        let _ = ready_app.emit("server-ready", admin_url());
    });

    Ok(())
}

fn stop_server_sidecar(app: &tauri::AppHandle) {
    let child = {
        let state = app.state::<ServerProcess>();
        let child = match state.child.lock() {
            Ok(mut guard) => guard.take(),
            Err(_) => None,
        };
        child
    };

    if let Some(child) = child {
        let _ = child.kill();
    }
}

fn wait_for_server() {
    for _ in 0..80 {
        if std::net::TcpStream::connect(("127.0.0.1", SERVER_PORT)).is_ok() {
            return;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
}

fn admin_url() -> String {
    format!("http://localhost:{SERVER_PORT}/admin")
}

fn study_url() -> String {
    format!("http://localhost:{SERVER_PORT}")
}

fn participant_url() -> String {
    if let Ok(socket) = std::net::UdpSocket::bind("0.0.0.0:0") {
        if socket.connect("8.8.8.8:80").is_ok() {
            if let Ok(address) = socket.local_addr() {
                return format!("http://{}:{SERVER_PORT}", address.ip());
            }
        }
    }
    study_url()
}
