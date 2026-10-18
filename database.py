import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

class DatabaseManager:
    """数据库管理类"""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._init_database()
    
    def get_connection(self):
        """获取数据库连接"""
        return sqlite3.connect(self.db_path)

    def _init_database(self):
        """初始化数据库"""
        with self.get_connection() as conn:
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
    
    def add_print_record(self, filename, filepath, pages=0, print_type='two-sided-short-edge', 
                        status='success', print_command='', error_message=''):
        """添加打印记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO print_history 
                (filename, filepath, print_time, pages, print_type, status, print_command, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (filename, filepath, datetime.now(), pages, print_type, status, print_command, error_message))
            conn.commit()
    
    def add_file_record(self, filename, filepath, file_type, status='processed'):
        """添加文件处理记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO file_history 
                (filename, filepath, file_type, process_time, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (filename, filepath, file_type, datetime.now(), status))
            conn.commit()
    
    def add_conversion_record(self, original_file, pdf_file, status='success', error_message=''):
        """添加转换记录"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO conversion_history 
                (original_file, pdf_file, convert_time, status, error_message)
                VALUES (?, ?, ?, ?, ?)
            ''', (original_file, pdf_file, datetime.now(), status, error_message))
            conn.commit()
    
    def add_to_print_queue(self, filename, filepath, print_type='two-sided-short-edge'):
        """添加到打印队列"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO print_queue 
                (filename, filepath, print_type, added_time, status)
                VALUES (?, ?, ?, ?, ?)
            ''', (filename, filepath, print_type, datetime.now(), 'pending'))
            queue_id = cursor.lastrowid
            conn.commit()
            return queue_id
    
    def get_print_history(self, limit=100):
        """获取打印历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM print_history 
                ORDER BY print_time DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
    
    def get_file_history(self, limit=100):
        """获取文件处理历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM file_history 
                ORDER BY process_time DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()
            
    def get_conversion_history(self, limit=50):
        """获取转换历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM conversion_history 
                ORDER BY convert_time DESC 
                LIMIT ?
            ''', (limit,))
            return cursor.fetchall()

    def get_pending_print_queue(self):
        """获取等待中的打印队列"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT * FROM print_queue 
                WHERE status = 'pending'
                ORDER BY added_time ASC
            ''')
            return cursor.fetchall()

    def update_queue_status(self, queue_id, status):
        """更新队列状态"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE print_queue 
                SET status = ?
                WHERE id = ?
            ''', (status, queue_id))
            conn.commit()

    def clear_file_history_by_date(self, date_str):
        """按日期清除文件处理历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM file_history 
                WHERE date(process_time) = ?
            ''', (date_str,))
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count

    def clear_print_history_by_date(self, date_str):
        """按日期清除打印历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM print_history 
                WHERE date(print_time) = ?
            ''', (date_str,))
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count

    def clear_all_conversion_history(self):
        """清除所有转换历史"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM conversion_history')
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count

    def clear_pending_print_queue(self):
        """清空打印队列"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM print_queue WHERE status = "pending"')
            deleted_count = cursor.rowcount
            conn.commit()
            return deleted_count

    def update_queue_status_bulk(self, status, where_status):
        """批量更新队列状态"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f'''
                UPDATE print_queue 
                SET status = ?
                WHERE status = ?
            ''', (status, where_status))
            updated_count = cursor.rowcount
            conn.commit()
            return updated_count

    def update_queue_status_by_id_or_file(self, status, value):
        """通过ID或文件名更新队列状态"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE print_queue 
                SET status = ?
                WHERE id = ? OR filename LIKE ? OR filepath LIKE ?
            ''', (status, value, f'%{value}%', f'%{value}%'))
            updated_count = cursor.rowcount
            conn.commit()
            return updated_count
