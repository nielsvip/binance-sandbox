import sys
import paramiko
import os
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, 
                             QTextEdit, QGroupBox, QFormLayout, QListWidget, 
                             QListWidgetItem, QProgressBar, QMessageBox, QCheckBox,
                             QFileDialog, QAction, QMenu)
from PyQt5.QtCore import Qt, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QPalette
import re
from datetime import datetime

def apply_dark_theme(app):
    app.setStyle("Fusion")
    dark_palette = QPalette()
    dark_palette.setColor(QPalette.Window, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.WindowText, Qt.white)
    dark_palette.setColor(QPalette.Base, QColor(35, 35, 35))
    dark_palette.setColor(QPalette.Text, Qt.white)
    dark_palette.setColor(QPalette.Button, QColor(53, 53, 53))
    dark_palette.setColor(QPalette.ButtonText, Qt.white)
    app.setPalette(dark_palette)

class SSHWorker(QThread):
    finished = pyqtSignal(list)
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, account, ticker, side, search_terms, search_archived, search_actions_log):
        super().__init__()
        self.account = account
        self.ticker = ticker
        self.side = side
        self.search_terms = search_terms
        self.search_archived = search_archived
        self.search_actions_log = search_actions_log
        self.running = True

    def run(self):
        try:
            results = []
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect('157.180.125.52', username='niels', password='flowsy')
            
            # Process account-specific logs (ez_manage)
            account_log_files = [f"ez_manage_{self.account}.log"]
            if self.search_archived:
                for i in range(1, 10):
                    account_log_files.append(f"ez_manage_{self.account}.log.{i}")
            
            for log_file in account_log_files:
                if not self.running:
                    break
                    
                self.progress.emit(f"Searching {log_file}...")
                
                # Check if file exists
                stdin, stdout, stderr = ssh.exec_command(f"ls /home/niels/logs/{log_file}")
                if not stdout.read().strip():
                    self.progress.emit(f"Skipping {log_file} (not found)")
                    continue
                
                # Build grep command for account logs - use the SAME approach as actions.log
                filters = []
                
                # For account logs, we apply all filters
                if self.ticker:
                    filters.append(f"grep \"{self.ticker}\"")
                if self.side:
                    filters.append(f"grep -i \"{self.side}\"")
                for term in self.search_terms:
                    if term.strip():
                        filters.append(f"grep -i \"{term}\"")
                
                if filters:
                    grep_cmd = f"cat /home/niels/logs/{log_file} | {' | '.join(filters)}"
                else:
                    grep_cmd = f"cat /home/niels/logs/{log_file}"
                
                self.progress.emit(f"Running: {grep_cmd}")
                
                # Execute command
                stdin, stdout, stderr = ssh.exec_command(grep_cmd)
                lines = stdout.readlines()
                error_output = stderr.read()
                
                if error_output:
                    self.progress.emit(f"Grep warning: {error_output}")
                
                for line in lines:
                    if not self.running:
                        break
                    line = line.strip()
                    if line:
                        timestamp_match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
                        timestamp_str = timestamp_match.group(1) if timestamp_match else "No timestamp"
                        results.append((timestamp_str, line, log_file))
            
            # Process actions.log
            if self.search_actions_log:
                actions_log_files = ["actions.log"]
                if self.search_archived:
                    for i in range(1, 10):
                        actions_log_files.append(f"actions.log.{i}")
                
                for log_file in actions_log_files:
                    if not self.running:
                        break
                        
                    self.progress.emit(f"Searching {log_file} for account {self.account}...")
                    
                    # Check if file exists
                    stdin, stdout, stderr = ssh.exec_command(f"ls /home/niels/logs/{log_file}")
                    if not stdout.read().strip():
                        self.progress.emit(f"Skipping {log_file} (not found)")
                        continue
                    
                    # Build grep command for actions.log
                    filters = [f"grep -i \"{self.account}\""]
                    
                    if self.ticker:
                        filters.append(f"grep \"{self.ticker}\"")
                    
                    grep_cmd = f"cat /home/niels/logs/{log_file} | {' | '.join(filters)}"
                    self.progress.emit(f"Running: {grep_cmd}")
                    
                    # Execute command
                    stdin, stdout, stderr = ssh.exec_command(grep_cmd)
                    lines = stdout.readlines()
                    error_output = stderr.read()
                    
                    if error_output:
                        self.progress.emit(f"Grep warning: {error_output}")
                    
                    for line in lines:
                        if not self.running:
                            break
                        line = line.strip()
                        if line:
                            timestamp_match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
                            timestamp_str = timestamp_match.group(1) if timestamp_match else "No timestamp"
                            results.append((timestamp_str, line, log_file))
            
            ssh.close()
            if self.running:
                # Sort by timestamp (newest first)
                results.sort(key=lambda x: self.parse_timestamp(x[0]), reverse=True)
                self.finished.emit(results)
                
        except Exception as e:
            if self.running:
                self.error.emit(f"SSH Error: {str(e)}")

    def parse_timestamp(self, timestamp_str):
        try:
            return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.min

    def stop(self):
        self.running = False

