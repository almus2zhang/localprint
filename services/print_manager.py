import os
import sqlite3
import logging
import subprocess
from datetime import datetime, date
from database import DatabaseManager

class PrintService:
    """打印服务 - 处理CUPS交互和队列管理"""
    
    # 内存缓存映射：用于将 CUPS 的 EPSON_xxx 作业ID 关联到真实的文档名与页数
    JOB_INFO_CACHE = {}

    def __init__(self, db_manager: DatabaseManager):
        self.db_manager = db_manager
    
    @classmethod
    def register_job(cls, job_id, filename, pages=1, filepath='', print_type='two-sided-long-edge'):
        """注册打印任务关联信息"""
        if not job_id:
            return
        cls.JOB_INFO_CACHE[str(job_id).strip()] = {
            'filename': filename,
            'pages': max(1, int(pages or 1)),
            'filepath': filepath,
            'print_type': print_type,
            'added_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'status': 'printing'
        }

    def get_print_queue(self):
        """获取打印队列 - 仅返回当前正在打印或排队等待的真实作业"""
        import re
        combined_queue = []
        seen_job_ids = set()

        try:
            # 1. 首先从CUPS获取当前实时打印作业 (真正正在打印或排队中的任务)
            try:
                cmd = ['lpstat', '-o']
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0 and result.stdout.strip():
                    for line in result.stdout.strip().split('\n'):
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.split()
                        if len(parts) >= 1:
                            job_id = parts[0]
                            seen_job_ids.add(job_id)

                            # 尝试从缓存中查找真实文件名与页数
                            cached = self.JOB_INFO_CACHE.get(job_id)
                            if cached:
                                filename = cached['filename']
                                pages = cached.get('pages', 1)
                                filepath = cached.get('filepath', '')
                                print_type = cached.get('print_type', 'two-sided-long-edge')
                                time_str = cached.get('added_time', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
                            else:
                                clean_id = re.sub(r'^[A-Za-z0-9_-]+-(\d+)$', r'打印任务 #\1', job_id)
                                filename = clean_id
                                pages = 1
                                filepath = ''
                                print_type = 'two-sided-long-edge'
                                time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                            clean_name = re.sub(r'^\d{8}[_]?\d{4,6}_', '', filename)
                            if clean_name.startswith('tmp_'):
                                ext = os.path.splitext(clean_name)[1]
                                clean_name = f"微信文档{ext}" if ext else "微信文档"

                            combined_queue.append({
                                'id': job_id,
                                'filename': clean_name,
                                'pages': pages,
                                'filepath': filepath,
                                'print_type': print_type,
                                'added_time': time_str,
                                'status': 'printing'
                            })
            except Exception as e:
                logging.error(f"获取CUPS打印队列异常: {str(e)}")

            # 2. 从数据库获取处于 pending 等待状态的任务（如果有）
            try:
                pending_db_jobs = self.db_manager.get_pending_print_queue()
                for rec in pending_db_jobs:
                    # rec: (id, filename, filepath, print_type, added_time, status)
                    rec_filename = re.sub(r'^\d{8}[_]?\d{4,6}_', '', rec[1])
                    if rec_filename.startswith('tmp_'):
                        ext = os.path.splitext(rec_filename)[1]
                        rec_filename = f"微信文档{ext}" if ext else "微信文档"
                    if not any(j['filename'] == rec_filename for j in combined_queue):
                        combined_queue.append({
                            'id': str(rec[0]),
                            'filename': rec_filename,
                            'pages': 1,
                            'filepath': rec[2],
                            'print_type': rec[3],
                            'added_time': str(rec[4])[:19] if rec[4] else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                            'status': 'pending'
                        })
            except Exception as e:
                logging.error(f"获取数据库等待队列异常: {e}")

            # 清理缓存中已不在 CUPS 队列里的过期任务
            for cached_id in list(self.JOB_INFO_CACHE.keys()):
                if cached_id not in seen_job_ids:
                    del self.JOB_INFO_CACHE[cached_id]

            return combined_queue
            
        except Exception as e:
            logging.error(f"获取打印队列时发生错误: {str(e)}")
            return []

    def cancel_all_print_jobs(self):
        """取消所有打印作业"""
        try:
            # 取消CUPS中的所有打印作业
            try:
                cmd = ['cancel', '-a', '-x']
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode != 0:
                    subprocess.run(['cancel', '-a'], capture_output=True, text=True)
                logging.info("已发送取消所有CUPS打印作业指令")
            except Exception as e:
                logging.error(f"取消CUPS打印作业时发生错误: {str(e)}")
            
            # 清空缓存
            self.JOB_INFO_CACHE.clear()

            # 更新数据库中的打印队列状态
            updated_count = self.db_manager.update_queue_status_bulk('cancelled', 'pending')
            
            return True, f"已取消所有打印作业"
            
        except Exception as e:
            error_msg = f"取消打印作业时发生错误: {str(e)}"
            logging.error(error_msg)
            return False, error_msg

    def cancel_print_job(self, job_id):
        """取消单个打印作业"""
        import re
        try:
            job_id_str = str(job_id).strip()
            cups_cancelled = False

            # 1. 尝试通过 CUPS 命令取消作业
            try:
                cmd = ['cancel', job_id_str]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0:
                    cups_cancelled = True
                    logging.info(f"已成功通过CUPS取消打印作业: {job_id_str}")
                else:
                    # 尝试提取结尾纯数字重试 (如 EPSON_L6260_Series-123 -> 123)
                    num_match = re.search(r'\d+$', job_id_str)
                    if num_match:
                        res2 = subprocess.run(['cancel', num_match.group()], capture_output=True, text=True)
                        if res2.returncode == 0:
                            cups_cancelled = True
                            logging.info(f"已通过数字ID取消CUPS作业: {num_match.group()}")
            except Exception as e:
                logging.error(f"执行 cancel 命令异常: {e}")

            # 2. 从内存缓存中删除
            if job_id_str in self.JOB_INFO_CACHE:
                del self.JOB_INFO_CACHE[job_id_str]

            # 3. 更新数据库 print_queue 中的对应记录状态
            updated_count = self.db_manager.update_queue_status_by_id_or_file('cancelled', job_id_str)

            # 4. 如果是历史记录ID (如 hist_12)
            if job_id_str.startswith('hist_'):
                hist_id = job_id_str.replace('hist_', '')
                try:
                    with self.db_manager.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("UPDATE print_history SET status = 'cancelled' WHERE id = ?", (hist_id,))
                        if cursor.rowcount > 0:
                            updated_count += cursor.rowcount
                            conn.commit()
                except Exception as e:
                    logging.error(f"更新历史记录状态失败: {e}")

            return True, f"已成功取消打印作业 {job_id_str}"
            
        except Exception as e:
            error_msg = f"取消打印作业时发生错误: {str(e)}"
            logging.error(error_msg)
            return False, error_msg

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
    
    def clear_file_history_by_date(self, target_date):
        actual_date = self._parse_display_date(target_date)
        if not actual_date:
             return False, "无法解析日期"
        date_str = actual_date.strftime('%Y-%m-%d')
        count = self.db_manager.clear_file_history_by_date(date_str)
        return True, f"已清除 {target_date} 的文件处理历史，共 {count} 条记录"

    def clear_print_history_by_date(self, target_date):
        actual_date = self._parse_display_date(target_date)
        if not actual_date:
             return False, "无法解析日期"
        date_str = actual_date.strftime('%Y-%m-%d')
        count = self.db_manager.clear_print_history_by_date(date_str)
        return True, f"已清除 {target_date} 的打印历史，共 {count} 条记录"
