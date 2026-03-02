#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io,
    net::TcpStream,
    sync::Mutex,
    thread,
    time::{Duration, Instant},
};

use tauri::{
    api::process::{Command, CommandChild, CommandEvent},
    Manager, RunEvent,
};

struct BackendState {
    child: Mutex<Option<CommandChild>>,
}

fn ui_host() -> String {
    let host = std::env::var("CHASINGCLAW_UI_HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let trimmed = host.trim();
    if trimmed.is_empty() {
        "127.0.0.1".to_string()
    } else {
        trimmed.to_string()
    }
}

fn ui_port() -> u16 {
    match std::env::var("CHASINGCLAW_UI_PORT") {
        Ok(raw) => raw
            .trim()
            .parse::<u16>()
            .ok()
            .filter(|port| *port > 0)
            .unwrap_or(18789),
        Err(_) => 18789,
    }
}

fn wait_for_port(host: &str, port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect((host, port)).is_ok() {
            return true;
        }
        thread::sleep(Duration::from_millis(250));
    }
    false
}

fn spawn_sidecar(
    app: &tauri::AppHandle,
    host: &str,
    port: u16,
) -> Result<CommandChild, Box<dyn std::error::Error>> {
    let mut command = Command::new_sidecar("chasingclaw-ui").map_err(|err| {
        io::Error::new(
            io::ErrorKind::Other,
            format!("failed to resolve sidecar: {err}"),
        )
    })?;

    command.args([
        "--host".to_string(),
        host.to_string(),
        "--port".to_string(),
        port.to_string(),
    ]);

    let (mut rx, child) = command
        .spawn()
        .map_err(|err| {
            io::Error::new(
                io::ErrorKind::Other,
                format!("failed to start sidecar: {err}"),
            )
        })?;

    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) => {
                    println!("[chasingclaw-ui] {line}");
                }
                CommandEvent::Stderr(line) => {
                    eprintln!("[chasingclaw-ui:stderr] {line}");
                }
                CommandEvent::Error(err) => {
                    eprintln!("[chasingclaw-ui:error] {err}");
                }
                CommandEvent::Terminated(_) => {
                    if let Some(window) = app_handle.get_window("main") {
                        let _ = window.eval(
                            "window.dispatchEvent(new CustomEvent('backend-exited'));",
                        );
                    }
                    break;
                }
                _ => {}
            }
        }
    });

    Ok(child)
}

fn main() {
    tauri::Builder::default()
        .manage(BackendState {
            child: Mutex::new(None),
        })
        .setup(|app| {
            let host = ui_host();
            let port = ui_port();

            if !wait_for_port(&host, port, Duration::from_millis(250)) {
                let child = spawn_sidecar(&app.handle(), &host, port)?;
                if let Ok(mut guard) = app.state::<BackendState>().child.lock() {
                    *guard = Some(child);
                }
            }

            let app_handle = app.handle();
            thread::spawn(move || {
                if wait_for_port(&host, port, Duration::from_secs(45)) {
                    if let Some(window) = app_handle.get_window("main") {
                        let url = format!("http://{host}:{port}/");
                        let script = format!("window.location.replace({url:?});");
                        let _ = window.eval(&script);
                    }
                } else if let Some(window) = app_handle.get_window("main") {
                    let _ = window.eval(
                        "window.dispatchEvent(new CustomEvent('backend-start-failed'));",
                    );
                }
            });

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                let state = app_handle.state::<BackendState>();
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(mut child) = guard.take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