# class SSHWorker(QThread):
#     finished = pyqtSignal(list)
#     error = pyqtSignal(str)
#     progress = pyqtSignal(str)

#     def __init__(self, account, ticker, side, search_terms, search_archived, search_actions_log):
#         super().__init__()
#         self.account = account
#         self.ticker = ticker
#         self.side = side
#         self.search_terms = search_terms
#         self.search_archived = search_archived
#         self.search_actions_log = search_actions_log
#         self.running = True

#     def run(self):
#         try:
#             results = []
#             ssh = paramiko.SSHClient()
#             ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#             ssh.connect('157.180.125.52', username='niels', password='flowsy')
            
#             # Search account-specific logs
#             log_files = [f"ez_manage_{self.account}.log"]
#             if self.search_archived:
#                 for i in range(1, 10):
#                     log_files.append(f"ez_manage_{self.account}.log.{i}")
            
#             # Process account-specific logs
#             for log_file in log_files:
#                 if not self.running:
#                     break
                    
#                 self.progress.emit(f"Searching {log_file}...")
                
#                 # Check if file exists
#                 stdin, stdout, stderr = ssh.exec_command(f"ls /home/niels/logs/{log_file}")
#                 if not stdout.read().strip():
#                     self.progress.emit(f"Skipping {log_file} (not found)")
#                     continue
                
#                 # Build the grep command for account logs
#                 filters = []

#                 # Add ticker filter (case sensitive)
#                 if self.ticker:
#                     filters.append(f"grep \"{self.ticker}\"")

#                 # Add side filter (case insensitive)
#                 if self.side:
#                     filters.append(f"grep -i \"{self.side}\"")

#                 # Add search term filters
#                 for term in self.search_terms:
#                     if term.strip():
#                         filters.append(f"grep -i \"{term}\"")

#                 if filters:
#                     grep_cmd = f"cat /home/niels/logs/{log_file} | {' | '.join(filters)}"
#                 else:
#                     grep_cmd = f"cat /home/niels/logs/{log_file}"
                
#                 # Execute command
#                 stdin, stdout, stderr = ssh.exec_command(grep_cmd)
#                 lines = stdout.readlines()
                
#                 for line in lines:
#                     if not self.running:
#                         break
#                     line = line.strip()
#                     if line:
#                         timestamp_match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
#                         timestamp_str = timestamp_match.group(1) if timestamp_match else "No timestamp"
#                         results.append((timestamp_str, line, log_file))
            
#             # Process actions.log separately with account and ticker filtering
#             if self.search_actions_log:
#                 actions_log_files = ["actions.log"]
#                 if self.search_archived:
#                     for i in range(1, 10):
#                         actions_log_files.append(f"actions.log.{i}")
                
#                 for log_file in actions_log_files:
#                     if not self.running:
#                         break
                        
#                     self.progress.emit(f"Searching {log_file} for {self.account}...")
                    
#                     # Check if file exists
#                     stdin, stdout, stderr = ssh.exec_command(f"ls /home/niels/logs/{log_file}")
#                     if not stdout.read().strip():
#                         self.progress.emit(f"Skipping {log_file} (not found)")
#                         continue
                    
#                     # Build grep command for actions.log - always filter by account
#                     filters = [f"grep -i \"{self.account}\""]  # Always filter by account name
                    
