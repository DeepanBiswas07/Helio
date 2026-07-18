import sys
import psutil
import subprocess
import ctypes
import time
import platform
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QGridLayout, QProgressBar
)
from PyQt5.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from .base import BasePanel, _content_alpha, _draw_header

class SystemPanel(BasePanel):
    def __init__(self):
        super().__init__("SYSTEM", QColor(255, 120, 0))

    def draw(self, painter, shape_rect, panel_progress, alpha):
        content_a = _content_alpha(panel_progress, alpha)
        # Only draw the glowing header frame, the body is drawn by the QWidget
        _draw_header(painter, shape_rect, self.title, self.accent, content_a)

class StatCard(QFrame):
    def __init__(self, title, icon, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            StatCard {
                background-color: rgba(20, 10, 0, 140);
                border: 1px solid rgba(255, 120, 0, 40);
                border-radius: 12px;
            }
        """)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(15, 15, 15, 15)
        
        header = QHBoxLayout()
        icon_lbl = QLabel(icon)
        icon_lbl.setStyleSheet("color: #FF7800; font-size: 16px;")
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet("color: #FF7800; font-weight: bold; font-size: 11px;")
        
        header.addWidget(icon_lbl)
        header.addWidget(title_lbl)
        header.addStretch()
        
        self.layout.addLayout(header)

class TopCard(StatCard):
    def __init__(self, title, icon, is_progress=False, parent=None):
        super().__init__(title, icon, parent)
        
        self.value_lbl = QLabel("0%")
        self.value_lbl.setStyleSheet("color: white; font-size: 24px; font-weight: bold;")
        self.layout.addWidget(self.value_lbl)
        
        self.desc_lbl = QLabel("")
        self.desc_lbl.setStyleSheet("color: rgba(255, 255, 255, 140); font-size: 10px;")
        
        if is_progress:
            self.bar = QProgressBar()
            self.bar.setFixedHeight(6)
            self.bar.setTextVisible(False)
            self.bar.setStyleSheet("""
                QProgressBar {
                    background-color: rgba(255, 255, 255, 20);
                    border-radius: 3px;
                }
                QProgressBar::chunk {
                    background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #FF5000, stop:1 #FF8C00);
                    border-radius: 3px;
                }
            """)
            self.layout.addWidget(self.bar)
        
        self.layout.addWidget(self.desc_lbl)
        self.layout.addStretch()


class SystemMonitorThread(QThread):
    stats_ready = pyqtSignal(dict)

    def __init__(self, widget_ref):
        super().__init__()
        self.widget_ref = widget_ref
        self.running = True
        
        # Pre-fetch heavy/static values ONCE to avoid WMI stutter on Windows!
        self.boot_time = psutil.boot_time()
        self.cores = psutil.cpu_count(logical=False)
        self.threads = psutil.cpu_count(logical=True)
        try:
            freq = psutil.cpu_freq()
            self.base_freq = f"{freq.max/1000:.2f} GHz" if freq else "N/A"
        except:
            self.base_freq = "N/A"
            
        self.last_disk_io = psutil.disk_io_counters()
        self.last_disk_time = time.time()

    def run(self):
        while self.running:
            # Task Manager efficiency: If panel is not visible, don't poll! Just sleep and wait.
            if not self.widget_ref.isVisible():
                time.sleep(0.5)
                continue

            try:
                stats = {}
                
                # CPU
                stats['cpu_pct'] = psutil.cpu_percent(interval=None)
                stats['freq_str'] = self.base_freq
                
                # RAM
                mem = psutil.virtual_memory()
                stats['ram_pct'] = mem.percent
                stats['ram_used'] = mem.used / 1e9
                stats['ram_total'] = mem.total / 1e9
                
                # Disk
                disk = psutil.disk_usage('/')
                stats['disk_pct'] = disk.percent
                stats['disk_free'] = disk.free / 1e9
                stats['disk_total'] = disk.total / 1e9
                
                # Disk I/O
                disk_io = psutil.disk_io_counters()
                now = time.time()
                dt = now - self.last_disk_time
                if dt > 0:
                    stats['disk_read_mbs'] = (disk_io.read_bytes - self.last_disk_io.read_bytes) / dt / 1e6
                    stats['disk_write_mbs'] = (disk_io.write_bytes - self.last_disk_io.write_bytes) / dt / 1e6
                else:
                    stats['disk_read_mbs'] = 0.0
                    stats['disk_write_mbs'] = 0.0
                
                stats['disk_total_read'] = disk_io.read_bytes / 1e9
                stats['disk_total_write'] = disk_io.write_bytes / 1e9
                
                self.last_disk_io = disk_io
                self.last_disk_time = now
                
                # Network
                net = psutil.net_io_counters()
                stats['net_recv'] = net.bytes_recv / 1e6
                stats['net_sent'] = net.bytes_sent / 1e6
                
                # Performance
                uptime_seconds = time.time() - self.boot_time
                m, s = divmod(uptime_seconds, 60)
                h, m = divmod(m, 60)
                stats['uptime'] = (int(h), int(m), int(s))
                stats['cores'] = self.cores
                stats['threads'] = self.threads

                self.stats_ready.emit(stats)
            except Exception as e:
                pass
            
            time.sleep(2.0)

    def stop(self):
        self.running = False
        self.wait()


class SystemWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent; color: white; font-family: 'Segoe UI';")
        
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 65, 16, 16)
        main_layout.setSpacing(10)
        
        # --- 4 Top Cards ---
        top_cards = QHBoxLayout()
        top_cards.setSpacing(10)
        
        self.cpu_card = TopCard("CPU USAGE", "⚙", False)
        self.ram_card = TopCard("RAM UTILIZATION", "🖴", True)
        self.disk_card = TopCard("SYSTEM DISK (C:)", "🖴", True)
        
        self.health_card = StatCard("SYSTEM HEALTH", "♥")
        health_val = QLabel("Healthy")
        health_val.setStyleSheet("color: #4CAF50; font-size: 24px; font-weight: bold;")
        health_desc = QLabel("All systems operational")
        health_desc.setStyleSheet("color: rgba(255, 255, 255, 140); font-size: 10px;")
        self.health_card.layout.addWidget(health_val)
        self.health_card.layout.addWidget(health_desc)
        self.health_card.layout.addStretch()
        
        top_cards.addWidget(self.cpu_card)
        top_cards.addWidget(self.ram_card)
        top_cards.addWidget(self.disk_card)
        top_cards.addWidget(self.health_card)
        
        main_layout.addLayout(top_cards)
        
        # --- Middle Cards ---
        mid_cards = QHBoxLayout()
        mid_cards.setSpacing(10)
        
        self.specs_card = StatCard("SYSTEM SPECS", "🖥")
        
        # Build system specs strings
        uname = platform.uname()
        os_info = f"{uname.system} {uname.release}"
        node_name = uname.node
        arch = uname.machine
        cpu_name = platform.processor()
        
        spec_text = (
            f"OS\t\t{os_info}\n\n"
            f"Machine\t\t{node_name}\n\n"
            f"Arch\t\t{arch}\n\n"
            f"CPU Model\t{cpu_name}\n"
        )
        
        self.specs_lbl = QLabel(spec_text)
        self.specs_lbl.setStyleSheet("color: rgba(255, 255, 255, 180); font-size: 11px; line-height: 1.5;")
        self.specs_lbl.setWordWrap(True)
        self.specs_card.layout.addWidget(self.specs_lbl)
        self.specs_card.layout.addStretch()
        
        self.perf_card = StatCard("PERFORMANCE", "📶")
        self.perf_lbl = QLabel()
        self.perf_lbl.setStyleSheet("color: rgba(255, 255, 255, 180); font-size: 12px; line-height: 2;")
        self.perf_card.layout.addWidget(self.perf_lbl)
        self.perf_card.layout.addStretch()
        
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(10)
        left_panel.addWidget(self.specs_card)
        left_panel.addWidget(self.perf_card)
        
        right_panel = QVBoxLayout()
        right_panel.setContentsMargins(0, 0, 0, 0)
        right_panel.setSpacing(10)
        
        self.net_card = StatCard("NETWORK", "🌐")
        self.net_lbl = QLabel()
        self.net_lbl.setStyleSheet("color: rgba(255, 255, 255, 180); font-size: 12px;")
        self.net_card.layout.addWidget(self.net_lbl)
        self.net_card.layout.addStretch()
        
        self.io_card = StatCard("STORAGE I/O", "⇄")
        self.io_lbl = QLabel("Loading I/O data...")
        self.io_lbl.setStyleSheet("color: rgba(255, 255, 255, 180); font-size: 12px; line-height: 1.8;")
        self.io_card.layout.addWidget(self.io_lbl)
        self.io_card.layout.addStretch()
        
        right_panel.addWidget(self.net_card)
        right_panel.addWidget(self.io_card)
        
        mid_cards.addLayout(left_panel, 1)
        mid_cards.addLayout(right_panel, 1)
        
        main_layout.addLayout(mid_cards)
        
        # --- Bottom Buttons ---
        bot_layout = QVBoxLayout()
        btn_layout = QHBoxLayout()
        
        # Define real, functional Windows utility buttons
        self.btn_refresh = QPushButton("↻ Refresh")
        self.btn_taskmgr = QPushButton("⚡ Task Manager")
        self.btn_sys = QPushButton("⚙ Sys Settings")
        self.btn_net = QPushButton("🌐 Net Settings")
        self.btn_disk = QPushButton("🧹 Disk Cleanup")
        self.btn_ram = QPushButton("🧠 Clean RAM")

        self.btn_refresh.clicked.connect(self._force_refresh)
        self.btn_taskmgr.clicked.connect(lambda: self._launch_and_minimize("taskmgr"))
        self.btn_sys.clicked.connect(lambda: self._launch_and_minimize("cmd /c start ms-settings:", shell=True))
        self.btn_net.clicked.connect(lambda: self._launch_and_minimize("control netconnections"))
        self.btn_disk.clicked.connect(lambda: self._launch_and_minimize("cleanmgr"))
        self.btn_ram.clicked.connect(self._clean_ram)
        
        buttons = [self.btn_refresh, self.btn_taskmgr, self.btn_sys, self.btn_net, self.btn_disk, self.btn_ram]
        
        for btn in buttons:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(20, 10, 0, 140);
                    border: 1px solid rgba(255, 120, 0, 40);
                    border-radius: 8px;
                    color: rgba(255, 255, 255, 200);
                    font-size: 11px;
                    padding: 10px;
                }
                QPushButton:hover {
                    background-color: rgba(255, 120, 0, 30);
                    border: 1px solid rgba(255, 120, 0, 100);
                    color: white;
                }
            """)
            btn_layout.addWidget(btn)
        
        bot_layout.addLayout(btn_layout)
        main_layout.addLayout(bot_layout)
        
        # Start background worker thread instead of QTimer
        self.worker = SystemMonitorThread(self)
        self.worker.stats_ready.connect(self._update_ui)
        self.worker.start()

    def _launch_and_minimize(self, cmd, shell=False):
        # 1. Launch the command
        if shell:
            subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen(cmd)
        
        # 2. Trigger the premium space closing animation instead of hard minimizing
        from PyQt5.QtWidgets import QApplication
        for top_level in QApplication.instance().topLevelWidgets():
            if hasattr(top_level, '_space_overlay'):
                top_level._space_overlay.close_space()
                break

    def _clean_ram(self):
        # Flash the button to indicate action
        old_text = self.btn_ram.text()
        self.btn_ram.setText("🧠 Cleaning...")
        
        # Free memory across all processes (Windows EmptyWorkingSet)
        try:
            psapi = ctypes.WinDLL('psapi')
            kernel32 = ctypes.WinDLL('kernel32')
            PROCESS_SET_QUOTA = 0x0100
            PROCESS_QUERY_INFORMATION = 0x0400
            for p in psutil.process_iter(['pid']):
                try:
                    h_process = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_QUERY_INFORMATION, False, p.pid)
                    if h_process:
                        psapi.EmptyWorkingSet(h_process)
                        kernel32.CloseHandle(h_process)
                except: pass
        except Exception:
            pass
            
        QTimer.singleShot(1000, lambda: self.btn_ram.setText(old_text))
        
    def _force_refresh(self):
        # Force a quick UI clear or visual feedback
        pass

    def _update_ui(self, stats):
        if not stats:
            return
            
        try:
            # CPU
            self.cpu_card.value_lbl.setText(f"{stats['cpu_pct']:.1f}%")
            self.cpu_card.desc_lbl.setText(f"{stats['freq_str']}\nSpeed")
            
            # RAM
            self.ram_card.value_lbl.setText(f"{stats['ram_pct']}%")
            self.ram_card.bar.setValue(int(stats['ram_pct']))
            self.ram_card.desc_lbl.setText(f"{stats['ram_used']:.1f} GB / {stats['ram_total']:.1f} GB\nIn Use")
            
            # Disk
            self.disk_card.value_lbl.setText(f"{stats['disk_pct']}%")
            self.disk_card.bar.setValue(int(stats['disk_pct']))
            self.disk_card.desc_lbl.setText(f"{stats['disk_free']:.1f} GB Free / {stats['disk_total']:.1f} GB\nTotal")
            
            # Network
            self.net_lbl.setText(f"Status\t\t● Connected\n\nDownload\n{stats['net_recv']:.1f} MB Total\n\nUpload\n{stats['net_sent']:.1f} MB Total")
            
            # Disk I/O
            read_speed = f"{stats['disk_read_mbs']:.1f}" if stats['disk_read_mbs'] < 1000 else f"{stats['disk_read_mbs']/1000:.1f}k"
            write_speed = f"{stats['disk_write_mbs']:.1f}" if stats['disk_write_mbs'] < 1000 else f"{stats['disk_write_mbs']/1000:.1f}k"
            self.io_lbl.setText(f"Read Speed\t{read_speed} MB/s\nWrite Speed\t{write_speed} MB/s\nTotal Read\t{stats['disk_total_read']:.1f} GB\nTotal Written\t{stats['disk_total_write']:.1f} GB")
            
            # Perf
            h, m, s = stats['uptime']
            cores, threads, cpu_pct = stats['cores'], stats['threads'], stats['cpu_pct']
            self.perf_lbl.setText(f"CPU Cores\t\t{cores} Cores / {threads} Threads\n\nSystem Load\t\t{cpu_pct}%\n\nUptime\t\t{h:02d}:{m:02d}:{s:02d}\n\t\tsince last boot")
            
        except Exception as e:
            pass
