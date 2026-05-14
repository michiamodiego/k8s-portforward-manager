"""
Port-Forward Manager
GUI tkinter per avviare/chiudere port-forward kubectl verso microservizi.

Esempio comando gestito:
    kubectl port-forward svc/servicename 8091:80 -n namespace

I processi vengono individuati a livello di sistema (psutil) confrontando la
command line di ogni processo kubectl con la configurazione salvata, quindi
funziona anche dopo un riavvio della GUI o se l'esecuzione precedente è
terminata lasciando dei port-forward orfani.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "portforward_config.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")
REFRESH_INTERVAL_MS = 2000


class Environment:
    def __init__(self, label, command):
        self.label = label
        self.command = command

    def to_dict(self):
        return {"label": self.label, "command": self.command}

    @classmethod
    def from_dict(cls, d):
        return cls(d.get("label", ""), d.get("command", ""))


class PortForward:
    def __init__(self, name, resource_type, resource_name, namespace,
                 local_port, remote_port):
        self.name = name
        self.resource_type = resource_type
        self.resource_name = resource_name
        self.namespace = namespace
        self.local_port = str(local_port)
        self.remote_port = str(remote_port)

    def to_dict(self):
        return {
            "name": self.name,
            "resource_type": self.resource_type,
            "resource_name": self.resource_name,
            "namespace": self.namespace,
            "local_port": self.local_port,
            "remote_port": self.remote_port,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            d.get("name", ""),
            d.get("resource_type", "svc"),
            d.get("resource_name", ""),
            d.get("namespace", "default"),
            d.get("local_port", ""),
            d.get("remote_port", ""),
        )

    def kubectl_args(self):
        args = [
            "kubectl", "port-forward",
            f"{self.resource_type}/{self.resource_name}",
            f"{self.local_port}:{self.remote_port}",
        ]
        if self.namespace:
            args.extend(["-n", self.namespace])
        return args

    def label_str(self):
        return f"PF-MANAGER:{self.name}@{self.local_port}"

    def _legacy_match(self, cmdline_list):
        if not cmdline_list:
            return False
        joined = " ".join(cmdline_list).lower()
        if "kubectl" not in joined or "port-forward" not in joined:
            return False
        target_main = f"{self.resource_type}/{self.resource_name}".lower()
        alt_type = {"svc": "service", "service": "svc"}.get(
            self.resource_type.lower())
        target_alt = (f"{alt_type}/{self.resource_name}".lower()
                      if alt_type else None)
        port_spec = f"{self.local_port}:{self.remote_port}"
        has_target = target_main in joined or (
            target_alt is not None and target_alt in joined)
        has_namespace = (not self.namespace or 
                        self.namespace.lower() in joined)
        return (
            has_target
            and port_spec in joined
            and has_namespace
        )

    def find_processes(self):
        if not HAS_PSUTIL:
            return []
        label = self.label_str().lower()
        found = {}
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                cmd = proc.info.get('cmdline') or []
                joined = " ".join(cmd).lower()
                if label in joined:
                    found[proc.pid] = proc
                    try:
                        for child in proc.children(recursive=True):
                            cname = (child.name() or '').lower()
                            if 'kubectl' in cname:
                                found[child.pid] = child
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    continue
                if self._legacy_match(cmd):
                    found[proc.pid] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied,
                    psutil.ZombieProcess):
                continue
        return list(found.values())

    def kubectl_processes(self):
        result = []
        for p in self.find_processes():
            try:
                if 'kubectl' in (p.name() or '').lower():
                    result.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return result


class LogViewer:
    REFRESH_MS = 1000

    def __init__(self, parent, title, log_path):
        self.log_path = log_path
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.geometry("900x500")
        self._last_size = -1

        toolbar = ttk.Frame(self.top, padding=6)
        toolbar.pack(fill=tk.X)
        ttk.Label(toolbar, text=f"Log: {log_path}",
                  foreground="#555").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="Aggiorna",
                   command=self._refresh).pack(side=tk.RIGHT)
        ttk.Button(toolbar, text="Pulisci",
                   command=self._clear).pack(side=tk.RIGHT, padx=4)

        text_frame = ttk.Frame(self.top)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.text = tk.Text(text_frame, wrap="word",
                            font=("Consolas", 9),
                            background="#1e1e1e", foreground="#dcdcdc",
                            insertbackground="#dcdcdc")
        scrollbar = ttk.Scrollbar(text_frame, orient=tk.VERTICAL,
                                  command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.config(state="disabled")

        self.top.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()
        self._schedule = self.top.after(self.REFRESH_MS, self._tick)

    def _tick(self):
        if not self.top.winfo_exists():
            return
        self._refresh()
        self._schedule = self.top.after(self.REFRESH_MS, self._tick)

    def _refresh(self):
        try:
            size = os.path.getsize(self.log_path)
        except OSError:
            content = "(log non ancora creato — clicca 'Avvia portforwarding' su questa riga)"
            size = -2
        else:
            if size == self._last_size:
                return
            try:
                with open(self.log_path, "r", encoding="utf-8",
                          errors="replace") as f:
                    content = f.read()
            except OSError as e:
                content = f"(errore lettura log: {e})"
                size = -2
        self._last_size = size
        at_bottom = self.text.yview()[1] >= 0.999
        self.text.config(state="normal")
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.config(state="disabled")
        if at_bottom:
            self.text.see(tk.END)

    def _clear(self):
        try:
            with open(self.log_path, "wb") as f:
                f.truncate(0)
            self._last_size = -1
            self._refresh()
        except OSError as e:
            messagebox.showerror("Errore",
                                 f"Impossibile svuotare il log: {e}",
                                 parent=self.top)

    def _on_close(self):
        try:
            self.top.after_cancel(self._schedule)
        except Exception:
            pass
        self.top.destroy()


class EnvironmentDialog:
    def __init__(self, parent, title="Ambiente", initial=None):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        defaults = initial or {"label": "", "command": ""}

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="Nome ambiente (label)").grid(
            row=0, column=0, sticky="w", padx=4, pady=4)
        self.label_var = tk.StringVar(value=defaults.get("label", ""))
        ttk.Entry(frm, textvariable=self.label_var, width=40).grid(
            row=0, column=1, sticky="ew", padx=4, pady=4)

        ttk.Label(frm, text="Comando inizializzazione").grid(
            row=1, column=0, sticky="nw", padx=4, pady=4)
        self.command_text = tk.Text(frm, width=50, height=4, wrap="word")
        self.command_text.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        self.command_text.insert("1.0", defaults.get("command", ""))

        ttk.Label(frm, text="(es: kubectl config use-context dev-cluster)",
                  foreground="gray", font=("Segoe UI", 8)).grid(
            row=2, column=1, sticky="w", padx=4, pady=(0, 4))

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, pady=(12, 0), sticky="e")
        ttk.Button(btns, text="Annulla", command=self._cancel).pack(
            side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side=tk.RIGHT)

    def _ok(self):
        label = self.label_var.get().strip()
        command = self.command_text.get("1.0", tk.END).strip()
        if not label:
            messagebox.showerror("Errore", "Il nome ambiente è obbligatorio.")
            return
        if not command:
            messagebox.showerror("Errore", "Il comando è obbligatorio.")
            return
        self.result = {"label": label, "command": command}
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()


class PortForwardDialog:
    def __init__(self, parent, title="Port-forward", initial=None):
        self.result = None
        self.top = tk.Toplevel(parent)
        self.top.title(title)
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        defaults = initial or {
            "name": "",
            "resource_type": "svc",
            "resource_name": "",
            "namespace": "",
            "local_port": "",
            "remote_port": "",
        }

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        rows = [
            ("Nome microservizio (etichetta)", "name"),
            ("Tipo risorsa", "resource_type"),
            ("Nome risorsa kubernetes", "resource_name"),
            ("Namespace", "namespace"),
            ("Porta locale", "local_port"),
            ("Porta remota", "remote_port"),
        ]
        self.vars = {}
        for r, (label, key) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w",
                                            padx=4, pady=4)
            var = tk.StringVar(value=str(defaults.get(key, "")))
            if key == "resource_type":
                widget = ttk.Combobox(
                    frm, textvariable=var, width=28, state="readonly",
                    values=["svc", "service", "pod", "deployment",
                            "statefulset"],
                )
            else:
                widget = ttk.Entry(frm, textvariable=var, width=30)
            widget.grid(row=r, column=1, sticky="ew", padx=4, pady=4)
            self.vars[key] = var

        btns = ttk.Frame(frm)
        btns.grid(row=len(rows), column=0, columnspan=2, pady=(12, 0),
                  sticky="e")
        ttk.Button(btns, text="Annulla", command=self._cancel).pack(
            side=tk.RIGHT, padx=4)
        ttk.Button(btns, text="OK", command=self._ok).pack(side=tk.RIGHT)

    def _ok(self):
        data = {k: v.get().strip() for k, v in self.vars.items()}
        required = ["name", "resource_type", "resource_name", "namespace",
                    "local_port", "remote_port"]
        for k in required:
            if not data[k]:
                messagebox.showerror("Errore", f"Campo obbligatorio: {k}")
                return
        try:
            int(data["local_port"])
            int(data["remote_port"])
        except ValueError:
            messagebox.showerror("Errore", "Le porte devono essere numeri.")
            return
        self.result = data
        self.top.destroy()

    def _cancel(self):
        self.result = None
        self.top.destroy()


class PortForwardManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Port-Forward Manager")
        self.root.geometry("1020x600")
        self.entries = []
        self.environments = []
        self.selected_environment = None
        self.row_widgets = []
        self._load_config()
        self._build_ui()
        self.root.minsize(280, 200)
        self.root.after(100, self._handle_orphans_on_startup)
        self._schedule_refresh()

    def _load_config(self):
        if not os.path.exists(CONFIG_FILE):
            self.entries = []
            self.environments = []
            self.selected_environment = None
            self._save_config()
            return
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                self.entries = [PortForward.from_dict(d) for d in data]
                self.environments = []
                self.selected_environment = None
            else:
                self.entries = [PortForward.from_dict(d) 
                               for d in data.get("portforwards", [])]
                self.environments = [Environment.from_dict(d) 
                                   for d in data.get("environments", [])]
                self.selected_environment = data.get("selected_environment")
        except Exception as e:
            messagebox.showerror(
                "Errore", f"Impossibile caricare {CONFIG_FILE}: {e}")
            self.entries = []
            self.environments = []
            self.selected_environment = None

    def _save_config(self):
        try:
            data = {
                "portforwards": [e.to_dict() for e in self.entries],
                "environments": [e.to_dict() for e in self.environments],
                "selected_environment": self.selected_environment,
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            messagebox.showerror("Errore",
                                 f"Impossibile salvare configurazione: {e}")

    def _build_ui(self):
        env_frame = ttk.LabelFrame(self.root, text="Gestione Ambienti", 
                                   padding=8)
        env_frame.pack(fill=tk.X, padx=8, pady=8)

        env_row1 = ttk.Frame(env_frame)
        env_row1.pack(fill=tk.X, pady=(0, 4))
        
        ttk.Label(env_row1, text="Ambiente:").pack(side=tk.LEFT, padx=(0, 4))
        
        self.env_combo_var = tk.StringVar()
        self.env_combo = ttk.Combobox(env_row1, textvariable=self.env_combo_var,
                                      state="readonly", width=30)
        self.env_combo.pack(side=tk.LEFT, padx=4)
        self.env_combo.bind("<<ComboboxSelected>>", self._on_environment_selected)
        
        self.init_env_btn = ttk.Button(env_row1, text="Inizializza Ambiente",
                                       command=self._on_init_environment)
        self.init_env_btn.pack(side=tk.LEFT, padx=4)
        
        env_row2 = ttk.Frame(env_frame)
        env_row2.pack(fill=tk.X)
        
        self.add_env_btn = ttk.Button(env_row2, text="Aggiungi Ambiente",
                                      command=self._on_add_environment)
        self.add_env_btn.pack(side=tk.LEFT, padx=2)
        self.edit_env_btn = ttk.Button(env_row2, text="Modifica Ambiente",
                                       command=self._on_edit_environment)
        self.edit_env_btn.pack(side=tk.LEFT, padx=2)
        self.del_env_btn = ttk.Button(env_row2, text="Elimina Ambiente",
                                      command=self._on_delete_environment)
        self.del_env_btn.pack(side=tk.LEFT, padx=2)
        
        self._update_environment_combo()

        ttk.Separator(self.root, orient="horizontal").pack(fill=tk.X, pady=4)
        
        toolbar = ttk.Frame(self.root, padding=8)
        toolbar.pack(fill=tk.X)
        self.add_btn = ttk.Button(toolbar, text="Aggiungi",
                                  command=self._on_add)
        self.add_btn.pack(side=tk.LEFT)
        self.refresh_btn = ttk.Button(toolbar, text="Aggiorna stato",
                                      command=self._refresh_status)
        self.refresh_btn.pack(side=tk.LEFT, padx=6)
        self.start_all_btn = ttk.Button(toolbar, text="Start All",
                                        command=self._on_start_all)
        self.start_all_btn.pack(side=tk.LEFT, padx=6)
        self.stop_all_btn = ttk.Button(toolbar, text="Stop All",
                                       command=self._on_stop_all)
        self.stop_all_btn.pack(side=tk.LEFT, padx=6)
        if not HAS_PSUTIL:
            ttk.Label(
                toolbar,
                text="psutil non installato — esegui: pip install psutil",
                foreground="red",
            ).pack(side=tk.LEFT, padx=10)

        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)
        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient=tk.VERTICAL,
                                  command=canvas.yview)
        self.rows_frame = ttk.Frame(canvas)
        self.rows_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        self._canvas = canvas
        self._canvas_window = canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.bind("<Configure>", self._on_canvas_resize)

        self._render_rows()

    def _render_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_widgets = []
        for i, entry in enumerate(self.entries):
            self._render_row(i, entry)
        self.root.after_idle(self._reflow_all_rows)

    def _render_row(self, idx, entry):
        row = ttk.Frame(self.rows_frame, padding=(4, 4))
        row.pack(fill=tk.X)
        ttk.Separator(self.rows_frame, orient="horizontal").pack(fill=tk.X)
        state_lbl = tk.Label(row, text="OFF", width=5, anchor="center",
                             fg="#c20000",
                             font=("Segoe UI", 10, "bold"))
        name_lbl = ttk.Label(row, text=entry.name, anchor="w",
                             font=("Segoe UI", 9, "bold"))
        port_lbl = ttk.Label(row,
                             text=f"{entry.local_port}:{entry.remote_port}",
                             anchor="w")
        ns_lbl = ttk.Label(row, text=entry.namespace or "default", anchor="w")
        status_lbl = ttk.Label(row, text="-", anchor="w")
        start_btn = ttk.Button(row, text="Start",
                               command=lambda i=idx: self._on_start(i))
        stop_btn = ttk.Button(row, text="Stop",
                              command=lambda i=idx: self._on_stop(i))
        edit_btn = ttk.Button(row, text="Modifica",
                              command=lambda i=idx: self._on_edit(i))
        log_btn = ttk.Button(row, text="Log",
                             command=lambda i=idx: self._on_show_log(i))
        del_btn = ttk.Button(row, text="Elimina",
                             command=lambda i=idx: self._on_delete(i))

        for col, widget in enumerate([state_lbl, name_lbl, port_lbl, ns_lbl,
                                      status_lbl, start_btn, stop_btn,
                                      edit_btn, log_btn, del_btn]):
            widget.grid(row=0, column=col, sticky="w", padx=2, pady=1)

        self.row_widgets.append({
            "frame": row,
            "state": state_lbl,
            "name": name_lbl,
            "port": port_lbl,
            "ns": ns_lbl,
            "status": status_lbl,
            "start": start_btn,
            "stop": stop_btn,
            "edit": edit_btn,
            "log": log_btn,
            "delete": del_btn,
        })

    def _on_canvas_resize(self, event):
        self._canvas.itemconfig(self._canvas_window, width=event.width)
        self.root.after_idle(self._reflow_all_rows)

    def _reflow_all_rows(self):
        available = self._canvas.winfo_width()
        if available <= 1:
            return
        for w in self.row_widgets:
            self._reflow_row(w, available)

    def _reflow_row(self, w, available):
        widgets = [w["state"], w["name"], w["port"], w["ns"], w["status"],
                   w["start"], w["stop"], w["edit"], w["log"], w["delete"]]
        PADX = 2
        line = 0
        col = 0
        used = 0
        for widget in widgets:
            widget.grid_forget()
            req = widget.winfo_reqwidth() + 2 * PADX
            if col > 0 and used + req > available:
                line += 1
                col = 0
                used = 0
            widget.grid(row=line, column=col, sticky="w",
                        padx=PADX, pady=1)
            col += 1
            used += req

    def _schedule_refresh(self):
        self._refresh_status()
        self.root.after(REFRESH_INTERVAL_MS, self._schedule_refresh)

    def _refresh_status(self):
        for idx, entry in enumerate(self.entries):
            if idx >= len(self.row_widgets):
                continue
            kubectl_procs = entry.kubectl_processes()
            w = self.row_widgets[idx]
            if kubectl_procs:
                pids = ", ".join(str(p.pid) for p in kubectl_procs)
                w["state"].config(text="ON", fg="#0a7d12")
                w["status"].config(text=f"Attivo (PID {pids})",
                                   foreground="#0a7d12")
                w["start"].state(["disabled"])
                w["stop"].state(["!disabled"])
                w["edit"].state(["disabled"])
                w["delete"].state(["disabled"])
            else:
                w["state"].config(text="OFF", fg="#c20000")
                w["status"].config(text="Non attivo", foreground="gray")
                w["start"].state(["!disabled"])
                w["stop"].state(["disabled"])
                w["edit"].state(["!disabled"])
                w["delete"].state(["!disabled"])

    def _handle_orphans_on_startup(self):
        if not HAS_PSUTIL:
            return
        orphans = []  # lista di (entry, [proc, ...])
        for entry in self.entries:
            procs = entry.find_processes()
            if procs:
                orphans.append((entry, procs))
        if not orphans:
            return
        lines = []
        for entry, procs in orphans:
            pids = ", ".join(str(p.pid) for p in procs)
            lines.append(
                f"  • {entry.name}  "
                f"{entry.local_port}:{entry.remote_port}  "
                f"({entry.namespace or 'default'})  PID {pids}"
            )
        msg = (
            "Trovati port-forward già attivi da una sessione precedente:\n\n"
            + "\n".join(lines)
            + "\n\nVuoi terminarli ora?\n"
            "  • Sì    → li chiudo tutti\n"
            "  • No   → li lascio attivi e li mostro come tali"
        )
        if messagebox.askyesno("Port-forward attivi rilevati", msg):
            self._terminate_processes(
                [p for _, procs in orphans for p in procs])
        self._refresh_status()

    def _terminate_processes(self, procs):
        errors = []
        for p in procs:
            try:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except psutil.TimeoutExpired:
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
                errors.append(f"PID {p.pid}: {e}")
        if errors:
            messagebox.showwarning(
                "Attenzione",
                "Alcuni processi non sono stati terminati:\n"
                + "\n".join(errors))

    def _log_path_for(self, entry):
        os.makedirs(LOG_DIR, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in "-_." else "_"
                            for c in entry.name)
        return os.path.join(
            LOG_DIR, f"{safe_name}_{entry.local_port}.log")

    def _on_start(self, idx):
        entry = self.entries[idx]
        if entry.kubectl_processes():
            messagebox.showinfo(
                "Info", f"Port-forward per '{entry.name}' già attivo.")
            return
        for p in entry.find_processes():
            try:
                p.terminate()
                try:
                    p.wait(timeout=2)
                except psutil.TimeoutExpired:
                    p.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        log_path = self._log_path_for(entry)
        try:
            log_file = open(log_path, "wb")
        except OSError as e:
            messagebox.showerror(
                "Errore", f"Impossibile aprire il log {log_path}: {e}")
            return
        try:
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
            if sys.platform == "win32":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            else:
                popen_kwargs["start_new_session"] = True
            subprocess.Popen(entry.kubectl_args(), **popen_kwargs)
        except FileNotFoundError:
            log_file.close()
            messagebox.showerror(
                "Errore",
                "kubectl non trovato nel PATH. Installa kubectl o "
                "aggiungilo al PATH di sistema.")
            return
        except Exception as e:
            log_file.close()
            messagebox.showerror("Errore",
                                 f"Impossibile avviare port-forward: {e}")
            return
        finally:
            try:
                log_file.close()
            except Exception:
                pass
        self.root.after(800, self._post_start_check, idx, log_path)

    def _post_start_check(self, idx, log_path):
        self._refresh_status()
        entry = self.entries[idx]
        if not entry.kubectl_processes():
            try:
                with open(log_path, "r", encoding="utf-8",
                          errors="replace") as f:
                    log_content = f.read().strip()
            except OSError:
                log_content = "(log non leggibile)"
            messagebox.showwarning(
                "kubectl non avviato",
                f"Il processo kubectl per '{entry.name}' è uscito subito.\n\n"
                f"Output ({log_path}):\n\n"
                + (log_content if log_content else "(log vuoto)"))

    def _on_stop(self, idx):
        entry = self.entries[idx]
        procs = entry.find_processes()
        if not procs:
            messagebox.showinfo(
                "Info",
                f"Nessun processo attivo trovato per '{entry.name}'.")
            self._refresh_status()
            return
        self._terminate_processes(procs)
        self._refresh_status()

    def _on_start_all(self):
        if not HAS_PSUTIL:
            return
        inactive_entries = []
        for entry in self.entries:
            if not entry.kubectl_processes():
                inactive_entries.append(entry)
        if not inactive_entries:
            messagebox.showinfo("Info", "Tutti i port-forward sono già attivi.")
            return
        if not messagebox.askyesno(
                "Conferma",
                f"Avviare tutti i {len(inactive_entries)} port-forward non attivi?"):
            return
        for entry in inactive_entries:
            idx = self.entries.index(entry)
            self._on_start(idx)
        self._refresh_status()

    def _on_stop_all(self):
        if not HAS_PSUTIL:
            return
        all_procs = []
        for entry in self.entries:
            all_procs.extend(entry.find_processes())
        if not all_procs:
            messagebox.showinfo("Info", "Nessun port-forward attivo.")
            return
        if not messagebox.askyesno(
                "Conferma",
                f"Terminare tutti i {len(all_procs)} port-forward attivi?"):
            return
        self._terminate_processes(all_procs)
        self._refresh_status()

    def _on_add(self):
        dlg = PortForwardDialog(self.root, title="Aggiungi port-forward")
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.entries.append(PortForward.from_dict(dlg.result))
            self._save_config()
            self._render_rows()
            self._refresh_status()

    def _on_show_log(self, idx):
        entry = self.entries[idx]
        log_path = self._log_path_for(entry)
        LogViewer(self.root,
                  title=f"Log — {entry.name} ({entry.local_port}:{entry.remote_port})",
                  log_path=log_path)

    def _on_edit(self, idx):
        entry = self.entries[idx]
        if entry.find_processes():
            messagebox.showwarning(
                "Attenzione",
                "Chiudi il port-forward prima di modificarlo.")
            return
        dlg = PortForwardDialog(self.root, title="Modifica port-forward",
                                initial=entry.to_dict())
        self.root.wait_window(dlg.top)
        if dlg.result:
            self.entries[idx] = PortForward.from_dict(dlg.result)
            self._save_config()
            self._render_rows()
            self._refresh_status()

    def _on_delete(self, idx):
        entry = self.entries[idx]
        if entry.find_processes():
            messagebox.showwarning(
                "Attenzione",
                "Chiudi il port-forward prima di eliminarlo.")
            return
        if not messagebox.askyesno(
                "Conferma",
                f"Eliminare la configurazione '{entry.name}'?"):
            return
        del self.entries[idx]
        self._save_config()
        self._render_rows()
        self._refresh_status()

    def _disable_all_controls(self):
        self.env_combo.config(state="disabled")
        self.init_env_btn.config(state="disabled")
        self.add_env_btn.config(state="disabled")
        self.edit_env_btn.config(state="disabled")
        self.del_env_btn.config(state="disabled")
        self.add_btn.config(state="disabled")
        self.refresh_btn.config(state="disabled")
        self.start_all_btn.config(state="disabled")
        self.stop_all_btn.config(state="disabled")
        for w in self.row_widgets:
            w["start"].config(state="disabled")
            w["stop"].config(state="disabled")
            w["edit"].config(state="disabled")
            w["log"].config(state="disabled")
            w["delete"].config(state="disabled")

    def _enable_all_controls(self):
        self.env_combo.config(state="readonly")
        self.init_env_btn.config(state="normal")
        self.add_env_btn.config(state="normal")
        self.edit_env_btn.config(state="normal")
        self.del_env_btn.config(state="normal")
        self.add_btn.config(state="normal")
        self.refresh_btn.config(state="normal")
        self.start_all_btn.config(state="normal")
        self.stop_all_btn.config(state="normal")
        self._refresh_status()

    def _update_environment_combo(self):
        env_labels = [env.label for env in self.environments]
        self.env_combo["values"] = env_labels
        if self.selected_environment and self.selected_environment in env_labels:
            self.env_combo_var.set(self.selected_environment)
        elif env_labels:
            self.env_combo_var.set(env_labels[0])
            self.selected_environment = env_labels[0]
        else:
            self.env_combo_var.set("")
            self.selected_environment = None

    def _on_environment_selected(self, event=None):
        selected = self.env_combo_var.get()
        if selected:
            self.selected_environment = selected
            self._save_config()

    def _on_init_environment(self):
        if not self.selected_environment:
            messagebox.showwarning("Attenzione", 
                                 "Nessun ambiente selezionato.")
            return
        
        env = None
        for e in self.environments:
            if e.label == self.selected_environment:
                env = e
                break
        
        if not env:
            messagebox.showerror("Errore", 
                               "Ambiente selezionato non trovato.")
            return
        
        active_pfs = []
        if HAS_PSUTIL:
            for entry in self.entries:
                procs = entry.find_processes()
                if procs:
                    active_pfs.append((entry, procs))
        
        confirm_msg = f"Ambiente: {env.label}\n\nComando da eseguire:\n{env.command}\n\n"
        
        if active_pfs:
            pf_count = sum(len(procs) for _, procs in active_pfs)
            pf_names = ", ".join(entry.name for entry, _ in active_pfs)
            confirm_msg += (
                f"⚠️ ATTENZIONE: Ci sono {pf_count} port-forward attivi "
                f"({pf_names})\n\n"
                f"Verranno terminati automaticamente prima dell'inizializzazione.\n\n"
            )
        
        confirm_msg += "Procedere?"
        
        if not messagebox.askyesno("Conferma inizializzazione", confirm_msg):
            return
        
        self._disable_all_controls()
        self.root.update_idletasks()
        
        try:
            if active_pfs:
                all_procs = [p for _, procs in active_pfs for p in procs]
                self._terminate_processes(all_procs)
                self._refresh_status()
            
            if sys.platform == "win32":
                result = subprocess.run(
                    ["cmd", "/c", env.command],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            else:
                result = subprocess.run(
                    env.command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=30
                )
            
            if result.returncode == 0:
                messagebox.showinfo(
                    "Successo",
                    f"Ambiente '{env.label}' inizializzato correttamente.\n\n"
                    f"Output:\n{result.stdout}"
                )
            else:
                messagebox.showerror(
                    "Errore",
                    f"Inizializzazione fallita (exit code {result.returncode}).\n\n"
                    f"Output:\n{result.stdout}\n\n"
                    f"Errori:\n{result.stderr}"
                )
        except subprocess.TimeoutExpired:
            messagebox.showerror(
                "Errore",
                "Il comando ha impiegato troppo tempo (timeout 30s)."
            )
        except Exception as e:
            messagebox.showerror(
                "Errore",
                f"Impossibile eseguire il comando:\n{e}"
            )
        finally:
            self._enable_all_controls()

    def _on_add_environment(self):
        dlg = EnvironmentDialog(self.root, title="Aggiungi ambiente")
        self.root.wait_window(dlg.top)
        if dlg.result:
            for env in self.environments:
                if env.label == dlg.result["label"]:
                    messagebox.showerror(
                        "Errore",
                        f"Esiste già un ambiente con nome '{env.label}'."
                    )
                    return
            self.environments.append(Environment.from_dict(dlg.result))
            self._save_config()
            self._update_environment_combo()

    def _on_edit_environment(self):
        if not self.selected_environment:
            messagebox.showwarning("Attenzione", 
                                 "Nessun ambiente selezionato.")
            return
        
        env = None
        env_idx = -1
        for i, e in enumerate(self.environments):
            if e.label == self.selected_environment:
                env = e
                env_idx = i
                break
        
        if not env:
            messagebox.showerror("Errore", 
                               "Ambiente selezionato non trovato.")
            return
        
        active_pfs = []
        if HAS_PSUTIL:
            for entry in self.entries:
                procs = entry.find_processes()
                if procs:
                    active_pfs.append((entry, procs))
        
        if active_pfs:
            pf_count = sum(len(procs) for _, procs in active_pfs)
            pf_names = ", ".join(entry.name for entry, _ in active_pfs)
            confirm_msg = (
                f"Per modificare l'ambiente '{self.selected_environment}' è necessario "
                f"terminare i port-forward attivi.\n\n"
                f"⚠️ Ci sono {pf_count} port-forward attivi ({pf_names})\n\n"
                f"Verranno terminati automaticamente.\n\n"
                f"Procedere?"
            )
            if not messagebox.askyesno("Conferma modifica", confirm_msg):
                return
            
            all_procs = [p for _, procs in active_pfs for p in procs]
            self._terminate_processes(all_procs)
            self._refresh_status()
        
        dlg = EnvironmentDialog(self.root, title="Modifica ambiente",
                              initial=env.to_dict())
        self.root.wait_window(dlg.top)
        if dlg.result:
            for i, e in enumerate(self.environments):
                if i != env_idx and e.label == dlg.result["label"]:
                    messagebox.showerror(
                        "Errore",
                        f"Esiste già un ambiente con nome '{e.label}'."
                    )
                    return
            old_label = self.environments[env_idx].label
            self.environments[env_idx] = Environment.from_dict(dlg.result)
            if old_label == self.selected_environment:
                self.selected_environment = self.environments[env_idx].label
            self._save_config()
            self._update_environment_combo()

    def _on_delete_environment(self):
        if not self.selected_environment:
            messagebox.showwarning("Attenzione", 
                                 "Nessun ambiente selezionato.")
            return
        
        active_pfs = []
        if HAS_PSUTIL:
            for entry in self.entries:
                procs = entry.find_processes()
                if procs:
                    active_pfs.append((entry, procs))
        
        confirm_msg = f"Eliminare l'ambiente '{self.selected_environment}'?"
        
        if active_pfs:
            pf_count = sum(len(procs) for _, procs in active_pfs)
            pf_names = ", ".join(entry.name for entry, _ in active_pfs)
            confirm_msg = (
                f"Eliminare l'ambiente '{self.selected_environment}'?\n\n"
                f"⚠️ ATTENZIONE: Ci sono {pf_count} port-forward attivi "
                f"({pf_names})\n\n"
                f"Verranno terminati automaticamente."
            )
        
        if not messagebox.askyesno("Conferma", confirm_msg):
            return
        
        if active_pfs:
            all_procs = [p for _, procs in active_pfs for p in procs]
            self._terminate_processes(all_procs)
            self._refresh_status()
        
        self.environments = [e for e in self.environments 
                           if e.label != self.selected_environment]
        self.selected_environment = None
        self._save_config()
        self._update_environment_combo()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    PortForwardManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