#                     # Add ticker filter if specified (case sensitive)
#                     if self.ticker:
#                         filters.append(f"grep \"{self.ticker}\"")
                    
#                     # For actions.log, we typically don't want side or search terms
#                     # as they are action-specific and might not have those fields
                    
#                     grep_cmd = f"cat /home/niels/logs/{log_file} | {' | '.join(filters)}"
                    
#                     # Execute command
#                     stdin, stdout, stderr = ssh.exec_command(grep_cmd)
#                     lines = stdout.readlines()
                    
#                     for line in lines:
#                         if not self.running:
#                             break
#                         line = line.strip()
#                         if line:
#                             timestamp_match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
#                             timestamp_str = timestamp_match.group(1) if timestamp_match else "No timestamp"
#                             results.append((timestamp_str, line, log_file))
            
#             ssh.close()
#             if self.running:
#                 # Sort by timestamp (newest first)
#                 results.sort(key=lambda x: self.parse_timestamp(x[0]), reverse=True)
#                 self.finished.emit(results)
                
#         except Exception as e:
#             if self.running:
#                 self.error.emit(f"SSH Error: {str(e)}")

#     def parse_timestamp(self, timestamp_str):
#         try:
#             return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
#         except:
#             return datetime.min

#     def stop(self):
#         self.running = False



# class SSHWorker(QThread):
#     finished = pyqtSignal(list)
#     error = pyqtSignal(str)
#     progress = pyqtSignal(str)

#     def __init__(self, account, ticker, side, search_terms, search_archived, search_actions_log):
#         super().__init__()
#         self.account = account
#         self.ticker = ticker
#         self.side = side
#         self.search_terms = search_terms
#         self.search_archived = search_archived
#         self.search_actions_log = search_actions_log
#         self.running = True

#     def run(self):
#         try:
#             results = []
#             ssh = paramiko.SSHClient()
#             ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
#             ssh.connect('157.180.125.52', username='niels', password='flowsy')
            
#             # Search account-specific logs
#             log_files = [f"ez_manage_{self.account}.log"]
#             if self.search_archived:
#                 for i in range(1, 10):
#                     log_files.append(f"ez_manage_{self.account}.log.{i}")
            
#             # Add actions.log if requested
#             if self.search_actions_log:
#                 log_files.append("actions.log")
#                 if self.search_archived:
#                     for i in range(1, 10):
#                         log_files.append(f"actions.log.{i}")
            
#             for log_file in log_files:
#                 if not self.running:
#                     break
                    
#                 self.progress.emit(f"Searching {log_file}...")
                
#                 # Check if file exists
#                 stdin, stdout, stderr = ssh.exec_command(f"ls /home/niels/logs/{log_file}")
#                 if not stdout.read().strip():
#                     self.progress.emit(f"Skipping {log_file} (not found)")
#                     continue
                
#                 # Build the grep command
#                 filters = []

#                 # Add ticker filter (case sensitive)
#                 if self.ticker:
#                     filters.append(f"grep \"{self.ticker}\"")

#                 # Add side filter (case insensitive)
#                 if self.side:
#                     filters.append(f"grep -i \"{self.side}\"")

#                 # Add search term filters
#                 for term in self.search_terms:
#                     if term.strip():
#                         filters.append(f"grep -i \"{term}\"")

#                 if filters:
#                     grep_cmd = f"cat /home/niels/logs/{log_file} | {' | '.join(filters)}"
#                 else:
#                     grep_cmd = f"cat /home/niels/logs/{log_file}"
                
#                 # Execute command
#                 stdin, stdout, stderr = ssh.exec_command(grep_cmd)
#                 lines = stdout.readlines()
                
#                 for line in lines:
#                     if not self.running:
#                         break
#                     line = line.strip()
#                     if line:
#                         timestamp_match = re.search(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]', line)
#                         timestamp_str = timestamp_match.group(1) if timestamp_match else "No timestamp"
#                         results.append((timestamp_str, line, log_file))
            
#             ssh.close()
#             if self.running:
#                 # Sort by timestamp (newest first)
#                 results.sort(key=lambda x: self.parse_timestamp(x[0]), reverse=True)
#                 self.finished.emit(results)
                
#         except Exception as e:
#             if self.running:
#                 self.error.emit(f"SSH Error: {str(e)}")

