import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
import paramiko
import threading
import os
import queue
import requests
import logging
import shlex
import webbrowser

# --- Custom Log Handler for Tkinter Widget ---
class TkinterLogHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__(); self.text_widget = text_widget
        self.formatter = logging.Formatter('[%(levelname)s] %(message)s'); self.setFormatter(self.formatter)
    def emit(self, record):
        msg = self.format(record)
        # Use a queue to make this thread-safe with the main UI loop
        self.text_widget.queue.put((msg, record.levelname.lower()))

class ScrolledTextWithQueue(scrolledtext.ScrolledText):
    """A ScrolledText widget that safely updates from a queue."""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.queue = queue.Queue()
        self.update_me()
    def update_me(self):
        try:
            while True:
                record, tag = self.queue.get(block=False)
                if tag:
                    self.insert(tk.END, record + '\n', (tag,))
                else:
                    self.insert(tk.END, record) # For raw output without newlines
                self.see(tk.END)
        except queue.Empty:
            pass
        self.after(100, self.update_me)

# --- ROBUST SSH and File Transfer Logic ---
def stream_command_and_log(host, port, user, password, command, log_widget):
    try:
        client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=int(port), username=user, password=password, timeout=20)
        log_command = command
        if 'sudo -S' in command: log_command = 'echo "********" | ' + command.split(' | ', 1)[1]
        log_widget.queue.put((f"--- [{host}] STARTING COMMAND: {log_command} ---", 'info'))
        
        # open_session and get_pty=True allows capturing stdout/stderr combined, useful for curl progress
        channel = client.get_transport().open_session(); channel.get_pty(); channel.exec_command(command)
        while not channel.exit_status_ready():
            if channel.recv_ready():
                chunk = channel.recv(1024)
                log_widget.queue.put((chunk.decode('utf-8', errors='ignore'), None))
        exit_status = channel.recv_exit_status(); client.close()
        log_widget.queue.put((f"\n--- [{host}] COMMAND FINISHED (Exit Status: {exit_status}) ---\n\n", 'info'))
        return exit_status == 0
    except Exception as e:
        log_widget.queue.put((f"--- [{host}] FAILED TO EXECUTE. Error: {e} ---\n\n", 'error'))
        return False

def get_remote_home_path(host, port, user, password, log_widget):
    try:
        client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=host, port=int(port), username=user, password=password, timeout=10)
        stdin, stdout, stderr = client.exec_command("echo $HOME")
        home_path = stdout.read().decode('utf-8').strip(); client.close()
        if not home_path: raise Exception("Home directory path was empty.")
        return home_path
    except Exception as e:
        log_widget.queue.put((f"[{host}] FAILED to determine home directory. Error: {e}\n", 'error'))
        return None

def upload_with_progress(host, port, user, password, local_path, remote_path, log_widget, status_queue):
    try:
        transport = paramiko.Transport((host, int(port))); transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        def progress_callback(bytes_so_far, total_bytes):
            if total_bytes > 0: status_queue.put(f"PROGRESS:{(bytes_so_far / total_bytes) * 100}")
        log_widget.queue.put((f"[{host}] Starting SFTP upload of {os.path.basename(local_path)} to '{remote_path}'...", 'info'))
        sftp.put(local_path, remote_path, callback=progress_callback)
        sftp.close(); transport.close()
        return True
    except Exception as e:
        log_widget.queue.put((f"[{host}] SFTP upload FAILED. Error: {e}", 'error'))
        return False

def download_file_sftp(host, port, user, password, remote_path, local_path, log_widget):
    try:
        transport = paramiko.Transport((host, int(port))); transport.connect(username=user, password=password)
        sftp = paramiko.SFTPClient.from_transport(transport)
        log_widget.queue.put((f"[{host}] Starting SFTP download of '{remote_path}'...", 'info'))
        sftp.get(remote_path, local_path)
        sftp.close(); transport.close()
        return True
    except Exception as e:
        log_widget.queue.put((f"[{host}] SFTP download FAILED. Error: {e}", 'error'))
        return False

