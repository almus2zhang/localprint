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
        """获取打印队列 - 返回包含真实文档名与页数的结构"""
        import re
        combined_queue = []
        seen_job_ids = set()

        try:
            # 1. 首先从CUPS获取当前实时打印作业
            cups_jobs = []
            try:
                cmd = ['lpstat', '-o']
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                
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
                                # 尝试从数据库历史中找最近匹配
                                recent = self.db_manager.get_print_history(1)
                                if recent and len(recent) > 0:
                                    filename = recent[0][1]
                                    pages = recent[0][4] or 1
                                    filepath = recent[0][2]
                                    print_type = recent[0][5]
                                    time_str = str(recent[0][3])
                                else:
                                    # 降级：去除打印机前缀，如 EPSON_L6260_Series-123 -> 任务#123
                                    clean_id = re.sub(r'^[A-Za-z0-9_-]+-(\d+)$', r'打印任务 #\1', job_id)
                                    filename = clean_id
                                    pages = 1
                                    filepath = ''
                                    print_type = 'two-sided-long-edge'
                                    time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                            cups_jobs.append({
                                'id': job_id,
                                'filename': filename,
                                'pages': pages,
                                'filepath': filepath,
                                'print_type': print_type,
                                'added_time': time_str,
                                'status': 'printing'
                            })
            except subprocess.CalledProcessError as e:
                if "No entries" not in e.stderr and "no entries" not in e.stderr:
                    logging.error(f"获取CUPS打印队列时出错: {e.stderr}")
            except Exception as e:
                logging.error(f"获取CUPS打印队列异常: {str(e)}")

            combined_queue.extend(cups_jobs)

            # 2. 如果正在打印的作业较少，从数据库补充最近打印记录（让用户知道刚打完的任务及页数）
            recent_history = self.db_manager.get_print_history(5)
            for rec in recent_history:
                # rec: (id, filename, filepath, print_time, pages, print_type, status, print_command, error_message)
                rec_id = f"hist_{rec[0]}"
                rec_filename = re.sub(r'^\d{8}[_]?\d{4,6}_', '', rec[1])
                rec_pages = rec[4] if rec[4] and rec[4] > 0 else 1
                rec_status = rec[6] or 'completed'
                # 避免与当前 CUPS 里的重复
                if not any(j['filename'] == rec_filename and j['status'] == 'printing' for j in combined_queue):
                    combined_queue.append({
                        'id': rec_id,
                        'filename': rec_filename,
                        'pages': rec_pages,
                        'filepath': rec[2],
                        'print_type': rec[5],
                        'added_time': str(rec[3])[:19] if rec[3] else datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                        'status': 'completed' if rec_status == 'success' else rec_status
                    })

            return combined_queue[:6]
            
        except Exception as e:
            logging.error(f"获取打印队列时发生错误: {str(e)}")
            return []

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
            updated_count = self.db_manager.update_queue_status_bulk('cancelled', 'pending')
            
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
            updated_count = self.db_manager.update_queue_status_by_id_or_file('cancelled', job_id)
            
            if updated_count > 0:
                return True, f"已取消打印作业 {job_id}"
            else:
                return False, f"未找到作业 {job_id}"
            
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