#     def parse_timestamp(self, timestamp_str):
#         try:
#             return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
#         except:
#             return datetime.min

#     def stop(self):
#         self.running = False

class LogViewerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.log_data = []
        self.initUI()

    def initUI(self):
        self.setWindowTitle("SSH Log Viewer")
        self.setGeometry(100, 100, 1400, 900)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Left panel
        left_panel = QWidget()
        left_panel.setMaximumWidth(400)
        left_layout = QVBoxLayout(left_panel)
        
        search_group = QGroupBox("Search Criteria")
        search_layout = QFormLayout()
        
        self.account_input = QLineEdit()
        self.ticker_input = QLineEdit()
        self.side_input = QLineEdit()
        self.search1_input = QLineEdit()
        self.search2_input = QLineEdit()
        self.search3_input = QLineEdit()
        self.archived_checkbox = QCheckBox("Search archived logs")
        self.archived_checkbox.setChecked(False)
        self.actions_log_checkbox = QCheckBox("Also search actions.log")
        self.actions_log_checkbox.setChecked(True)
        
        for widget in [self.account_input, self.ticker_input, self.side_input, 
                    self.search1_input, self.search2_input, self.search3_input]:
            widget.setStyleSheet("color: white; background-color: #353535;")
        
        self.archived_checkbox.setStyleSheet("color: white;")
        self.actions_log_checkbox.setStyleSheet("color: white;")
        
        search_layout.addRow("Account*:", self.account_input)
        search_layout.addRow("Ticker:", self.ticker_input)
        search_layout.addRow("Side:", self.side_input)
        search_layout.addRow("Search Term 1:", self.search1_input)
        search_layout.addRow("Search Term 2:", self.search2_input)
        search_layout.addRow("Search Term 3:", self.search3_input)
        search_layout.addRow(self.archived_checkbox)
        search_layout.addRow(self.actions_log_checkbox)
        
        search_group.setLayout(search_layout)
        left_layout.addWidget(search_group)
        
        # Buttons
        self.search_btn = QPushButton("Search Logs")
        self.search_btn.setDefault(True)
        self.search_btn.setStyleSheet("QPushButton { background-color: #4C776F; color: white; font-weight: bold; padding: 8px; }")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setStyleSheet("QPushButton { background-color: #835850; color: white; padding: 8px; }")
        self.stop_btn.setEnabled(False)
        self.clear_btn = QPushButton("Clear Results")
        self.clear_btn.setStyleSheet("QPushButton { background-color: #485037; color: white; padding: 8px; }")
        self.save_btn = QPushButton("Save to File")
        self.save_btn.setStyleSheet("QPushButton { background-color: #6A774C; color: white; padding: 8px; }")
        self.test_btn = QPushButton("Test SSH Connection")
        self.test_btn.setStyleSheet("QPushButton { background-color: #434740; color: white; padding: 8px; }")
        
        self.search_btn.clicked.connect(self.start_search)
        self.stop_btn.clicked.connect(self.stop_search)
        self.clear_btn.clicked.connect(self.clear_results)
        self.save_btn.clicked.connect(self.save_to_file)
        self.test_btn.clicked.connect(self.test_ssh_connection)
        
        for input_field in [self.account_input, self.ticker_input, self.side_input,
                        self.search1_input, self.search2_input, self.search3_input]:
            input_field.returnPressed.connect(self.start_search)
        
        left_layout.addWidget(self.search_btn)
        left_layout.addWidget(self.stop_btn)
        left_layout.addWidget(self.clear_btn)
        left_layout.addWidget(self.save_btn)
        left_layout.addWidget(self.test_btn)
        
        # Sort button
        self.sort_btn = QPushButton("Sort Newest First")
        self.sort_btn.setStyleSheet("QPushButton { background-color: #845C2B; color: white; padding: 8px; }")
        self.sort_btn.clicked.connect(self.sort_results_newest_first)
        left_layout.addWidget(self.sort_btn)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("QProgressBar { color: white; background-color: #252525; }")
        left_layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Ready to search logs")
        self.status_label.setStyleSheet("color: #AAAAAA; background-color: #252525; padding: 5px; border-radius: 3px;")
        self.status_label.setWordWrap(True)
        left_layout.addWidget(self.status_label)
        
        left_layout.addStretch()
        main_layout.addWidget(left_panel)
        
        # Right panel
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("Filter:"))
        self.filter_input = QLineEdit()
        self.filter_input.textChanged.connect(self.filter_results)
        filter_layout.addWidget(self.filter_input)
        right_layout.addLayout(filter_layout)
        
        self.results_list = QListWidget()
        self.results_list.itemClicked.connect(self.show_log_details)
        self.results_list.setStyleSheet("""
            QListWidget {
                color: white;
                background-color: #252525;
                border: 1px solid #555555;
            }
            QListWidget::item:selected {
                background-color: #2B5C84;
            }
        """)
        right_layout.addWidget(self.results_list)
        
        self.details_display = QTextEdit()
        self.details_display.setReadOnly(True)
        self.details_display.setMaximumHeight(200)
        self.details_display.setStyleSheet("""
            QTextEdit {
                color: white;
                background-color: #252525;
                border: 1px solid #555555;
            }
        """)
        right_layout.addWidget(self.details_display)
        
        main_layout.addWidget(right_panel, 1)
        
        # Set defaults
        self.account_input.setText("fin")
        self.ticker_input.setText("BTCUSDC")
        self.side_input.setText("LONG")
        
        # Update status label with shortcuts
        self.status_label.setText("Ready | Ctrl+↑: First | Ctrl+↓: Last | Ctrl+Home: Top | Ctrl+End: Bottom")
        
        # Create menu bar (must be done after all UI elements are created)
        menubar = self.menuBar()
        view_menu = menubar.addMenu('Navigation')
        
        # Add keyboard shortcut hints
        help_action = QAction('Shortcuts: Ctrl+↑/↓ to jump, Ctrl+Home/End to scroll', self)
        help_action.setEnabled(False)
        view_menu.addAction(help_action)
        view_menu.addSeparator()
        
        # Add navigation actions
        first_action = QAction('Jump to First (Oldest)', self)
        first_action.setShortcut('Ctrl+Up')
        first_action.triggered.connect(self.jump_to_first_item)
        view_menu.addAction(first_action)
        
        last_action = QAction('Jump to Last (Newest)', self)
        last_action.setShortcut('Ctrl+Down')
        last_action.triggered.connect(self.jump_to_last_item)
        view_menu.addAction(last_action)
        
        top_action = QAction('Scroll to Top', self)
        top_action.setShortcut('Ctrl+Home')
        top_action.triggered.connect(self.results_list.scrollToTop)
        view_menu.addAction(top_action)
        
        bottom_action = QAction('Scroll to Bottom', self)
        bottom_action.setShortcut('Ctrl+End')
        bottom_action.triggered.connect(self.results_list.scrollToBottom)
        view_menu.addAction(bottom_action)


    def showEvent(self, event):
        """Show the window maximized"""
        super().showEvent(event)
        self.showMaximized()

    def sort_results_newest_first(self):
        """Manually sort results by timestamp (newest first)"""
        if not self.log_data:
            return
            
        try:
            # Sort by timestamp
            self.log_data.sort(key=lambda x: self.parse_timestamp_for_sorting(x[0]), reverse=True)
            self.update_results_list()
            self.status_label.setText("Results sorted (newest first)")
        except Exception as e:
            self.status_label.setText("Sorting failed")

    def parse_timestamp_for_sorting(self, timestamp_str):
        """Helper method to parse timestamps for sorting"""
        try:
            return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
        except Exception:
            # Try alternative formats if the main one fails
            try:
                return datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
            except Exception:
                return datetime.min

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_Return, Qt.Key_Enter] and self.search_btn.isEnabled():
            self.start_search()
        elif event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_Up:  # Ctrl+Up: jump to first (oldest) item
                self.jump_to_first_item()
            elif event.key() == Qt.Key_Down:  # Ctrl+Down: jump to last (newest) item
                self.jump_to_last_item()
            elif event.key() == Qt.Key_Home:  # Ctrl+Home: scroll to top
                self.results_list.scrollToTop()
            elif event.key() == Qt.Key_End:  # Ctrl+End: scroll to bottom
                self.results_list.scrollToBottom()
        else:
            super().keyPressEvent(event)
    
    def jump_to_first_item(self):
        """Jump to the first (oldest) item in the list"""
        if self.results_list.count() > 0:
            self.results_list.setCurrentRow(0)
            self.results_list.scrollToItem(self.results_list.item(0))
    
    def jump_to_last_item(self):
        """Jump to the last (newest) item in the list"""
        if self.results_list.count() > 0:
            last_index = self.results_list.count() - 1
            self.results_list.setCurrentRow(last_index)
            self.results_list.scrollToItem(self.results_list.item(last_index))

    def sort_results_newest_first(self):
        """Manually sort results by timestamp (newest first)"""
        if not self.log_data:
            QMessageBox.information(self, "Info", "No results to sort")
            return
            
        try:
            # Create a copy with datetime objects for sorting
            sorted_data = []
            for timestamp, line, log_file in self.log_data:
                try:
                    dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
                    sorted_data.append((dt, timestamp, line, log_file))
                except Exception:
                    sorted_data.append((datetime.min, timestamp, line, log_file))
            
            # Sort by datetime (newest first)
            sorted_data.sort(key=lambda x: x[0], reverse=True)
            
            # Convert back to original format
            self.log_data = [(ts, line, log_file) for dt, ts, line, log_file in sorted_data]
            
            self.update_results_list()
            self.status_label.setText("Results sorted (newest first)")
            QMessageBox.information(self, "Success", "Results sorted successfully!")
            
        except Exception as e:
            self.status_label.setText("Sorting failed")
            QMessageBox.warning(self, "Sort Error", f"Could not sort results: {str(e)}")
    def start_search(self):
        if not self.account_input.text():
            QMessageBox.warning(self, "Error", "Account is required")
            return
            
        self.clear_results()
        self.search_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.status_label.setText("Searching...")
        
        # Get search parameters
        account = self.account_input.text()
        ticker = self.ticker_input.text().upper()
        side = self.side_input.text()
        search_terms = [
            self.search1_input.text(),
            self.search2_input.text(), 
            self.search3_input.text()
        ]
        search_archived = self.archived_checkbox.isChecked()
        search_actions_log = self.actions_log_checkbox.isChecked()
        
        # Debug output
        print(f"DEBUG: Account={account}, Ticker={ticker}, Side={side}")
        print(f"DEBUG: Search Terms={search_terms}")
        print(f"DEBUG: Archived={search_archived}, Actions={search_actions_log}")
        
        # Create and start worker
        self.worker = SSHWorker(account, ticker, side, search_terms, search_archived, search_actions_log)
        self.worker.finished.connect(self.on_search_finished)
        self.worker.error.connect(self.on_search_error)
        self.worker.progress.connect(self.on_search_progress)
        self.worker.start()    

    # def start_search(self):
    #     if not self.account_input.text():
    #         QMessageBox.warning(self, "Error", "Account is required")
    #         return
            
    #     self.clear_results()
    #     self.search_btn.setEnabled(False)
    #     self.stop_btn.setEnabled(True)
    #     self.progress_bar.setVisible(True)
    #     self.status_label.setText("Searching...")
        
    #     # Get search parameters
    #     account = self.account_input.text()
    #     ticker = self.ticker_input.text().upper()
    #     side = self.side_input.text()
    #     search_terms = [
    #         self.search1_input.text(),
    #         self.search2_input.text(), 
    #         self.search3_input.text()
    #     ]
    #     search_archived = self.archived_checkbox.isChecked()
    #     search_actions_log = self.actions_log_checkbox.isChecked()
        
    #     # Create and start worker
    #     self.worker = SSHWorker(account, ticker, side, search_terms, search_archived, search_actions_log)
    #     self.worker.finished.connect(self.on_search_finished)
    #     self.worker.error.connect(self.on_search_error)
    #     self.worker.progress.connect(self.on_search_progress)
    #     self.worker.start()
    
    def on_search_finished(self, results):
        self.log_data = results
        self.update_results_list()
        self.search_finished()
        
        if results:
            self.status_label.setText(f"Found {len(results)} results")
        else:
            self.status_label.setText("No results found")
    
    def on_search_error(self, error_msg):
        QMessageBox.critical(self, "Error", f"Search failed: {error_msg}")
        self.search_finished()
        self.status_label.setText("Search failed")
    
    def on_search_progress(self, message):
        self.status_label.setText(message)
        print(f"PROGRESS: {message}")
    
    def search_finished(self):
        self.search_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.progress_bar.setVisible(False)
    
    def stop_search(self):
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait()
        self.search_finished()
        self.status_label.setText("Search stopped")
    
    def update_results_list(self):
        self.results_list.clear()
        for timestamp, line, log_file in self.log_data:
            display_line = line

            
            item_text = f"[{log_file}] {timestamp}\n{display_line}"
            item = QListWidgetItem(item_text)
            
            # Color code based on log file type
            if 'actions.log' in log_file:
                item.setBackground(QColor(60, 50, 50))
            elif log_file.endswith('.log'):
                item.setBackground(QColor(50, 60, 50))
            else:
                item.setBackground(QColor(50, 50, 60))
                
            self.results_list.addItem(item)
    
    def show_log_details(self, item):
        index = self.results_list.row(item)
        if 0 <= index < len(self.log_data):
            timestamp, line, log_file = self.log_data[index]
            self.details_display.setPlainText(f"Source: {log_file}\nTimestamp: {timestamp}\n\n{line}")
    
    def clear_results(self):
        self.results_list.clear()
        self.details_display.clear()
        self.log_data = []
        self.status_label.setText("Results cleared")
    
    def filter_results(self):
        filter_text = self.filter_input.text().lower()
        self.results_list.clear()
        
        for timestamp, line, log_file in self.log_data:
            if not filter_text or filter_text in line.lower() or filter_text in log_file.lower():
                display_line = line
                
                item_text = f"[{log_file}] {timestamp}\n{display_line}"
                item = QListWidgetItem(item_text)
                
                if 'actions.log' in log_file:
                    item.setBackground(QColor(60, 50, 50))
                elif log_file.endswith('.log'):
                    item.setBackground(QColor(50, 60, 50))
                else:
                    item.setBackground(QColor(50, 50, 60))
                    
                self.results_list.addItem(item)


    def save_to_file(self):
        if not self.log_data:
            QMessageBox.warning(self, "Error", "No results to save")
            return
            
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Results", 
            os.path.expanduser(f"~/logs/{self.account_input.text()}_search_results.txt"),
            "Text Files (*.txt)"
        )
        
        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(f"Log Search Results - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Account: {self.account_input.text()}\n")
                    f.write(f"Ticker: {self.ticker_input.text()}\n")
                    f.write(f"Side: {self.side_input.text()}\n")
                    f.write(f"Search Terms: {self.search1_input.text()}, {self.search2_input.text()}, {self.search3_input.text()}\n")
                    f.write(f"Search Actions Log: {self.actions_log_checkbox.isChecked()}\n")
                    f.write("=" * 80 + "\n\n")
                    
                    for timestamp, line, log_file in self.log_data:
                        f.write(f"[{log_file}] {timestamp} - {line}\n")
                QMessageBox.information(self, "Success", f"Results saved to {file_path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Save failed: {str(e)}")
    
    def test_ssh_connection(self):
        try:
            self.status_label.setText("Testing SSH connection...")
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect('157.180.125.52', username='niels', password='flowsy', timeout=10)
            
            stdin, stdout, stderr = ssh.exec_command('pwd')
            pwd_result = stdout.read().decode().strip()
            
            stdin, stdout, stderr = ssh.exec_command('ls -la /home/niels/logs/')
            logs_list = stdout.read().decode()
            
            ssh.close()
            
            message = f"SSH Connection Successful!\nCurrent dir: {pwd_result}\nLogs directory contents:\n{logs_list[:200]}..."
            QMessageBox.information(self, "SSH Test", message)
            self.status_label.setText("SSH connection successful")
            
        except Exception as e:
            QMessageBox.critical(self, "SSH Test Failed", f"SSH connection failed: {str(e)}")
            self.status_label.setText("SSH connection failed")
    
    def closeEvent(self, event):
        self.stop_search()
        event.accept()

def main():
    app = QApplication(sys.argv)
    apply_dark_theme(app)
    window = LogViewerApp()
    window.showMaximized()  # Show maximized on startup
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()