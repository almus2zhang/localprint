#!/usr/bin/env python3
"""
文件监控与打印管理系统
功能：监控目录、自动转换文档为PDF（不打印）
支持日期折叠
"""

import os
import time
import logging
import sqlite3
from datetime import datetime, date
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import zipfile
import tarfile
import subprocess
from pathlib import Path
import threading
from collections import deque
import chardet
import shutil
import json
from flask import Flask, render_template, request, jsonify, send_file
from collections import defaultdict

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/var/log/file_monitor.log'),
        logging.StreamHandler()
    ]
)

# Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

class PrintManager:
    """打印管理器"""
    
    def __init__(self, db_path='print_history.db'):
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS print_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                print_time DATETIME NOT NULL,
                pages INTEGER DEFAULT 0,
                print_type TEXT DEFAULT 'two-sided-short-edge',
                status TEXT DEFAULT 'success',
                print_command TEXT,
                error_message TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS print_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                print_type TEXT DEFAULT 'two-sided-short-edge',
                added_time DATETIME NOT NULL,
                status TEXT DEFAULT 'pending'
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversion_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_file TEXT NOT NULL,
                pdf_file TEXT NOT NULL,
                convert_time DATETIME NOT NULL,
                status TEXT DEFAULT 'success',
                error_message TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS file_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_type TEXT NOT NULL,
                process_time DATETIME NOT NULL,
                status TEXT DEFAULT 'processed'
            )
        ''')
        conn.commit()
        conn.close()
    
    def add_print_record(self, filename, filepath, pages=0, print_type='two-sided-short-edge', 
                        status='success', print_command='', error_message=''):
        """添加打印记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO print_history 
            (filename, filepath, print_time, pages, print_type, status, print_command, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (filename, filepath, datetime.now(), pages, print_type, status, print_command, error_message))
        conn.commit()
        conn.close()
    
    def add_file_record(self, filename, filepath, file_type, status='processed'):
        """添加文件处理记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO file_history 
            (filename, filepath, file_type, process_time, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, filepath, file_type, datetime.now(), status))
        conn.commit()
        conn.close()
    
    def add_conversion_record(self, original_file, pdf_file, status='success', error_message=''):
        """添加转换记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversion_history 
            (original_file, pdf_file, convert_time, status, error_message)
            VALUES (?, ?, ?, ?, ?)
        ''', (original_file, pdf_file, datetime.now(), status, error_message))
        conn.commit()
        conn.close()
    
    def add_to_print_queue(self, filename, filepath, print_type='two-sided-short-edge'):
        """添加到打印队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO print_queue 
            (filename, filepath, print_type, added_time, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (filename, filepath, print_type, datetime.now(), 'pending'))
        queue_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return queue_id
    
    def get_print_history(self, limit=100):
        """获取打印历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM print_history 
            ORDER BY print_time DESC 
            LIMIT ?
        ''', (limit,))
        history = cursor.fetchall()
        conn.close()
        return history
    
    def get_file_history(self, limit=100):
        """获取文件处理历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM file_history 
            ORDER BY process_time DESC 
            LIMIT ?
        ''', (limit,))
        history = cursor.fetchall()
        conn.close()
        return history
    
    def get_conversion_history(self, limit=50):
        """获取转换历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM conversion_history 
            ORDER BY convert_time DESC 
            LIMIT ?
        ''', (limit,))
        history = cursor.fetchall()
        conn.close()
        return history
    
    def get_print_queue(self):
        """获取打印队列 - 从CUPS系统获取实时打印作业"""
        try:
            # 首先从CUPS获取当前打印作业
            cups_queue = []
            try:
                cmd = ['lpstat', '-o']
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().split('\n'):
                        if line.strip():
                            # 解析打印作业信息
                            parts = line.split()
                            if len(parts) >= 1:
                                job_id = parts[0]
                                # 获取作业详细信息
                                try:
                                    info_cmd = ['lpq', job_id]
                                    info_result = subprocess.run(info_cmd, capture_output=True, text=True, check=True)
                                    # 解析作业信息
                                    cups_queue.append({
                                        'id': job_id,
                                        'filename': job_id,  # 先用作业ID作为文件名
                                        'filepath': '',  # CUPS不提供文件路径
                                        'print_type': 'unknown',
                                        'added_time': datetime.now(),  # 无法获取确切时间
                                        'status': 'pending'
                                    })
                                except:
                                    # 如果无法获取详细信息，至少添加基本作业信息
                                    cups_queue.append({
                                        'id': job_id,
                                        'filename': job_id,
                                        'filepath': '',
                                        'print_type': 'unknown',
                                        'added_time': datetime.now(),
                                        'status': 'pending'
                                    })
                else:
                    logging.info("CUPS打印队列为空")
            except subprocess.CalledProcessError as e:
                if "No entries" in e.stderr or "no entries" in e.stderr:
                    logging.info("CUPS打印队列为空")
                else:
                    logging.error(f"获取CUPS打印队列时出错: {e.stderr}")
            except Exception as e:
                logging.error(f"获取CUPS打印队列时发生错误: {str(e)}")
            
            # 同时从数据库获取打印队列
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM print_queue 
                WHERE status = 'pending'
                ORDER BY added_time ASC
            ''')
            db_queue = cursor.fetchall()
            conn.close()
            
            # 合并两个队列，优先显示CUPS队列
            combined_queue = []
            
            # 添加CUPS队列
            for cups_job in cups_queue:
                combined_queue.append((
                    cups_job['id'],  # 使用CUPS作业ID作为ID
                    cups_job['filename'],
                    cups_job['filepath'],
                    cups_job['print_type'],
                    cups_job['added_time'],
                    cups_job['status']
                ))
            
            # 添加数据库队列，但要避免重复
            for db_job in db_queue:
                # 检查是否已经在CUPS队列中
                job_exists = any(job[1] == db_job[1] for job in combined_queue)  # 比较文件名
                if not job_exists:
                    combined_queue.append(db_job)
            
            logging.info(f"合并后的打印队列: {len(combined_queue)} 个作业")
            return combined_queue
            
        except Exception as e:
            logging.error(f"获取打印队列时发生错误: {str(e)}")
            return []
    
    def update_queue_status(self, queue_id, status):
        """更新队列状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE print_queue 
            SET status = ?
            WHERE id = ?
        ''', (status, queue_id))
        conn.commit()
        conn.close()
    
    def clear_file_history_by_date(self, target_date):
        """按日期清除文件处理历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 将日期字符串转换为实际的日期对象
        actual_date = self._parse_display_date(target_date)
        if not actual_date:
            conn.close()
            return False, "无法解析日期"
        
        # 格式化日期为字符串用于SQL查询
        date_str = actual_date.strftime('%Y-%m-%d')
        
        cursor.execute('''
            DELETE FROM file_history 
            WHERE date(process_time) = ?
        ''', (date_str,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return True, f"已清除 {target_date} 的文件处理历史，共 {deleted_count} 条记录"
    
    def clear_print_history_by_date(self, target_date):
        """按日期清除打印历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 将日期字符串转换为实际的日期对象
        actual_date = self._parse_display_date(target_date)
        if not actual_date:
            conn.close()
            return False, "无法解析日期"
        
        # 格式化日期为字符串用于SQL查询
        date_str = actual_date.strftime('%Y-%m-%d')
        
        cursor.execute('''
            DELETE FROM print_history 
            WHERE date(print_time) = ?
        ''', (date_str,))
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return True, f"已清除 {target_date} 的打印历史，共 {deleted_count} 条记录"
    
    def clear_all_conversion_history(self):
        """清除所有转换历史"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM conversion_history')
        count_before = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM conversion_history')
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return True, f"已清除所有转换历史，共 {deleted_count} 条记录"
    
    def clear_print_queue(self):
        """清空打印队列"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('SELECT COUNT(*) FROM print_queue WHERE status = "pending"')
        count_before = cursor.fetchone()[0]
        
        cursor.execute('DELETE FROM print_queue WHERE status = "pending"')
        
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        
        return True, f"已清空打印队列，共 {deleted_count} 条记录"
    
    def _parse_display_date(self, display_date):
        """将前端显示的日期字符串转换为实际的日期对象"""
        today = date.today()
        
        if display_date == "今天":
            return today
        elif display_date == "昨天":
            return today.replace(day=today.day - 1)
        else:
            # 尝试解析格式如 "2023年12月01日"
            try:
                # 移除中文字符并解析
                date_str = display_date.replace("年", "-").replace("月", "-").replace("日", "")
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                # 如果解析失败，返回None
                return None
# 在 PrintManager 类中添加以下方法
    def cancel_all_print_jobs(self):
        """取消所有打印作业"""
        try:
            cancelled_count = 0
            
            # 取消CUPS中的打印作业
            try:
                cmd = ['cancel', '-a']  # 取消所有作业
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                logging.info("已取消所有CUPS打印作业")
                cancelled_count += 1  # 即使不知道具体数量，也认为至少取消了一个
            except subprocess.CalledProcessError as e:
                if "No jobs" in e.stderr or "no jobs" in e.stderr:
                    logging.info("CUPS中没有打印作业可取消")
                else:
                    logging.error(f"取消CUPS打印作业时出错: {e.stderr}")
            except Exception as e:
                logging.error(f"取消CUPS打印作业时发生错误: {str(e)}")
            
            # 更新数据库中的打印队列状态
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE print_queue 
                SET status = 'cancelled'
                WHERE status = 'pending'
            ''')
            updated_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            return True, f"已取消所有打印作业，更新了 {updated_count} 条队列记录"
            
        except Exception as e:
            error_msg = f"取消打印作业时发生错误: {str(e)}"
            logging.error(error_msg)
            return False, error_msg

    def cancel_print_job(self, job_id):
        """取消单个打印作业"""
        try:
            # 尝试取消CUPS作业
            try:
                cmd = ['cancel', str(job_id)]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                logging.info(f"已取消CUPS打印作业: {job_id}")
            except subprocess.CalledProcessError as e:
                # 如果CUPS中没有该作业，可能是数据库中的作业
                if "No such" in e.stderr or "not found" in e.stderr:
                    logging.info(f"CUPS中没有作业 {job_id}，尝试更新数据库状态")
                else:
                    logging.error(f"取消CUPS作业 {job_id} 失败: {e.stderr}")
            except Exception as e:
                logging.error(f"取消CUPS作业 {job_id} 时发生错误: {str(e)}")
            
            # 更新数据库中的对应记录状态
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE print_queue 
                SET status = 'cancelled'
                WHERE id = ? OR filename LIKE ? OR filepath LIKE ?
            ''', (job_id, f'%{job_id}%', f'%{job_id}%'))
            updated_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            if updated_count > 0:
                return True, f"已取消打印作业 {job_id}"
            else:
                return False, f"未找到作业 {job_id}"
            
        except Exception as e:
            error_msg = f"取消打印作业时发生错误: {str(e)}"
            logging.error(error_msg)
            return False, error_msg
            
class EncodingHandler:
    """文件名编码处理类"""
    
    ENCODINGS = ['utf-8', 'gbk', 'gb2312', 'big5', 'shift_jis', 'euc-kr', 'iso-8859-1', 'cp437']
    
    @staticmethod
    def detect_encoding(data):
        """检测数据编码"""
        try:
            result = chardet.detect(data)
            encoding = result['encoding']
            confidence = result['confidence']
            if encoding and confidence > 0.6:
                return encoding
        except:
            pass
        return None
    
    @staticmethod
    def decode_filename(filename_bytes, archive_encoding=None):
        """解码文件名"""
        if archive_encoding:
            try:
                return filename_bytes.decode(archive_encoding)
            except (UnicodeDecodeError, LookupError):
                pass
        
        for encoding in EncodingHandler.ENCODINGS:
            try:
                return filename_bytes.decode(encoding)
            except (UnicodeDecodeError, LookupError):
                continue
        
        try:
            return filename_bytes.decode('utf-8', errors='replace')
        except:
            return filename_bytes.decode('ascii', errors='replace')
    
    @staticmethod
    def safe_filename(filename):
        """创建安全的文件名"""
        unsafe_chars = ['<', '>', ':', '"', '|', '?', '*', '\\', '/']
        safe_name = filename
        for char in unsafe_chars:
            safe_name = safe_name.replace(char, '_')
        safe_name = ''.join(char for char in safe_name if ord(char) >= 32)
        if len(safe_name) > 255:
            name, ext = os.path.splitext(safe_name)
            safe_name = name[:255-len(ext)] + ext
        return safe_name

class FileHandler(FileSystemEventHandler):
    def __init__(self, watch_directory, extract_to=None, print_manager=None):
        self.watch_directory = watch_directory
        self.extract_to = extract_to or os.path.join(watch_directory, "extracted")
        self.convert_to = os.path.join(watch_directory, "converted_pdfs")
        self.print_manager = print_manager
        self.processed_files = set()
        self.file_queue = deque()
        self.processing = False
        self.extracted_files_queue = deque()
        self.conversion_queue = deque()
        
        os.makedirs(self.extract_to, exist_ok=True)
        os.makedirs(self.convert_to, exist_ok=True)
        
        self.process_thread = threading.Thread(target=self._process_queue, daemon=True)
        self.process_thread.start()
        
        self.extracted_process_thread = threading.Thread(target=self._process_extracted_queue, daemon=True)
        self.extracted_process_thread.start()
        
        self.conversion_thread = threading.Thread(target=self._process_conversion_queue, daemon=True)
        self.conversion_thread.start()
        
        self.start_time = time.time()
        
        logging.info(f"文件监控已启动: {watch_directory}")

    def on_created(self, event):
        if not event.is_directory:
            file_path = event.src_path
            file_creation_time = self._get_file_creation_time(file_path)
            if file_creation_time >= self.start_time:
                logging.info(f"检测到新文件: {os.path.basename(file_path)}")
                self._add_to_queue(file_path)

    def on_moved(self, event):
        if not event.is_directory:
            file_path = event.dest_path
            file_creation_time = self._get_file_creation_time(file_path)
            if file_creation_time >= self.start_time:
                logging.info(f"检测到移动文件: {os.path.basename(file_path)}")
                self._add_to_queue(file_path)

    def _get_file_creation_time(self, file_path):
        try:
            return os.path.getctime(file_path)
        except OSError:
            return os.path.getmtime(file_path)

    def _add_to_queue(self, file_path):
        if file_path not in self.processed_files:
            self.file_queue.append(file_path)

    def _add_extracted_to_queue(self, file_path):
        if file_path not in self.processed_files:
            self.extracted_files_queue.append(file_path)
    
    def _add_to_conversion_queue(self, file_path):
        """添加到转换队列"""
        if file_path not in self.processed_files:
            self.conversion_queue.append(file_path)

    def _process_queue(self):
        while True:
            if self.file_queue and not self.processing:
                file_path = self.file_queue.popleft()
                self.processing = True
                try:
                    self._process_file(file_path)
                except Exception as e:
                    logging.error(f"处理文件 {file_path} 时出错: {str(e)}")
                finally:
                    self.processing = False
            time.sleep(0.5)

    def _process_extracted_queue(self):
        while True:
            if self.extracted_files_queue:
                file_path = self.extracted_files_queue.popleft()
                try:
                    if self._wait_for_file_stable(file_path):
                        self._process_single_file(file_path)
                except Exception as e:
                    logging.error(f"处理解压文件 {file_path} 时出错: {str(e)}")
            time.sleep(0.5)
    
    def _process_conversion_queue(self):
        """处理文档转换队列"""
        while True:
            if self.conversion_queue:
                file_path = self.conversion_queue.popleft()
                try:
                    if self._wait_for_file_stable(file_path):
                        logging.info(f"开始转换文档: {os.path.basename(file_path)}")
                        pdf_path = self._convert_to_pdf(file_path)
                        if pdf_path and os.path.exists(pdf_path):
                            # 记录文件处理历史
                            if self.print_manager:
                                self.print_manager.add_file_record(
                                    filename=os.path.basename(file_path),
                                    filepath=file_path,
                                    file_type='converted'
                                )
                            # 等待PDF文件稳定
                            time.sleep(2)
                            logging.info(f"转换后的PDF已准备好: {os.path.basename(pdf_path)}")
                            # self._print_file(pdf_path, 'two-sided-short-edge')
                except Exception as e:
                    logging.error(f"处理转换文件 {file_path} 时出错: {str(e)}")
            time.sleep(0.5)

    def _process_file(self, file_path):
        if not self._wait_for_file_stable(file_path):
            return
        
        try:
            self._process_single_file(file_path)
        except Exception as e:
            logging.error(f"处理文件 {file_path} 时出错: {str(e)}")
    
    def _process_single_file(self, file_path):
        """处理单个文件的核心逻辑"""
        # 忽略转换目录中的文件
        if self.convert_to in file_path:
            logging.info(f"忽略转换目录中的文件: {file_path}")
            return
        file_ext = Path(file_path).suffix.lower()
        
        if self._is_archive_file(file_path):
            logging.info(f"开始解压文件: {Path(file_path).name}")
            if self._extract_file(file_path):
                # 记录文件处理历史
                if self.print_manager:
                    self.print_manager.add_file_record(
                        filename=os.path.basename(file_path),
                        filepath=file_path,
                        file_type='extracted'
                    )
                self.processed_files.add(file_path)
                logging.info(f"文件解压完成: {Path(file_path).name}")
                
        elif self._is_printable_file(file_path):
            # PDF文件无需转换，且不自动打印
            logging.info(f"检测到PDF文件，跳过转换和打印: {Path(file_path).name}")
            if self.print_manager:
                self.print_manager.add_file_record(
                    filename=os.path.basename(file_path),
                    filepath=file_path,
                    file_type='detected_pdf'
                )
            self.processed_files.add(file_path)
                
        elif self._is_convertible_file(file_path):
            # 添加到转换队列
            logging.info(f"添加到转换队列: {Path(file_path).name}")
            self._add_to_conversion_queue(file_path)
            self.processed_files.add(file_path)

    def _wait_for_file_stable(self, file_path, max_attempts=30, wait_interval=1):
        file_size = -1
        for attempt in range(max_attempts):
            try:
                current_size = os.path.getsize(file_path)
                if current_size == file_size and current_size > 0:
                    return True
                file_size = current_size
                time.sleep(wait_interval)
            except OSError:
                time.sleep(wait_interval)
        return False

    def _is_archive_file(self, file_path):
        archive_extensions = {'.zip', '.tar', '.gz', '.bz2', '.7z', '.rar'}
        file_ext = Path(file_path).suffix.lower()
        if file_ext in archive_extensions:
            return True
        if file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
            return True
        return False

    def _is_printable_file(self, file_path):
        """检查是否为可直接打印的文件（仅PDF）"""
        # 现在只将PDF视为可直接打印的文件，图片也需要转换
        return Path(file_path).suffix.lower() == '.pdf'
    
    def _is_convertible_file(self, file_path):
        """检查是否为需要转换的文件（文档和图片）"""
        convertible_extensions = {'.doc', '.docx', '.xls', '.xlsx', '.txt', '.ppt', '.pptx', 
                                 '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}
        file_ext = Path(file_path).suffix.lower()
        return file_ext in convertible_extensions

    def _convert_to_pdf(self, file_path):
        """将文档和图片转换为PDF，并保存到专门的转换目录"""
        try:
            file_name = Path(file_path).stem
            # 使用专门的转换目录
            pdf_path = os.path.join(self.convert_to, f"{file_name}.pdf")
            
            # 检查是否已存在同名PDF
            if os.path.exists(pdf_path):
                # 检查时间戳，如果原文件更新则重新转换
                if os.path.getmtime(file_path) <= os.path.getmtime(pdf_path):
                    logging.info(f"PDF文件已存在，跳过转换: {pdf_path}")
                    return pdf_path
                else:
                    logging.info(f"原文件已更新，重新转换: {file_path}")
            
            file_ext = Path(file_path).suffix.lower()
            
            # 处理文档文件
            if file_ext in {'.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx'}:
                return self._convert_office_to_pdf(file_path, pdf_path)
                
            # 处理文本文件
            elif file_ext == '.txt':
                return self._convert_text_to_pdf(file_path, pdf_path)
                
            # 处理图片文件
            elif file_ext in {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif'}:
                return self._convert_image_to_pdf(file_path, pdf_path)
                
            else:
                logging.warning(f"不支持转换的文件类型: {file_ext}")
                return None
                
        except Exception as e:
            error_msg = f"转换过程中发生错误: {str(e)}"
            logging.error(error_msg)
            if self.print_manager:
                self.print_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path if 'pdf_path' in locals() else '',
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_office_to_pdf(self, file_path, pdf_path):
        """使用LibreOffice转换Office文档为PDF"""
        try:
            cmd = [
                'libreoffice', '--headless', '--convert-to', 'pdf',
                '--outdir', self.convert_to, file_path
            ]
            
            logging.info(f"执行Office转换命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                # 检查实际生成的PDF文件
                expected_pdf = pdf_path
                if not os.path.exists(expected_pdf):
                    # 查找转换目录中最近创建的PDF文件
                    pdf_files = [f for f in os.listdir(self.convert_to) 
                                if f.endswith('.pdf') and 
                                os.path.getctime(os.path.join(self.convert_to, f)) > time.time() - 10]
                    if pdf_files:
                        expected_pdf = os.path.join(self.convert_to, pdf_files[0])
                
                if os.path.exists(expected_pdf):
                    logging.info(f"Office文档转换成功: {file_path} -> {expected_pdf}")
                    if self.print_manager:
                        self.print_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=expected_pdf,
                            status='success'
                        )
                    return expected_pdf
                else:
                    error_msg = "转换成功但未找到生成的PDF文件"
                    logging.error(error_msg)
                    if self.print_manager:
                        self.print_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='failed',
                            error_message=error_msg
                        )
                    return None
            else:
                error_msg = f"Office转换失败: {result.stderr}"
                logging.error(error_msg)
                if self.print_manager:
                    self.print_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='failed',
                        error_message=error_msg
                    )
                return None
                
        except subprocess.CalledProcessError as e:
            error_msg = f"Office转换过程出错: {str(e)}"
            logging.error(error_msg)
            if self.print_manager:
                self.print_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_text_to_pdf(self, file_path, pdf_path):
        """使用enscript将文本文件转换为PDF"""
        try:
            # 首先检查文件编码
            try:
                with open(file_path, 'rb') as f:
                    raw_data = f.read()
                    encoding = chardet.detect(raw_data)['encoding'] or 'utf-8'
            except:
                encoding = 'utf-8'
            
            # 使用enscript转换
            cmd = f"enscript '{file_path}' --output=- --font=Courier10 --encoding={encoding} | ps2pdf - '{pdf_path}'"
            
            logging.info(f"执行文本转换命令: {cmd}")
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True)
            
            if result.returncode == 0 and os.path.exists(pdf_path):
                logging.info(f"文本文件转换成功: {file_path} -> {pdf_path}")
                if self.print_manager:
                    self.print_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='success'
                    )
                return pdf_path
            else:
                error_msg = f"文本转换失败: {result.stderr}"
                logging.error(error_msg)
                if self.print_manager:
                    self.print_manager.add_conversion_record(
                        original_file=file_path,
                        pdf_file=pdf_path,
                        status='failed',
                        error_message=error_msg
                    )
                return None
                
        except subprocess.CalledProcessError as e:
            error_msg = f"文本转换过程出错: {str(e)}"
            logging.error(error_msg)
            if self.print_manager:
                self.print_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _convert_image_to_pdf(self, file_path, pdf_path):
        """将图片文件转换为PDF"""
        try:
            # 方法1: 使用ImageMagick的convert命令
            try:
                cmd = ['convert', file_path, pdf_path]
                logging.info(f"执行图片转换命令: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                
                if result.returncode == 0 and os.path.exists(pdf_path):
                    logging.info(f"图片转换成功: {file_path} -> {pdf_path}")
                    if self.print_manager:
                        self.print_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except (subprocess.CalledProcessError, FileNotFoundError):
                # ImageMagick不可用，尝试其他方法
                pass
            
            # 方法2: 使用img2pdf Python库（如果可用）
            try:
                import img2pdf
                with open(file_path, "rb") as img_file:
                    img_data = img_file.read()
                
                with open(pdf_path, "wb") as pdf_file:
                    pdf_file.write(img2pdf.convert(img_data))
                
                if os.path.exists(pdf_path):
                    logging.info(f"图片转换成功 (img2pdf): {file_path} -> {pdf_path}")
                    if self.print_manager:
                        self.print_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except ImportError:
                logging.warning("img2pdf库未安装，无法使用该方法转换图片")
            except Exception as e:
                logging.error(f"img2pdf转换失败: {str(e)}")
            
            # 方法3: 使用Python的PIL库（如果可用）
            try:
                from PIL import Image
                image = Image.open(file_path)
                if image.mode == 'RGBA':
                    image = image.convert('RGB')
                image.save(pdf_path, "PDF", resolution=100.0)
                
                if os.path.exists(pdf_path):
                    logging.info(f"图片转换成功 (PIL): {file_path} -> {pdf_path}")
                    if self.print_manager:
                        self.print_manager.add_conversion_record(
                            original_file=file_path,
                            pdf_file=pdf_path,
                            status='success'
                        )
                    return pdf_path
            except ImportError:
                logging.warning("PIL库未安装，无法使用该方法转换图片")
            except Exception as e:
                logging.error(f"PIL转换失败: {str(e)}")
            
            error_msg = "所有图片转换方法都失败了"
            logging.error(error_msg)
            if self.print_manager:
                self.print_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None
            
        except Exception as e:
            error_msg = f"图片转换过程中发生错误: {str(e)}"
            logging.error(error_msg)
            if self.print_manager:
                self.print_manager.add_conversion_record(
                    original_file=file_path,
                    pdf_file=pdf_path,
                    status='failed',
                    error_message=error_msg
                )
            return None

    def _extract_zip_with_encoding(self, file_path, extract_dir):
        try:
            with zipfile.ZipFile(file_path, 'r') as zip_ref:
                file_list = zip_ref.namelist()
                encoding = None
                if file_list:
                    try:
                        sample_name = file_list[0].encode('cp437')
                        detected = EncodingHandler.detect_encoding(sample_name)
                        if detected:
                            encoding = detected
                    except:
                        pass
                
                for file_info in zip_ref.filelist:
                    try:
                        original_filename = file_info.filename
                        try:
                            decoded_name = original_filename.encode('cp437').decode('utf-8')
                        except:
                            try:
                                if encoding:
                                    decoded_name = original_filename.encode('cp437').decode(encoding)
                                else:
                                    decoded_name = original_filename.encode('cp437').decode('gbk')
                            except:
                                decoded_name = original_filename
                        
                        safe_name = EncodingHandler.safe_filename(decoded_name)
                        full_path = os.path.join(extract_dir, safe_name)
                        os.makedirs(os.path.dirname(full_path), exist_ok=True)
                        
                        if not file_info.is_dir():
                            with zip_ref.open(file_info) as source, open(full_path, 'wb') as target:
                                shutil.copyfileobj(source, target)
                            # 将解压的文件添加到处理队列
                            self._add_extracted_to_queue(full_path)
                    except Exception as e:
                        logging.error(f"解压ZIP中的文件 {file_info.filename} 时出错: {str(e)}")
                        continue
            return True
        except Exception as e:
            logging.error(f"解压ZIP文件 {file_path} 时出错: {str(e)}")
            return False

    def _extract_tar_with_encoding(self, file_path, extract_dir, mode='r'):
        try:
            with tarfile.open(file_path, mode) as tar_ref:
                members = tar_ref.getmembers()
                encoding = None
                if members:
                    try:
                        sample_name = members[0].name
                        detected = EncodingHandler.detect_encoding(sample_name.encode('utf-8', errors='replace'))
                        if detected:
                            encoding = detected
                    except:
                        pass
                
                for member in members:
                    try:
                        try:
                            if encoding:
                                decoded_name = member.name.encode('utf-8').decode(encoding)
                            else:
                                decoded_name = EncodingHandler.decode_filename(member.name.encode('utf-8'))
                        except:
                            decoded_name = member.name
                        
                        safe_name = EncodingHandler.safe_filename(decoded_name)
                        member.name = safe_name
                        tar_ref.extract(member, extract_dir)
                        
                        if not member.isdir():
                            full_path = os.path.join(extract_dir, member.name)
                            # 将解压的文件添加到处理队列
                            self._add_extracted_to_queue(full_path)
                    except Exception as e:
                        logging.error(f"解压TAR中的文件 {member.name} 时出错: {str(e)}")
                        continue
            return True
        except Exception as e:
            logging.error(f"解压TAR文件 {file_path} 时出错: {str(e)}")
            return False

    def _extract_file(self, file_path):
        try:
            file_ext = Path(file_path).suffix.lower()
            relative_path = os.path.relpath(file_path, self.watch_directory)
            safe_dir_name = relative_path.replace(os.path.sep, '_')
            extract_dir = os.path.join(self.extract_to, safe_dir_name)
            os.makedirs(extract_dir, exist_ok=True)
            
            if file_ext == '.zip' or file_path.endswith('.zip'):
                return self._extract_zip_with_encoding(file_path, extract_dir)
            elif file_ext == '.tar' or file_path.endswith('.tar.gz') or file_path.endswith('.tar.bz2'):
                mode = 'r'
                if file_path.endswith('.gz') or file_path.endswith('.tar.gz'):
                    mode = 'r:gz'
                elif file_path.endswith('.bz2') or file_path.endswith('.tar.bz2'):
                    mode = 'r:bz2'
                return self._extract_tar_with_encoding(file_path, extract_dir, mode)
            else:
                return False
        except Exception as e:
            logging.error(f"解压文件 {file_path} 时出错: {str(e)}")
            return False

    def _print_file(self, file_path, print_type='two-sided-short-edge'):
        """打印文件，支持多种打印方式"""
        try:
            file_ext = Path(file_path).suffix.lower()
            file_name = Path(file_path).name
            
            # 构建打印命令
            cmd = ['lp']
            
            # 根据打印类型设置参数
            if print_type == 'one-sided':
                cmd.extend(['-o', 'sides=one-sided'])
            elif print_type == 'two-sided-long-edge':
                cmd.extend(['-o', 'sides=two-sided-long-edge'])
            else:  # two-sided-short-edge
                cmd.extend(['-o', 'sides=two-sided-short-edge'])
            
            if file_ext in {'.jpg', '.jpeg', '.png'}:
                cmd.extend(['-o', 'fit-to-page'])
            
            cmd.append(file_path)
            
            logging.info(f"执行打印命令: {' '.join(cmd)}")
            
            # 执行打印
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            if result.returncode == 0:
                # 获取打印作业信息
                job_id = None
                if 'request id is' in result.stdout:
                    job_id = result.stdout.split('request id is ')[1].split()[0]
                
                # 记录打印历史
                if self.print_manager:
                    self.print_manager.add_print_record(
                        filename=file_name,
                        filepath=file_path,
                        pages=0,  # 可以后续添加页数检测功能
                        print_type=print_type,
                        status='success',
                        print_command=' '.join(cmd)
                    )
                
                logging.info(f"打印作业已创建: {job_id if job_id else 'Unknown'}")
                return True
            else:
                error_msg = result.stderr
                if self.print_manager:
                    self.print_manager.add_print_record(
                        filename=file_name,
                        filepath=file_path,
                        print_type=print_type,
                        status='failed',
                        print_command=' '.join(cmd),
                        error_message=error_msg
                    )
                logging.error(f"打印命令返回错误: {error_msg}")
                return False
                
        except subprocess.CalledProcessError as e:
            error_msg = e.stderr if e.stderr else str(e)
            if self.print_manager:
                self.print_manager.add_print_record(
                    filename=Path(file_path).name,
                    filepath=file_path,
                    print_type=print_type,
                    status='failed',
                    print_command=' '.join(cmd) if 'cmd' in locals() else '',
                    error_message=error_msg
                )
            logging.error(f"打印文件 {file_path} 时出错: {error_msg}")
            return False
        except Exception as e:
            if self.print_manager:
                self.print_manager.add_print_record(
                    filename=Path(file_path).name,
                    filepath=file_path,
                    print_type=print_type,
                    status='failed',
                    error_message=str(e)
                )
            logging.error(f"打印过程中发生错误: {str(e)}")
            return False

# 全局变量
print_manager = PrintManager()
file_handler = None
observer = None

def parse_datetime(datetime_str):
    """解析日期时间字符串，支持多种格式"""
    if isinstance(datetime_str, datetime):
        return datetime_str
    
    # 尝试不同的日期时间格式
    formats = [
        '%Y-%m-%d %H:%M:%S.%f',  # 包含毫秒
        '%Y-%m-%d %H:%M:%S',     # 不包含毫秒
        '%Y-%m-%d %H:%M',        # 不包含秒
        '%Y-%m-%d'               # 只包含日期
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(datetime_str, fmt)
        except ValueError:
            continue
    
    # 如果所有格式都失败，返回当前时间
    logging.warning(f"无法解析日期时间字符串: {datetime_str}，使用当前时间")
    return datetime.now()

def group_by_date(records, date_field_index):
    """按日期分组记录"""
    grouped = defaultdict(list)
    today = date.today()
    
    for record in records:
        # 获取日期字段
        date_value = record[date_field_index]
        record_datetime = parse_datetime(date_value)
        record_date = record_datetime.date()
        
        # 格式化日期显示
        delata = today - record_date
        # 格式化日期显示
        if delata.days == 0:
            date_display = "今天"
        elif delata.days == 1:
            date_display = "昨天"
        else:
            date_display = record_date.strftime('%Y年%m月%d日')
        
        grouped[date_display].append(record)
    
    return dict(grouped)

def group_files_by_date(files):
    """按修改日期分组文件"""
    grouped = defaultdict(list)
    today = date.today()
    
    for file in files:
        modified_date = file['modified'].date()
        
        delata = today - modified_date
        # 格式化日期显示
        if delata.days == 0:
            date_display = "今天"
        elif delata.days == 1:
            date_display = "昨天"
        else:
            date_display = modified_date.strftime('%Y年%m月%d日')
        
        grouped[date_display].append(file)
    
    return dict(grouped)

@app.route('/')
def index():
    """主页 - 显示文件列表和打印历史"""
    # 获取监控目录下的文件
    files = []
    if file_handler and os.path.exists(file_handler.watch_directory):
        for root, dirs, filenames in os.walk(file_handler.watch_directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                if (file_handler._is_printable_file(file_path) or 
                    file_handler._is_convertible_file(file_path)):
                    files.append({
                        'name': filename,
                        'path': file_path,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path)),
                        'type': 'printable' if file_handler._is_printable_file(file_path) else 'convertible'
                    })
    
    # 按修改日期倒序排序
    files.sort(key=lambda x: x['modified'], reverse=True)
    
    # 按修改日期分组文件
    files_by_date = group_files_by_date(files)
    
    # 获取所有PDF文件（可打印列表）
    pdf_files = []
    if file_handler and os.path.exists(file_handler.watch_directory):
        for root, dirs, filenames in os.walk(file_handler.watch_directory):
            for filename in filenames:
                file_path = os.path.join(root, filename)
                # 只收集PDF文件
                if file_handler._is_printable_file(file_path):
                    pdf_files.append({
                        'name': filename,
                        'path': file_path,
                        'size': os.path.getsize(file_path),
                        'modified': datetime.fromtimestamp(os.path.getmtime(file_path)),
                        'type': 'printable'
                    })
    
    # 按修改日期倒序排序
    pdf_files.sort(key=lambda x: x['modified'], reverse=True)
    
    # 按修改日期分组PDF文件
    pdf_files_by_date = group_files_by_date(pdf_files)
    
    # 获取打印历史并按日期分组
    history = print_manager.get_print_history(100)
    history_by_date = group_by_date(history, 3)  # 第3个字段是print_time
    
    # 获取转换历史
    conversion_history = print_manager.get_conversion_history(30)
    
    return render_template('index.html', 
                         files_by_date=files_by_date,
                         history_by_date=history_by_date,
                         printable_files_by_date=pdf_files_by_date,
                         conversion_history=conversion_history)

@app.route('/api/print', methods=['POST'])
def api_print():
    """API接口 - 打印文件"""
    data = request.json
    file_path = data.get('file_path')
    print_type = data.get('print_type', 'two-sided-short-edge')
    
    if not file_path or not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在'})
    
    # 检查文件类型
    file_ext = Path(file_path).suffix.lower()
    
    if file_ext in {'.doc', '.docx', '.xls', '.xlsx', '.txt', '.ppt', '.pptx'}:
        # 需要先转换
        pdf_path = file_handler._convert_to_pdf(file_path)
        if pdf_path and os.path.exists(pdf_path):
            file_handler.processed_files.add(pdf_path)
            file_path = pdf_path
        else:
            return jsonify({'success': False, 'message': '文件转换失败'})
    
    # 添加到打印队列
    queue_id = print_manager.add_to_print_queue(
        filename=os.path.basename(file_path),
        filepath=file_path,
        print_type=print_type
    )
    
    # 在后台线程中处理打印
    def print_in_background():
        success = file_handler._print_file(file_path, print_type)
        print_manager.update_queue_status(queue_id, 'completed' if success else 'failed')
    
    threading.Thread(target=print_in_background, daemon=True).start()
    
    return jsonify({'success': True, 'message': '打印任务已提交'})

@app.route('/api/print_multiple', methods=['POST'])
def api_print_multiple():
    """API接口 - 批量打印文件"""
    data = request.json
    file_paths = data.get('file_paths', [])
    print_type = data.get('print_type', 'two-sided-short-edge')
    
    if not file_paths:
        return jsonify({'success': False, 'message': '未选择文件'})
    
    success_count = 0
    failed_files = []
    
    for file_path in file_paths:
        if not os.path.exists(file_path):
            failed_files.append(f"{os.path.basename(file_path)}: 文件不存在")
            continue
        
        # 检查文件类型
        file_ext = Path(file_path).suffix.lower()
        original_path = file_path
        
        if file_ext in {'.doc', '.docx', '.xls', '.xlsx', '.txt', '.ppt', '.pptx'}:
            # 需要先转换
            pdf_path = file_handler._convert_to_pdf(file_path)
            if pdf_path and os.path.exists(pdf_path):
                file_handler.processed_files.add(pdf_path)
                file_path = pdf_path
            else:
                failed_files.append(f"{os.path.basename(original_path)}: 文件转换失败")
                continue
        
        # 添加到打印队列
        queue_id = print_manager.add_to_print_queue(
            filename=os.path.basename(file_path),
            filepath=file_path,
            print_type=print_type
        )
        
        # 在后台线程中处理打印
        def print_in_background(file_path=file_path, queue_id=queue_id):
            success = file_handler._print_file(file_path, print_type)
            print_manager.update_queue_status(queue_id, 'completed' if success else 'failed')
        
        threading.Thread(target=print_in_background, daemon=True).start()
        success_count += 1
    
    message = f"成功提交 {success_count} 个打印任务"
    if failed_files:
        message += f"，失败 {len(failed_files)} 个文件: {', '.join(failed_files)}"
    
    return jsonify({
        'success': True, 
        'message': message,
        'success_count': success_count,
        'failed_count': len(failed_files)
    })
    
@app.route('/api/clean_converted', methods=['POST'])
def api_clean_converted():
    """API接口 - 清理转换目录中的旧文件"""
    try:
        data = request.json
        # 默认保留最近7天的文件
        days = data.get('days', 7) if data else 7
        
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        deleted_count = 0
        
        if file_handler and os.path.exists(file_handler.convert_to):
            for filename in os.listdir(file_handler.convert_to):
                file_path = os.path.join(file_handler.convert_to, filename)
                if os.path.isfile(file_path):
                    # 检查文件创建时间
                    if os.path.getctime(file_path) < cutoff_time:
                        os.remove(file_path)
                        deleted_count += 1
                        logging.info(f"删除旧转换文件: {filename}")
        
        return jsonify({
            'success': True, 
            'message': f'已清理 {deleted_count} 个旧转换文件'
        })
    except Exception as e:
        error_msg = f"清理转换目录时出错: {str(e)}"
        logging.error(error_msg)
        return jsonify({'success': False, 'message': error_msg})
        
@app.route('/api/reprint', methods=['POST'])
def api_reprint():
    """API接口 - 重新打印文件"""
    data = request.json
    file_path = data.get('file_path')
    print_type = data.get('print_type', 'two-sided-short-edge')
    
    if not file_path:
        return jsonify({'success': False, 'message': '文件路径不能为空'})
    
    # 检查文件是否存在
    if not os.path.exists(file_path):
        return jsonify({'success': False, 'message': '文件不存在，无法重新打印'})
    
    # 添加到打印队列
    queue_id = print_manager.add_to_print_queue(
        filename=os.path.basename(file_path),
        filepath=file_path,
        print_type=print_type
    )
    
    # 在后台线程中处理打印
    def print_in_background():
        success = file_handler._print_file(file_path, print_type)
        print_manager.update_queue_status(queue_id, 'completed' if success else 'failed')
    
    threading.Thread(target=print_in_background, daemon=True).start()
    
    return jsonify({'success': True, 'message': '重新打印任务已提交'})

@app.route('/api/history')
def api_history():
    """API接口 - 获取打印历史"""
    history = print_manager.get_print_history(100)
    result = []
    for item in history:
        result.append({
            'id': item[0],
            'filename': item[1],
            'filepath': item[2],
            'print_time': item[3],
            'pages': item[4],
            'print_type': item[5],
            'status': item[6],
            'error_message': item[8] or ''
        })
    return jsonify(result)

@app.route('/api/file_history')
def api_file_history():
    """API接口 - 获取文件处理历史"""
    history = print_manager.get_file_history(100)
    result = []
    for item in history:
        result.append({
            'id': item[0],
            'filename': item[1],
            'filepath': item[2],
            'file_type': item[3],
            'process_time': item[4],
            'status': item[5]
        })
    return jsonify(result)

@app.route('/api/conversion_history')
def api_conversion_history():
    """API接口 - 获取转换历史"""
    history = print_manager.get_conversion_history(50)
    result = []
    for item in history:
        result.append({
            'id': item[0],
            'original_file': item[1],
            'pdf_file': item[2],
            'convert_time': item[3],
            'status': item[4],
            'error_message': item[5] or ''
        })
    return jsonify(result)

@app.route('/api/queue')
def api_queue():
    """API接口 - 获取打印队列"""
    queue = print_manager.get_print_queue()
    result = []
    for item in queue:
        result.append({
            'id': item[0],
            'filename': item[1],
            'filepath': item[2],
            'print_type': item[3],
            'added_time': item[4],
            'status': item[5]
        })
    return jsonify(result)

# 新增的清除历史记录API
@app.route('/api/clear_file_history', methods=['POST'])
def api_clear_file_history():
    """API接口 - 清除文件处理历史"""
    data = request.json
    target_date = data.get('date')
    
    if not target_date:
        return jsonify({'success': False, 'message': '日期参数不能为空'})
    
    success, message = print_manager.clear_file_history_by_date(target_date)
    return jsonify({'success': success, 'message': message})

@app.route('/api/clear_print_history', methods=['POST'])
def api_clear_print_history():
    """API接口 - 清除打印历史"""
    data = request.json
    target_date = data.get('date')
    
    if not target_date:
        return jsonify({'success': False, 'message': '日期参数不能为空'})
    
    success, message = print_manager.clear_print_history_by_date(target_date)
    return jsonify({'success': success, 'message': message})

@app.route('/api/clear_conversion_history', methods=['POST'])
def api_clear_conversion_history():
    """API接口 - 清除转换历史"""
    success, message = print_manager.clear_all_conversion_history()
    return jsonify({'success': success, 'message': message})

@app.route('/api/clear_print_queue', methods=['POST'])
def api_clear_print_queue():
    """API接口 - 清空打印队列"""
    success, message = print_manager.clear_print_queue()
    return jsonify({'success': success, 'message': message})

# 在已有的 API 路由后面添加以下路由
@app.route('/api/cancel_all_prints', methods=['POST'])
def api_cancel_all_prints():
    """API接口 - 取消所有打印作业"""
    success, message = print_manager.cancel_all_print_jobs()
    return jsonify({'success': success, 'message': message})

@app.route('/api/cancel_print/<int:job_id>', methods=['POST'])
def api_cancel_print(job_id):
    """API接口 - 取消单个打印作业"""
    success, message = print_manager.cancel_print_job(job_id)
    return jsonify({'success': success, 'message': message})

@app.route('/api/debug/queue')
def api_debug_queue():
    """调试接口 - 获取详细的队列信息"""
    # 获取CUPS队列
    cups_info = ""
    try:
        cmd = ['lpstat', '-o']
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        cups_info = result.stdout if result.returncode == 0 else result.stderr
    except Exception as e:
        cups_info = str(e)
    
    # 获取数据库队列
    conn = sqlite3.connect(print_manager.db_path)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM print_queue')
    db_queue = cursor.fetchall()
    conn.close()
    
    return jsonify({
        'cups_queue': cups_info,
        'database_queue': db_queue,
        'database_path': print_manager.db_path
    })
    
def start_file_monitor(watch_directory, extract_to=None):
    """启动文件监控"""
    global file_handler, observer
    
    file_handler = FileHandler(watch_directory, extract_to, print_manager)
    observer = Observer()
    observer.schedule(file_handler, watch_directory, recursive=True)
    observer.start()
    
    logging.info(f"文件监控已启动: {watch_directory}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='文件监控与打印管理系统')
    parser.add_argument('directory', help='要监控的目录路径')
    parser.add_argument('--extract-to', help='解压文件的目标目录', default=None)
    parser.add_argument('--port', type=int, default=5000, help='Web服务端口')
    
    args = parser.parse_args()
    
    watch_directory = os.path.abspath(args.directory)
    
    if not os.path.exists(watch_directory):
        logging.error(f"监控目录不存在: {watch_directory}")
        return
    # 检查必要的依赖
    try:
        import chardet
    except ImportError:
        logging.error("chardet库未安装，请先安装: pip3 install chardet")
        exit(1)
    
    try:
        from flask import Flask
    except ImportError:
        logging.error("Flask库未安装，请先安装: pip3 install flask")
        exit(1)
    
    # 检查LibreOffice是否安装
    try:
        subprocess.run(['libreoffice', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.error("LibreOffice未安装，请先安装: sudo apt install libreoffice")
        exit(1)
    
    # 检查enscript是否安装（用于文本文件转换）
    try:
        subprocess.run(['enscript', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.warning("enscript未安装，文本文件转换功能将不可用。安装: sudo apt install enscript")
    
    # 检查图片转换工具
    image_tools_available = False
    
    # 检查ImageMagick
    try:
        subprocess.run(['convert', '--version'], capture_output=True, check=True)
        image_tools_available = True
        logging.info("ImageMagick已安装，可用于图片转换")
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.warning("ImageMagick未安装，图片转换将使用Python库")
    
    # 检查img2pdf
    try:
        import img2pdf
        image_tools_available = True
        logging.info("img2pdf已安装，可用于图片转换")
    except ImportError:
        logging.warning("img2pdf未安装，尝试安装: pip3 install img2pdf")
    
    # 检查PIL/Pillow
    try:
        from PIL import Image
        image_tools_available = True
        logging.info("PIL/Pillow已安装，可用于图片转换")
    except ImportError:
        logging.warning("PIL/Pillow未安装，尝试安装: pip3 install Pillow")
    
    if not image_tools_available:
        logging.error("没有可用的图片转换工具，图片转换功能将不可用")
        logging.error("请至少安装以下之一:")
        logging.error("1. ImageMagick: sudo apt install imagemagick")
        logging.error("2. img2pdf: pip3 install img2pdf")
        logging.error("3. Pillow: pip3 install Pillow")
        exit(1)
    # 启动文件监控
    start_file_monitor(watch_directory, args.extract_to)
    
    # 启动Flask应用
    logging.info(f"启动Web服务，端口: {args.port}")
    app.run(host='0.0.0.0', port=args.port, debug=False)

if __name__ == "__main__":
    # 检查必要的依赖
    
    try:
        import chardet
    except ImportError:
        logging.error("chardet库未安装，请先安装: pip3 install chardet")
        exit(1)
    
    try:
        from flask import Flask
    except ImportError:
        logging.error("Flask库未安装，请先安装: pip3 install flask")
        exit(1)
    
    # 检查LibreOffice是否安装
    try:
        subprocess.run(['libreoffice', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.error("LibreOffice未安装，请先安装: sudo apt install libreoffice")
        exit(1)
    
    # 检查enscript是否安装（用于文本文件转换）
    try:
        subprocess.run(['enscript', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logging.warning("enscript未安装，文本文件转换功能将不可用。安装: sudo apt install enscript")
    
    main()