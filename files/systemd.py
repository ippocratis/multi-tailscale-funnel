import yaml
import os
import subprocess
from pathlib import Path

# Read configuration
with open("services.yml") as f:
    config = yaml.safe_load(f)

# Get original user
USER = os.getenv("SUDO_USER") or os.getenv("USER")

# Validate .env
env_path = Path(".env").absolute()
if not env_path.exists():
    raise SystemExit("❌ .env file not found at current directory")

# Parse .env
env_vars = {}
with open(env_path) as f:
    for line in f:
        if "=" in line and not line.strip().startswith("#"):
            key, val = line.split("=", 1)
            env_vars[key.strip()] = val.strip()

if "TS_AUTHKEY" not in env_vars:
    raise SystemExit("❌ TS_AUTHKEY missing in .env")

SYSTEMD_DIR = Path("/etc/systemd/system")

def run_cmd(cmd, cwd=None):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {result.stderr}")
    return result

for name, info in config["services"].items():
    service_dir = Path(name).absolute()
    hostname = info["hostname"]
    port = info["port"]
    binary_path = service_dir / "app"
    systemd_unit_path = SYSTEMD_DIR / f"{name}-funnel.service"

    print(f"\n🚀 Processing {name}")

    (service_dir / "state").mkdir(parents=True, exist_ok=True)

    try:
        run_cmd(["chown", "-R", f"{USER}:{USER}", str(service_dir)])
    except Exception as e:
        print(f"⚠️ Permission fix error: {e}")

    main_go = service_dir / "main.go"

    if not binary_path.exists():
        print("📝 Writing Go source and building binary...")
        main_go.write_text(f'''package main

import (
    "log"
    "net/http"
    "net/http/httputil"
    "os"
    "tailscale.com/tsnet"
)

func main() {{
    srv := &tsnet.Server{{
        Hostname: "{hostname}",
        AuthKey:  os.Getenv("TS_AUTHKEY"),
        Dir:      "./state",
    }}
    defer srv.Close()

    ln, err := srv.ListenFunnel("tcp", ":443")
    if err != nil {{
        log.Fatal(err)
    }}

    proxy := &httputil.ReverseProxy{{
        Director: func(r *http.Request) {{
            r.URL.Host = "localhost:{port}"
            r.URL.Scheme = "http"
        }},
    }}

    log.Println("Starting reverse proxy for {name}...")
    log.Fatal(http.Serve(ln, proxy))
}}
''')

        try:
            print("🔨 Building binary...")
            run_cmd(["go", "mod", "init", f"tsnet/{name}"], cwd=service_dir)
            run_cmd(["go", "mod", "tidy"], cwd=service_dir)
            run_cmd(["go", "get", "tailscale.com/tsnet"], cwd=service_dir)
            run_cmd(["go", "build", "-o", "app"], cwd=service_dir)
            print("✅ Build successful")
        except Exception as e:
            print(f"❌ Build failed: {str(e)}")
            continue
    else:
        print("✅ Binary already exists, skipping build")

    if not systemd_unit_path.exists():
        print("🛠️ Creating and installing systemd unit...")
        service_file = service_dir / f"{name}-funnel.service"
        service_content = f"""
[Unit]
Description=Tailscale Funnel Proxy for {hostname}
After=network.target

[Service]
EnvironmentFile={env_path}
WorkingDirectory={service_dir}
ExecStart={service_dir}/app
Restart=always
User={USER}
Group={USER}

[Install]
WantedBy=multi-user.target
"""
        service_file.write_text(service_content.strip())

        try:
            run_cmd(["mv", str(service_file), str(systemd_unit_path)])
            run_cmd(["systemctl", "daemon-reload"])
            run_cmd(["systemctl", "enable", f"{name}-funnel.service"])
            run_cmd(["systemctl", "start", f"{name}-funnel.service"])
            print(f"✅ {name} systemd service installed and started")
        except Exception as e:
            print(f"❌ Failed to install/start service: {str(e)}")
    else:
        print("✅ Systemd service already exists, skipping installation")

print("\n🎉 All services checked and deployed!")