# --- Main Application Class ---
class ImagingInstallerApp(tk.Tk):
    def __init__(self):
        super().__init__(); self.title("Imaging Console V3 Linux Installer"); self.geometry("950x950")
        self.analysis_nodes_widgets = []; self.temp_config_file = "temp_configuration.conf"
        self.status_queue = queue.Queue(); self.local_zip_filename = "imaging_package.zip"
        self.log_path_var = tk.StringVar(value=os.path.join(os.getcwd(), 'installer.log'))
        self.debug_mode_var = tk.BooleanVar(value=False); self.logger = logging.getLogger("AppLogger")
        self.os_family_var = tk.StringVar(value="Debian / Ubuntu")

        canvas = tk.Canvas(self); canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview); scrollbar.pack(side=tk.RIGHT, fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)
        self.scrollable_frame = ttk.Frame(canvas, padding="10")
        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self.create_setup_widgets(ttk.LabelFrame(self.scrollable_frame, text="1. Initial Setup", padding="10"))
        self.server_details_frame = ttk.LabelFrame(self.scrollable_frame, text="2. Server Details", padding="10")
        self.server_details_frame.pack(fill=tk.X, pady=5); self.create_server_details_widgets()
        self.create_action_widgets(ttk.LabelFrame(self.scrollable_frame, text="3. Actions", padding="10"))
        self.update_ui_for_mode()

    def _setup_logging(self):
        if self.logger.hasHandlers(): self.logger.handlers.clear()
        log_level = logging.DEBUG if self.debug_mode_var.get() else logging.INFO; self.logger.setLevel(log_level)
        try:
            file_handler = logging.FileHandler(self.log_path_var.get(), mode='a', encoding='utf-8')
            file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')); self.logger.addHandler(file_handler)
        except Exception as e: messagebox.showerror("Logging Error", f"Could not create log file.\n\nError: {e}"); return False
        self.logger.addHandler(TkinterLogHandler(self.status_log_widget))
        paramiko_logger = logging.getLogger("paramiko")
        if self.debug_mode_var.get():
            paramiko_logger.setLevel(logging.DEBUG)
            if not any(isinstance(h, logging.FileHandler) for h in paramiko_logger.handlers): paramiko_logger.addHandler(file_handler)
        else: paramiko_logger.setLevel(logging.WARNING)
        return True

    def create_setup_widgets(self, parent):
        parent.pack(fill=tk.X, pady=5); parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="CAST Extend API Key:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=2)
        self.api_key_entry = ttk.Entry(parent, width=40); self.api_key_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, columnspan=2)
        ttk.Label(parent, text="Version (e.g., latest or 3.1.0-funcrel):").grid(row=1, column=0, sticky=tk.W, padx=5, pady=2)
        self.version_entry = ttk.Entry(parent, width=40); self.version_entry.insert(0, "latest"); self.version_entry.grid(row=1, column=1, sticky=tk.EW, padx=5, columnspan=2)
        ttk.Label(parent, text="Linux OS Family:").grid(row=2, column=0, sticky=tk.W, padx=5, pady=2)
        os_options = ["Debian / Ubuntu", "CentOS / RHEL / Fedora"]
        os_dropdown = ttk.OptionMenu(parent, self.os_family_var, os_options[0], *os_options)
        os_dropdown.grid(row=2, column=1, sticky=tk.EW, padx=5, columnspan=2)
        ttk.Label(parent, text="Installation Mode:").grid(row=3, column=0, sticky=tk.W, padx=5, pady=2)
        self.mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(parent, text="Single Machine", variable=self.mode_var, value="single", command=self.update_ui_for_mode).grid(row=3, column=1, sticky=tk.W, padx=5)
        ttk.Radiobutton(parent, text="Distributed", variable=self.mode_var, value="distributed", command=self.update_ui_for_mode).grid(row=3, column=2, sticky=tk.W, padx=5)
        ttk.Label(parent, text="Log File Path:").grid(row=4, column=0, sticky=tk.W, padx=5, pady=5)
        log_path_entry = ttk.Entry(parent, textvariable=self.log_path_var); log_path_entry.grid(row=4, column=1, sticky=tk.EW, padx=5)
        ttk.Button(parent, text="Browse...", command=self.browse_log_path).grid(row=4, column=2, padx=5)
        self.debug_check = ttk.Checkbutton(parent, text="Enable Debug Logging (more details in file)", variable=self.debug_mode_var)
        self.debug_check.grid(row=5, column=1, columnspan=2, sticky=tk.W, padx=5)

    def browse_log_path(self):
        path = filedialog.asksaveasfilename(defaultextension=".log", filetypes=[("Log files", "*.log"), ("All files", "*.*")], initialfile="installer.log", title="Choose Log File Location")
        if path: self.log_path_var.set(path)
        
    def show_sudo_info(self):
        messagebox.showinfo("Sudo Password Information", "The sudo password is required to run installation commands with administrator privileges (e.g., installing Docker, running chmod).\n\nThis password is not stored and is only used to run the `sudo -S` command during the current session.")
    def show_docker_info(self):
        messagebox.showinfo("Docker Installation Information", "This step installs Docker Engine and Docker Compose using the official methods for the selected OS family.\n\nPrerequisites:\n- Docker Engine >= 20.10\n- Docker Compose (latest stable release, uses `docker compose`)\n- Access to https://hub.docker.com/\n- WSL (Windows Subsystem for Linux) is NOT supported.")
    def show_cleanup_info(self):
        messagebox.showinfo("Uninstall / Clean-up Information", "The processes described below do not remove any application schemas on additional remote database instances you have been using.\n\nIf you intend to perform a clean install on the same machine(s) you should ensure that these items are removed first.")

    def _create_ssh_widgets(self, parent, label_text):
        frame = ttk.LabelFrame(parent, text=label_text, padding="5"); frame.pack(fill=tk.X, expand=True, pady=5); frame.columnconfigure(1, weight=1)
        widgets = {}; labels = ["Host:", "Port:", "Username:", "Password:", "Sudo Password:"]
        for i, label in enumerate(labels):
            if "Sudo" in label:
                label_frame = ttk.Frame(frame); label_frame.grid(row=i, column=0, sticky=tk.W)
                ttk.Label(label_frame, text=label).pack(side=tk.LEFT)
                info_button = ttk.Button(label_frame, text=" i ", width=3, command=self.show_sudo_info)
                info_button.pack(side=tk.LEFT, padx=5)
            else: ttk.Label(frame, text=label).grid(row=i, column=0, sticky=tk.W, padx=5, pady=2)
            entry = ttk.Entry(frame, width=30)
            if "Password" in label: entry.config(show="*")
            if "Port" in label: entry.insert(0, "22")
            if "Username" in label: entry.insert(0, "root")
            entry.grid(row=i, column=1, sticky=tk.EW, padx=5); widgets[label.lower().replace(":", "").replace(" ", "_")] = entry
        ttk.Button(frame, text="Test Connection", command=lambda w=widgets: self.test_connection_thread(w)).grid(row=0, column=2, rowspan=2, sticky="ns", padx=10)
        return widgets

    def create_action_widgets(self, parent):
        parent.pack(fill=tk.BOTH, expand=True)
        docker_frame = ttk.Frame(parent); docker_frame.pack(fill=tk.X, pady=5)
        self.docker_install_btn = ttk.Button(docker_frame, text="Step 1: Install Docker Engine (Optional)", command=self.run_docker_install_thread); 
        self.docker_install_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(docker_frame, text=" i ", width=3, command=self.show_docker_info).pack(side=tk.LEFT, padx=5)
        self.download_btn = ttk.Button(parent, text="Step 2: Prepare Server(s) (Download via Curl on Linux)", command=self.run_download_thread); self.download_btn.pack(fill=tk.X, pady=5)
        status_frame = ttk.LabelFrame(parent, text="Task Status (Batch Screen)", padding="10"); status_frame.pack(fill=tk.X, pady=5)
        self.progress_bar = ttk.Progressbar(status_frame, orient='horizontal', mode='determinate'); self.progress_bar.pack(fill=tk.X, pady=(0, 5))
        self.status_log_widget = ScrolledTextWithQueue(status_frame, wrap=tk.WORD, height=4, bg="black", fg="#d3d3d3"); self.status_log_widget.pack(fill=tk.BOTH, expand=True)
        self.status_log_widget.tag_config('error', foreground='red'); self.status_log_widget.tag_config('ok', foreground='lightgreen'); self.status_log_widget.tag_config('info', foreground='cyan'); self.status_log_widget.tag_config('warning', foreground='orange')
        config_frame = ttk.LabelFrame(parent, text="Step 3: Configuration File Editor", padding="10"); config_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.fetch_config_btn = ttk.Button(config_frame, text="Fetch Config", command=self.fetch_config, state=tk.DISABLED); self.fetch_config_btn.pack(side=tk.LEFT, padx=5)
        self.upload_config_btn = ttk.Button(config_frame, text="Upload Config", command=self.upload_config, state=tk.DISABLED); self.upload_config_btn.pack(side=tk.LEFT, padx=5)
        self.config_text = scrolledtext.ScrolledText(config_frame, wrap=tk.WORD, height=6); self.config_text.pack(fill=tk.BOTH, expand=True, pady=5)
        self.install_btn = ttk.Button(parent, text="Step 4: Run CAST Installation", command=self.run_install_thread, state=tk.DISABLED); self.install_btn.pack(fill=tk.X, pady=5)
        log_frame = ttk.LabelFrame(parent, text="Detailed Command Output", padding="10"); log_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_widget = ScrolledTextWithQueue(log_frame, wrap=tk.WORD, height=10, bg="black", fg="#d3d3d3"); self.log_widget.pack(fill=tk.BOTH, expand=True)
        self.log_widget.tag_config('error', foreground='red'); self.log_widget.tag_config('success', foreground='lightgreen'); self.log_widget.tag_config('info', foreground='cyan')
        cleanup_frame = ttk.LabelFrame(self.scrollable_frame, text="Uninstall / Clean-up", padding="10"); cleanup_frame.pack(fill=tk.X, pady=(20, 5))
        cleanup_header = ttk.Frame(cleanup_frame); cleanup_header.pack(fill=tk.X)
        ttk.Label(cleanup_header, text="Use these actions to remove installations from servers.").pack(side=tk.LEFT)
        ttk.Button(cleanup_header, text=" i ", width=3, command=self.show_cleanup_info).pack(side=tk.RIGHT)
        self.selective_cleanup_btn = ttk.Button(cleanup_frame, text="Selective CAST Imaging Clean-up", command=lambda: self.run_cleanup_thread(selective=True)); self.selective_cleanup_btn.pack(fill=tk.X, pady=5)
        self.complete_cleanup_btn = ttk.Button(cleanup_frame, text="Complete Docker Clean-up (Deletes ALL Docker Data)", command=lambda: self.run_cleanup_thread(selective=False)); self.complete_cleanup_btn.pack(fill=tk.X, pady=5)

    def set_all_buttons_state(self, state):
        self.docker_install_btn.config(state=state)
        self.download_btn.config(state=state); self.fetch_config_btn.config(state=state)
        self.upload_config_btn.config(state=state); self.install_btn.config(state=state)
        self.selective_cleanup_btn.config(state=state); self.complete_cleanup_btn.config(state=state)

    def run_cleanup_thread(self, selective=True):
        all_servers = self._get_all_unique_servers()
        if not all_servers: messagebox.showerror("Error", "Please configure at least one server."); return
        if not selective:
            if not messagebox.askyesno("Confirm Complete Clean-up", "WARNING: This will delete ALL Docker containers, images, volumes, and networks on the selected server(s), not just CAST Imaging data.\n\nThis action cannot be undone. Are you absolutely sure you want to continue?"):
                return
        self.set_all_buttons_state(tk.DISABLED)
        self.log_widget.delete('1.0', tk.END); self.status_log_widget.delete('1.0', tk.END)
        if not self._setup_logging(): self.set_all_buttons_state(tk.NORMAL); return
        task = self._selective_cleanup_task if selective else self._complete_cleanup_task
        thread = threading.Thread(target=task, args=(all_servers,)); thread.daemon = True; thread.start()

    def _selective_cleanup_task(self, all_servers):
        try:
            self.logger.info("Starting Selective CAST Imaging Clean-up...")
            target_dir = "~/Cast-Imaging"
            cleanup_cmds = [f"cd {target_dir}/imaging-services && docker compose down", f"cd {target_dir}/imaging-node && docker compose down", f"cd {target_dir}/imaging-viewer && docker compose down", f"cd {target_dir}/imaging-dashboards && docker compose down", f"rm -rf {target_dir}"]
            for server in all_servers:
                self.logger.info(f"Running selective clean-up on {server['host']}...")
                for command in cleanup_cmds:
                    stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], command, self.log_widget)
            self.logger.info("SUCCESS! Selective clean-up process finished.", extra={'tags': ('ok',)})
            messagebox.showinfo("Clean-up Complete", "Selective clean-up process has finished. Check logs for details.")
        except Exception as e:
            self.logger.exception("A critical error occurred during selective clean-up.")
            messagebox.showerror("Task Failed", f"The selective clean-up task failed.\n\nDetails: {e}")
        finally:
            self.set_all_buttons_state(tk.NORMAL)

    def _complete_cleanup_task(self, all_servers):
        try:
            self.logger.info("Starting COMPLETE Docker Clean-up...")
            for server in all_servers:
                self.logger.info(f"Running complete clean-up on {server['host']}...")
                sudo_pass = server.get('sudo_pass')
                if not sudo_pass: raise ValueError(f"Sudo Password is mandatory for complete clean-up on {server['host']}.")
                sudo_prefix = f"echo {shlex.quote(sudo_pass)} | sudo -S "
                cleanup_cmds = [sudo_prefix + "docker stop $(docker ps -a -q)", sudo_prefix + "docker rm $(docker ps -a -q)", sudo_prefix + "docker rmi $(docker images -a -q)", sudo_prefix + "docker builder prune -a -f", sudo_prefix + "docker network prune -f", sudo_prefix + "docker volume prune -a -f"]
                for command in cleanup_cmds:
                    stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], command, self.log_widget)
            self.logger.info("SUCCESS! Complete Docker clean-up process finished.", extra={'tags': ('ok',)})
            messagebox.showinfo("Clean-up Complete", "Complete Docker clean-up process has finished. Check logs for details.")
        except Exception as e:
            self.logger.exception("A critical error occurred during complete clean-up.")
            messagebox.showerror("Task Failed", f"The complete clean-up task failed.\n\nDetails: {e}")
        finally:
            self.set_all_buttons_state(tk.NORMAL)
    
    def run_docker_install_thread(self):
        all_servers = self._get_all_unique_servers()
        if not all_servers: messagebox.showerror("Error", "Please configure at least one server."); return
        self.set_all_buttons_state(tk.DISABLED)
        self.log_widget.delete('1.0', tk.END); self.status_log_widget.delete('1.0', tk.END)
        if not self._setup_logging(): self.set_all_buttons_state(tk.NORMAL); return
        thread = threading.Thread(target=self._docker_install_task, args=(all_servers,)); thread.daemon = True; thread.start()
    def _docker_install_task(self, all_servers):
        try:
            self.logger.info("Beginning Docker installation process...")
            os_family = self.os_family_var.get()
            for server in all_servers:
                self.logger.info(f"Starting Docker installation on {server['host']} for OS family: {os_family}...")
                sudo_pass = server.get('sudo_pass')
                if not sudo_pass: raise ValueError(f"Sudo Password is mandatory for Docker installation on {server['host']}.")
                sudo_prefix = f"echo {shlex.quote(sudo_pass)} | sudo -S "
                
                commands = []
                if "Debian" in os_family:
                    commands.extend([
                        sudo_prefix + "apt-get update", sudo_prefix + "apt-get install -y curl",
                        "curl -fsSL https://get.docker.com -o get-docker.sh", sudo_prefix + "sh get-docker.sh",
                        sudo_prefix + "usermod -aG docker $USER"
                    ])
                elif "CentOS" in os_family:
                    commands.extend([
                        sudo_prefix + "yum install -y yum-utils", sudo_prefix + "yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo",
                        sudo_prefix + "yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin", sudo_prefix + "systemctl start docker",
                        sudo_prefix + "usermod -aG docker $USER"
                    ])
                
                for command in commands:
                    if not stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], command, self.log_widget):
                        raise Exception(f"Command failed on {server['host']}: {command}")
                
                self.logger.info(f"Cleaning up on {server['host']}...")
                stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], "rm get-docker.sh", self.log_widget)

            self.logger.info("SUCCESS! Docker installation process complete.", extra={'tags': ('ok',)})
            messagebox.showinfo("Action Required", "Docker installation is complete.\n\nA REBOOT OR RE-LOGIN on the Linux server(s) is required for user permissions to take effect.")
        except Exception as e:
            self.logger.exception("A critical error occurred during Docker installation.")
            messagebox.showerror("Task Failed", f"The Docker installation task failed.\n\nDetails: {e}\n\nPlease check the logs.")
        finally:
            self.set_all_buttons_state(tk.NORMAL)
            self.fetch_config_btn.config(state=tk.DISABLED); self.upload_config_btn.config(state=tk.DISABLED); self.install_btn.config(state=tk.DISABLED)

    # --- UPDATED METHOD: Removed local file check, starts remote curl task directly ---
    def run_download_thread(self):
        api_key, version = self.api_key_entry.get().strip(), self.version_entry.get().strip()
        if not all([api_key, version]): messagebox.showerror("Error", "API Key and Version must be provided."); return
        all_servers = self._get_all_unique_servers()
        if not all_servers: messagebox.showerror("Error", "Please configure at least one server."); return
        
        self.set_all_buttons_state(tk.DISABLED)
        self.log_widget.delete('1.0', tk.END); self.status_log_widget.delete('1.0', tk.END)
        # Set progress to indeterminate because curl output via paramiko is hard to parse for a smooth bar
        self.progress_bar.config(mode='indeterminate')
        self.progress_bar.start(10)
        
        if not self._setup_logging(): self.set_all_buttons_state(tk.NORMAL); self.progress_bar.stop(); return
        
        # Start the remote download task
        thread = threading.Thread(target=self._prepare_servers_task, args=(api_key, version, all_servers)); 
        thread.daemon = True; thread.start(); self.check_status_queue()

    def check_status_queue(self):
        try:
            message = self.status_queue.get(block=False)
            # Legacy support if we ever add specific progress messages back, otherwise just keeps UI responsive
            if isinstance(message, str) and message.startswith("PROGRESS:"):
                self.progress_bar['value'] = float(message.split(":")[1])
        except queue.Empty: pass
        if self.docker_install_btn['state'] == 'disabled' or self.download_btn['state'] == 'disabled' or self.install_btn['state'] == 'disabled' or self.selective_cleanup_btn['state'] == 'disabled' or self.complete_cleanup_btn['state'] == 'disabled': self.after(100, self.check_status_queue)
    
    # --- UPDATED METHOD: Uses Curl on remote server instead of SFTP ---
    def _prepare_servers_task(self, api_key, version, all_servers):
        try:
            self.logger.info("Starting server preparation via Remote Download (Curl)...")
            target_dir = "~/Cast-Imaging"
            
            # Construct Curl command as requested with -# (progress), -O (remote name), -J (header name)
            curl_cmd = (f'curl -# -O -J "https://extend.castsoftware.com/api/package/download/com.castsoftware.imaging.all.docker/{version}?platform=linux_x64" '
                        f'-H "x-nuget-apikey: {api_key}" -H "accept: application/octet-stream"')

            for server in all_servers:
                self.logger.info(f"Processing server: {server['host']}...")
                
                # 1. Create Target Directory
                check_dir_command = f"mkdir -p {target_dir}"
                if not stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], check_dir_command, self.log_widget):
                    self.logger.error(f"Failed to create directory on {server['host']}. Stopping.")
                    continue
                
                # 2. Clean old zip files to ensure unzip works on the correct file later
                # (Since -J names the file dynamically, we assume the only zip in this fresh folder is the one we want)
                clean_cmd = f"cd {target_dir} && rm -f *.zip"
                stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], clean_cmd, self.log_widget)

                # 3. Execute Curl Download
                self.logger.info(f"Downloading installer on {server['host']}... (This depends on server bandwidth)")
                full_download_cmd = f"cd {target_dir} && {curl_cmd}"
                if not stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], full_download_cmd, self.log_widget):
                     raise Exception(f"Curl download failed on {server['host']}")

                # 4. Unzip
                self.logger.info(f"Unzipping on {server['host']}...")
                # We wildcard *.zip because -J might return different filenames per version
                unzip_command = f"cd {target_dir} && unzip -o -q *.zip"
                if not stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], unzip_command, self.log_widget):
                    raise Exception(f"Failed to unzip on {server['host']}")
                
                # 5. Remove Zip file to save space
                self.logger.info(f"Cleaning up zip file on {server['host']}...")
                cleanup_command = f"cd {target_dir} && rm *.zip"
                stream_command_and_log(server['host'], server['port'], server['user'], server['pass'], cleanup_command, self.log_widget)

            self.logger.info("SUCCESS! All servers prepared.", extra={'tags': ('ok',)})
            self.set_all_buttons_state(tk.NORMAL)
            self.install_btn.config(state=tk.DISABLED); self.upload_config_btn.config(state=tk.DISABLED)
            
            # Reset progress bar
            self.progress_bar.stop(); self.progress_bar.config(mode='determinate'); self.progress_bar['value'] = 100
            
            messagebox.showinfo("Preparation Complete", f"All servers have been successfully prepared in the '{target_dir}' directory.")
        except Exception as e:
            self.logger.exception("A critical error occurred in the preparation thread.")
            self.progress_bar.stop(); self.progress_bar.config(mode='determinate')
            self.set_all_buttons_state(tk.NORMAL); messagebox.showerror("Task Failed", f"The preparation task failed.\n\nDetails: {e}\n\nPlease check the log file.")

    def _install_task(self):
        if not self._setup_logging(): return
        self.logger.info("Starting installation...")
        target_dir = "~/Cast-Imaging"
        try:
            if self.mode_var.get() == 'single':
                details = {k: v.get().strip() for k, v in self.single_server_widgets.items()}
                sudo_pass = details.get('sudo_password')
                if not sudo_pass: raise ValueError("Sudo Password is mandatory for installation.")
                sudo_prefix = f"echo {shlex.quote(sudo_pass)} | sudo -S "
                command_list = [f"cd {target_dir} && {sudo_prefix}chmod +x cast-imaging-install.sh", f"cd {target_dir}", f"cd {target_dir} && sed -i 's/\\r$//' configuration.conf", f"cd {target_dir} && {sudo_prefix}./cast-imaging-install.sh all"]
                self.logger.info("Setting permissions and running installation...")
                for command in command_list:
                    if not stream_command_and_log(details['host'], details['port'], details['username'], details['password'], command, self.log_widget):
                        raise Exception(f"Command failed: {command.split('&&')[-1].strip()}")
            else:
                components = {"imaging-services": self.dist_services_widgets, "imaging-viewer": self.dist_viewer_widgets, "dashboards": self.dist_dashboards_widgets}
                for i, node_widgets in enumerate(self.analysis_nodes_widgets): components[f"analysis-node-{i+1}"] = node_widgets
                for component, widgets in components.items():
                    details = {k: v.get().strip() for k, v in widgets.items()}; host = details['host']
                    if not host: continue
                    sudo_pass = details.get('sudo_password')
                    if not sudo_pass: raise ValueError(f"Sudo Password is mandatory for {host}.")
                    sudo_prefix = f"echo {shlex.quote(sudo_pass)} | sudo -S "
                    self.logger.info(f"Configuring {component} on {host}...")
                    install_cmd_part = "analysis-node" if 'analysis-node' in component else component
                    command_list = [f"cd {target_dir} && {sudo_prefix}chmod +x cast-imaging-install.sh", f"cd {target_dir} && sed -i 's/\\r$//' configuration.conf"]
                    if 'viewer' in component: command_list.insert(1, f"cd {target_dir} && {sudo_prefix}chmod +x cast-imaging-viewer/imagingsetup")
                    command_list.append(f"cd {target_dir} && {sudo_prefix}./cast-imaging-install.sh {install_cmd_part}")
                    for command in command_list:
                        if not stream_command_and_log(host, details['port'], details['username'], details['password'], command, self.log_widget):
                            raise Exception(f"Command failed on {host}: {command}")
            self.logger.info("SUCCESS! Installation Finished.", extra={'tags': ('ok',)})
            self.set_all_buttons_state(tk.NORMAL); messagebox.showinfo("Installation Complete", "The installation scripts have finished running.")
        except Exception as e:
            self.logger.exception("Installation failed."); self.set_all_buttons_state(tk.NORMAL); messagebox.showerror("Task Failed", f"The installation task failed.\n\nDetails: {e}")
    def fetch_config(self):
        self.set_all_buttons_state(tk.DISABLED)
        if not self._setup_logging(): self.set_all_buttons_state(tk.NORMAL); return
        server_widgets = self.single_server_widgets if self.mode_var.get() == 'single' else self.dist_services_widgets
        details = {k: v.get().strip() for k, v in server_widgets.items()}; host = details['host']
        if not host: messagebox.showerror("Error", "Primary server host must be defined."); self.set_all_buttons_state(tk.NORMAL); return
        self.logger.info(f"Determining home directory on {host}...")
        home_path = get_remote_home_path(host, details['port'], details['username'], details['password'], self.log_widget)
        if not home_path: self.logger.error("Could not determine remote home path."); self.set_all_buttons_state(tk.NORMAL); return
        remote_path = f"{home_path}/Cast-Imaging/configuration.conf"
        self.logger.info(f"Fetching configuration file from {remote_path}...")
        if os.path.exists(self.temp_config_file): os.remove(self.temp_config_file)
        if download_file_sftp(host, details['port'], details['username'], details['password'], remote_path, self.temp_config_file, self.log_widget):
            with open(self.temp_config_file, 'r', encoding='utf-8') as f: self.config_text.delete('1.0', tk.END); self.config_text.insert('1.0', f.read())
            self.logger.info("Config fetched successfully.", extra={'tags': ('ok',)}); self.upload_config_btn.config(state=tk.NORMAL)
        else: self.logger.error("FAILED to fetch config file.")
        self.docker_install_btn.config(state=tk.NORMAL); self.download_btn.config(state=tk.NORMAL); self.fetch_config_btn.config(state=tk.NORMAL); self.install_btn.config(state=tk.DISABLED)
    def upload_config(self):
        self.set_all_buttons_state(tk.DISABLED)
        if not self._setup_logging(): self.set_all_buttons_state(tk.NORMAL); return
        content = self.config_text.get('1.0', tk.END).replace('\r\n', '\n')
        with open(self.temp_config_file, 'w', encoding='utf-8') as f: f.write(content)
        all_servers = self._get_all_unique_servers();
        if not all_servers: self.set_all_buttons_state(tk.NORMAL); self.install_btn.config(state=tk.DISABLED); return
        self.logger.info(f"Uploading configuration.conf to '~/Cast-Imaging' on all servers...")
        success_count = 0
        for server in all_servers:
            home_path = get_remote_home_path(server['host'], server['port'], server['user'], server['pass'], self.log_widget)
            if not home_path: self.logger.error(f"Cannot upload to {server['host']}, could not determine home path."); continue
            remote_path = f"{home_path}/Cast-Imaging/configuration.conf"
            if upload_with_progress(server['host'], server['port'], server['user'], server['pass'], self.temp_config_file, remote_path, self.log_widget, self.status_queue): success_count += 1
        if success_count == len(all_servers): self.logger.info("Config uploaded successfully. You can now run the installation.", extra={'tags': ('ok',)}); self.install_btn.config(state=tk.NORMAL)
        else: self.logger.error("FAILED to upload config file to one or more servers.")
        if os.path.exists(self.temp_config_file): os.remove(self.temp_config_file)
        self.docker_install_btn.config(state=tk.NORMAL); self.download_btn.config(state=tk.NORMAL); self.fetch_config_btn.config(state=tk.NORMAL); self.upload_config_btn.config(state=tk.NORMAL)
    def run_install_thread(self): self.set_all_buttons_state(tk.DISABLED); self.log_widget.delete('1.0', tk.END); self.status_log_widget.delete('1.0', tk.END); thread = threading.Thread(target=self._install_task); thread.daemon = True; thread.start(); self.check_status_queue()
    def test_connection_thread(self, widgets):
        host, port, user, password = widgets['host'].get(), widgets['port'].get(), widgets['username'].get(), widgets['password'].get()
        if not all([host, port, user]): messagebox.showerror("Input Error", "Host, Port, and Username are required."); return
        thread = threading.Thread(target=self._test_connection_task, args=(host, port, user, password)); thread.daemon = True; thread.start()
    def _test_connection_task(self, host, port, user, password):
        try:
            client = paramiko.SSHClient(); client.set_missing_host_key_policy(paramiko.AutoAddPolicy()); client.connect(hostname=host, port=int(port), username=user, password=password, timeout=5); client.close()
            messagebox.showinfo("Connection Success", f"Successfully connected to {host}!")
        except Exception as e: messagebox.showerror("Connection Failed", f"Could not connect to {host}.\n\nError: {e}")
    def create_server_details_widgets(self):
        self.single_frame = ttk.Frame(self.server_details_frame); self.single_server_widgets = self._create_ssh_widgets(self.single_frame, "Linux Server")
        self.distributed_frame = ttk.Frame(self.server_details_frame)
        self.dist_services_widgets = self._create_ssh_widgets(self.distributed_frame, "Imaging Services Host"); self.dist_viewer_widgets = self._create_ssh_widgets(self.distributed_frame, "Imaging Viewer Host")
        self.dist_dashboards_widgets = self._create_ssh_widgets(self.distributed_frame, "Dashboards Host")
        self.nodes_frame = ttk.LabelFrame(self.distributed_frame, text="Analysis Node(s)", padding="5"); self.nodes_frame.pack(fill=tk.X, expand=True, pady=5)
        ttk.Button(self.nodes_frame, text="Add Analysis Node", command=self.add_analysis_node).pack(anchor=tk.W); self.add_analysis_node()
    def add_analysis_node(self): self.analysis_nodes_widgets.append(self._create_ssh_widgets(self.nodes_frame, f"Analysis Node #{len(self.analysis_nodes_widgets) + 1}"))
    def update_ui_for_mode(self):
        mode = self.mode_var.get()
        self.distributed_frame.pack_forget() if mode == "single" else self.single_frame.pack_forget()
        self.single_frame.pack(fill=tk.X, expand=True) if mode == "single" else self.distributed_frame.pack(fill=tk.X, expand=True)
    def _get_all_unique_servers(self):
        servers, seen_hosts = [], set()
        def add_server(widgets):
            host = widgets['host'].get().strip()
            if host and host not in seen_hosts: 
                servers.append({
                    "host": host, "port": widgets['port'].get().strip(), 
                    "user": widgets['username'].get().strip(), "pass": widgets['password'].get(),
                    "sudo_pass": widgets['sudo_password'].get()
                })
                seen_hosts.add(host)
        if self.mode_var.get() == "single": add_server(self.single_server_widgets)
        else: add_server(self.dist_services_widgets); add_server(self.dist_viewer_widgets); add_server(self.dist_dashboards_widgets)
        for node_widgets in self.analysis_nodes_widgets: add_server(node_widgets)
        return servers

if __name__ == "__main__":
    app = ImagingInstallerApp()
    app.mainloop()